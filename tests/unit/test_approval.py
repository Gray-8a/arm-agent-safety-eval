"""审批链：签名、绑定、单次、期限。

这四条里任何一条不成立，"人工审批"就只是个弹窗，不是控制。
"""

from __future__ import annotations

import pytest

from arm_eval.gate.approval import ApprovalStore, proposal_hash
from arm_eval.gate.gate import Decision, Verdict

PROPOSAL = {"action": "move_to", "target": [0.40, 0.08, 0.50], "confidence": 0.9}
OTHER = {"action": "move_to", "target": [0.40, 0.30, 0.50], "confidence": 0.9}


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_verdict(reasons=("NEAR_CLEARANCE",)) -> Verdict:
    return Verdict(Decision.REQUIRE_APPROVAL, tuple(reasons),
                   {"clearances": {"human_zone": 0.06}, "target": PROPOSAL["target"],
                    "action": "move_to", "confidence": 0.9},
                   policy_hash="deadbeef")


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def store(clock):
    counter = iter(range(1000))
    return ApprovalStore(secret=b"test-secret", ttl_s=10.0, now=clock,
                         nonce_factory=lambda: f"n{next(counter):04d}")


def test_grant_and_verify(store):
    req = store.request(PROPOSAL, make_verdict())
    tok = store.grant(req, operator="alice")
    ok, why = store.verify(tok, req, current_proposal=PROPOSAL)
    assert ok and why == "GRANTED"


def test_wrong_secret_is_invalid(store, clock):
    req = store.request(PROPOSAL, make_verdict())
    tok = store.grant(req)
    other = ApprovalStore(secret=b"different-secret", ttl_s=10.0, now=clock)
    other._requests[req.request_id] = req
    ok, why = other.verify(tok, req, current_proposal=PROPOSAL)
    assert not ok and why == "APPROVAL_INVALID"


def test_tampered_token_is_invalid(store):
    req = store.request(PROPOSAL, make_verdict())
    tok = store.grant(req)
    forged = tok.__class__(token="0" * 64, request_id=tok.request_id,
                           proposal_digest=tok.proposal_digest, operator="mallory",
                           issued_at=tok.issued_at, expires_at=tok.expires_at, nonce=tok.nonce)
    ok, why = store.verify(forged, req, current_proposal=PROPOSAL)
    assert not ok and why == "APPROVAL_INVALID"


def test_token_bound_to_proposal(store):
    """批了 A 不能拿去跑 B —— 这是最容易漏的一条。"""
    req = store.request(PROPOSAL, make_verdict())
    tok = store.grant(req)
    ok, why = store.verify(tok, req, current_proposal=OTHER)
    assert not ok and why == "APPROVAL_MISMATCH"


def test_expired_token(store, clock):
    req = store.request(PROPOSAL, make_verdict())
    tok = store.grant(req)
    clock.advance(11.0)
    ok, why = store.verify(tok, req, current_proposal=PROPOSAL)
    assert not ok and why == "APPROVAL_EXPIRED"


def test_single_use(store):
    req = store.request(PROPOSAL, make_verdict())
    tok = store.grant(req)
    assert store.verify(tok, req, current_proposal=PROPOSAL)[0] is True
    ok, why = store.verify(tok, req, current_proposal=PROPOSAL)
    assert not ok and why == "APPROVAL_REPLAY"


def test_token_for_other_request_rejected(store):
    req_a = store.request(PROPOSAL, make_verdict())
    req_b = store.request(OTHER, make_verdict())
    tok_a = store.grant(req_a)
    ok, why = store.verify(tok_a, req_b, current_proposal=OTHER)
    assert not ok and why == "APPROVAL_INVALID"


def test_no_operator_means_denied(store):
    out = store.ask(PROPOSAL, make_verdict(), operator=None)
    assert not out.ok and out.reason == "APPROVAL_DENIED"
    assert out.token is None


def test_operator_refusal(store):
    out = store.ask(PROPOSAL, make_verdict(), operator=lambda req: False)
    assert not out.ok and out.reason == "APPROVAL_DENIED"


def test_operator_approval(store):
    out = store.ask(PROPOSAL, make_verdict(), operator=lambda req: True)
    assert out.ok and out.reason == "GRANTED" and out.token is not None


def test_audit_records_the_whole_chain(store):
    store.ask(PROPOSAL, make_verdict(), operator=lambda req: True, episode_id="ep-1")
    events = [e["event"] for e in store.audit]
    assert events == ["approval_requested", "approval_granted", "approval_consumed"]
    assert all(e.get("request_id") for e in store.audit)


def test_proposal_hash_is_stable_and_order_free():
    a = {"action": "move_to", "target": [1, 2, 3], "confidence": 0.9}
    b = {"confidence": 0.9, "action": "move_to", "target": [1, 2, 3]}
    assert proposal_hash(a) == proposal_hash(b)
    assert proposal_hash(a) != proposal_hash({**a, "target": [1, 2, 4]})


def test_ephemeral_secret_when_env_missing(monkeypatch):
    monkeypatch.delenv("ARM_EVAL_APPROVAL_SECRET", raising=False)
    s = ApprovalStore()
    assert s.ephemeral is True and len(s.secret) == 32


def test_env_secret_is_used(monkeypatch):
    monkeypatch.setenv("ARM_EVAL_APPROVAL_SECRET", "from-env")
    s = ApprovalStore()
    assert s.ephemeral is False and s.secret == b"from-env"


def test_summary_is_human_readable(store):
    req = store.request(PROPOSAL, make_verdict())
    s = req.summary()
    assert s["reasons"] == ["NEAR_CLEARANCE"]
    assert s["clearances"] == {"human_zone": 0.06}
    assert s["ttl_s"] == 10.0
