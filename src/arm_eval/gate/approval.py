"""审批：一个不比它该有的更聪明的实现。

四条性质，缺一条这套东西就不成立：
1. 签名绑定提案 —— 批准了 A，不能拿去跑 B（proposal_hash 进 HMAC）。
2. 单次使用 —— 重放同一条 token 无效。
3. 有期限 —— TTL 到了自动作废，默认拒绝。
4. 审批只能放行 REQUIRE_APPROVAL —— 它碰不到 BLOCK，那是另一层的事。

密钥默认每个进程随机生成。设了 ARM_EVAL_APPROVAL_SECRET 才用环境变量。
不设就随机，比给一个写死的默认值安全 —— 写死的密钥等于没有签名。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field

SECRET_ENV = "ARM_EVAL_APPROVAL_SECRET"


def proposal_hash(proposal: dict) -> str:
    """提案的规范哈希。字段顺序、空白都不能影响它。"""
    blob = json.dumps(proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    episode_id: str
    proposal_digest: str
    policy_hash: str
    reason_codes: tuple[str, ...]
    evidence: dict
    created_at: float
    expires_at: float

    def summary(self) -> dict:
        """给人看的审批材料。3 秒内能判的那种。"""
        return {
            "request_id": self.request_id,
            "episode": self.episode_id,
            "reasons": list(self.reason_codes),
            "clearances": self.evidence.get("clearances"),
            "target": self.evidence.get("target"),
            "action": self.evidence.get("action"),
            "confidence": self.evidence.get("confidence"),
            "ttl_s": round(self.expires_at - self.created_at, 1),
        }


@dataclass(frozen=True)
class ApprovalToken:
    token: str
    request_id: str
    proposal_digest: str
    operator: str
    issued_at: float
    expires_at: float
    nonce: str


@dataclass
class ApprovalOutcome:
    ok: bool
    reason: str                     # "GRANTED" / "APPROVAL_INVALID" / ...
    token: ApprovalToken | None = None
    request: ApprovalRequest | None = None


class ApprovalStore:
    def __init__(self, secret: bytes | None = None, ttl_s: float = 120.0,
                 now=time.time, nonce_factory=None) -> None:
        env_secret = os.environ.get(SECRET_ENV)
        if secret is not None:
            self.secret, self.ephemeral = secret, False
        elif env_secret:
            self.secret, self.ephemeral = env_secret.encode("utf-8"), False
        else:
            self.secret, self.ephemeral = secrets.token_bytes(32), True
        self.ttl_s = float(ttl_s)
        self._now = now
        self._nonce = nonce_factory or (lambda: secrets.token_hex(8))
        self._requests: dict[str, ApprovalRequest] = {}
        self._consumed: set[str] = set()
        self.audit: list[dict] = []

    # --- 申请 ---
    def request(self, proposal: dict, verdict, episode_id: str = "") -> ApprovalRequest:
        now = self._now()
        req = ApprovalRequest(
            request_id=f"req-{self._nonce()[:12]}",
            episode_id=episode_id,
            proposal_digest=proposal_hash(proposal),
            policy_hash=getattr(verdict, "policy_hash", ""),
            reason_codes=tuple(getattr(verdict, "reason_codes", ())),
            evidence=dict(getattr(verdict, "evidence", {})),
            created_at=now,
            expires_at=now + self.ttl_s,
        )
        self._requests[req.request_id] = req
        self.audit.append({"event": "approval_requested", "request_id": req.request_id,
                           "ts": now, "episode": episode_id,
                           "reasons": list(req.reason_codes)})
        return req

    # --- 批准 ---
    def grant(self, req: ApprovalRequest, operator: str = "operator") -> ApprovalToken:
        nonce = self._nonce()
        sig = self._sign(req, nonce)
        token = ApprovalToken(token=sig, request_id=req.request_id,
                              proposal_digest=req.proposal_digest, operator=operator,
                              issued_at=self._now(), expires_at=req.expires_at, nonce=nonce)
        self.audit.append({"event": "approval_granted", "request_id": req.request_id,
                           "operator": operator, "ts": token.issued_at})
        return token

    # --- 核销 ---
    def verify(self, token: ApprovalToken, req: ApprovalRequest,
               current_proposal: dict | None = None) -> tuple[bool, str]:
        now = self._now()

        if token.request_id != req.request_id:
            return False, "APPROVAL_INVALID"
        if not hmac.compare_digest(token.token, self._sign(req, token.nonce)):
            return False, "APPROVAL_INVALID"
        if now > token.expires_at:
            return False, "APPROVAL_EXPIRED"
        if current_proposal is not None and proposal_hash(current_proposal) != req.proposal_digest:
            return False, "APPROVAL_MISMATCH"
        if req.request_id in self._consumed:
            return False, "APPROVAL_REPLAY"

        self._consumed.add(req.request_id)
        self.audit.append({"event": "approval_consumed", "request_id": req.request_id,
                           "operator": token.operator, "ts": now})
        return True, "GRANTED"

    # --- 一步到位：申请 -> 问人 -> 签发 -> 核销 ---
    def ask(self, proposal: dict, verdict, operator=None, episode_id: str = "") -> ApprovalOutcome:
        req = self.request(proposal, verdict, episode_id=episode_id)
        if operator is None:
            self.audit.append({"event": "approval_denied", "request_id": req.request_id,
                               "operator": "<none>", "ts": self._now()})
            return ApprovalOutcome(False, "APPROVAL_DENIED", None, req)
        if not operator(req):
            self.audit.append({"event": "approval_denied", "request_id": req.request_id,
                               "operator": "operator", "ts": self._now()})
            return ApprovalOutcome(False, "APPROVAL_DENIED", None, req)
        token = self.grant(req)
        ok, why = self.verify(token, req, current_proposal=proposal)
        return ApprovalOutcome(ok, why, token, req)

    def _sign(self, req: ApprovalRequest, nonce: str) -> str:
        msg = f"{req.request_id}|{req.proposal_digest}|{nonce}|{req.expires_at:.3f}"
        return hmac.new(self.secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
