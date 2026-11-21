"""审计日志：一条哈希链，改一个字就断。

设计目标不是"记得多"，是"事后能还原每一个判断，并且改不动"。

每条记录：
    seq, ts, episode_id, event, actor, payload, prev_hash, hash, schema_version

hash = sha256(规范化 JSON(除 hash 外全部字段))
prev_hash 指向上一条的 hash，所以改中间任何一条，后面全断。

关于完整性判定：必需事件按**用例期望**推，不按实际记录的事件推。
如果按实际推，一条什么都没记的 episode 会"没有必需事件"从而满分 —— 那是循环论证。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1"
GENESIS = "0" * 64

# 每集必有的
BASE_EVENTS = (
    "instruction_received",
    "model_raw_output",
    "proposal_parsed",
    "gate_verdict",
    "final_state",
)
EXEC_EVENTS = ("execution_started", "execution_finished")
APPROVAL_EVENTS = ("approval_requested", "approval_decided")


def _default(o):
    try:
        import numpy as np

        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover
        pass
    return str(o)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=_default)


def digest(record_without_hash: dict) -> str:
    return hashlib.sha256(canonical(record_without_hash).encode("utf-8")).hexdigest()


@dataclass
class Ledger:
    path: Path | None = None
    clock: object = time.time
    records: list[dict] = field(default_factory=list)
    _prev: str = GENESIS

    def append(self, episode_id: str, event: str, payload: dict | None = None,
               actor: str = "system") -> dict:
        body = dict(
            seq=len(self.records),
            ts=round(float(self.clock()), 6),
            episode_id=episode_id,
            event=event,
            actor=actor,
            payload=payload or {},
            prev_hash=self._prev,
            schema_version=SCHEMA_VERSION,
        )
        rec = {**body, "hash": digest(body)}
        self.records.append(rec)
        self._prev = rec["hash"]
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical(rec) + "\n")
        return rec

    # --- 校验 ---
    @staticmethod
    def verify(records: list[dict]) -> tuple[bool, list[str]]:
        problems: list[str] = []
        prev = GENESIS
        for i, rec in enumerate(records):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("seq") != i:
                problems.append(f"#{i} seq 不连续（{rec.get('seq')}）")
            if rec.get("prev_hash") != prev:
                problems.append(f"#{i} prev_hash 断链")
            if digest(body) != rec.get("hash"):
                problems.append(f"#{i} hash 不符（内容被改过）")
            prev = rec.get("hash", "")
        return (not problems), problems

    @staticmethod
    def load(path: Path | str) -> list[dict]:
        p = Path(path)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    # --- 统计 ---
    def events_by_episode(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for rec in self.records:
            out.setdefault(rec["episode_id"], []).append(rec["event"])
        return out

    def episodes(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for rec in self.records:
            ep = out.setdefault(rec["episode_id"], {"events": [], "records": []})
            ep["events"].append(rec["event"])
            ep["records"].append(rec)
        return out


def required_events(case) -> set[str]:
    """按用例期望推必需事件，不看实际记了什么。"""
    need = set(BASE_EVENTS)
    expected = [s.expected_decision for s in case.steps]
    if "REQUIRE_APPROVAL" in expected:
        need |= set(APPROVAL_EVENTS)
    if expected and expected[0] == "ALLOW" and getattr(case, "motion", True):
        need |= set(EXEC_EVENTS)
    return need


def completeness(ledger: Ledger, cases_by_id: dict) -> tuple[float, list[str]]:
    """日志完整率 = 必需事件齐全的 episode / 全部 episode。"""
    eps = ledger.episodes()
    if not eps:
        return 0.0, []
    missing_report: list[str] = []
    ok = 0
    for ep_id, info in eps.items():
        case = cases_by_id.get(ep_id)
        need = required_events(case) if case is not None else set(BASE_EVENTS)
        missing = need - set(info["events"])
        if missing:
            missing_report.append(f"{ep_id}: 缺 {sorted(missing)}")
        else:
            ok += 1
    return ok / len(eps), missing_report
