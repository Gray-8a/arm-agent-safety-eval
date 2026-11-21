"""哈希链：能检篡改，且完整性判定不能循环论证。"""

from __future__ import annotations

import json

import pytest

from arm_eval.audit.log import GENESIS, Ledger, canonical, completeness, digest, required_events
from arm_eval.harness import Step, load_cases


@pytest.fixture()
def ledger():
    ticks = iter(range(1000))
    return Ledger(clock=lambda: float(next(ticks)))


def test_first_record_points_at_genesis(ledger):
    rec = ledger.append("ep-1", "instruction_received", {"instruction": "x"})
    assert rec["prev_hash"] == GENESIS
    assert rec["hash"] == digest({k: v for k, v in rec.items() if k != "hash"})


def test_chain_links(ledger):
    ledger.append("ep-1", "a", {})
    b = ledger.append("ep-1", "b", {})
    assert b["prev_hash"] == ledger.records[0]["hash"]


def test_verify_ok(ledger):
    for i in range(5):
        ledger.append("ep-1", f"e{i}", {"i": i})
    ok, problems = Ledger.verify(ledger.records)
    assert ok, problems


def test_tamper_payload_detected(ledger):
    for i in range(3):
        ledger.append("ep-1", f"e{i}", {"i": i})
    ledger.records[1]["payload"]["i"] = 999
    ok, problems = Ledger.verify(ledger.records)
    assert not ok and any("hash 不符" in p for p in problems)


def test_tamper_detected_at_the_tampered_record(ledger):
    """只改 payload：断在被改的那一条（hash 不符）。"""
    for i in range(4):
        ledger.append("ep-1", f"e{i}", {})
    ledger.records[1]["payload"]["x"] = 1
    ok, problems = Ledger.verify(ledger.records)
    assert not ok
    assert any("#1 hash 不符" in p for p in problems)


def test_forging_the_hash_breaks_the_next_link(ledger):
    """连带把被改那条的 hash 重算：断在它后面那条（prev_hash 对不上）。

    这才是链的意义 —— 改一条要么被自己抓，要么被下一条抓，跑不掉。
    """
    for i in range(4):
        ledger.append("ep-1", f"e{i}", {})
    rec = ledger.records[1]
    rec["payload"]["x"] = 1
    rec["hash"] = digest({k: v for k, v in rec.items() if k != "hash"})
    ok, problems = Ledger.verify(ledger.records)
    assert not ok
    assert any("#2 prev_hash 断链" in p for p in problems)


def test_reorder_detected(ledger):
    for i in range(3):
        ledger.append("ep-1", f"e{i}", {})
    ledger.records[0], ledger.records[1] = ledger.records[1], ledger.records[0]
    ok, _ = Ledger.verify(ledger.records)
    assert not ok


def test_delete_middle_detected(ledger):
    for i in range(4):
        ledger.append("ep-1", f"e{i}", {})
    del ledger.records[1]
    ok, _ = Ledger.verify(ledger.records)
    assert not ok


def test_file_roundtrip(tmp_path, ledger):
    ledger.path = tmp_path / "a.jsonl"
    ledger.append("ep-1", "e0", {"v": 1})
    ledger.append("ep-2", "e0", {"v": 2})
    loaded = Ledger.load(ledger.path)
    assert len(loaded) == 2
    assert Ledger.verify(loaded)[0] is True


def test_canonical_is_order_insensitive():
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


def test_canonical_handles_numpy():
    import numpy as np

    assert canonical({"x": np.float64(1.5), "y": np.array([1, 2])}) == \
        canonical({"x": 1.5, "y": [1, 2]})


def test_required_events_depend_on_expectation_not_recording():
    """必需事件按用例期望推。按实际记录推会变成循环论证。"""
    blocked = type("C", (), {"steps": [Step({"action": "move_to"}, "BLOCK", ())]})()
    allowed = type("C", (), {"steps": [Step({"action": "move_to"}, "ALLOW", ())]})()
    gated = type("C", (), {"steps": [Step({"action": "move_to"}, "REQUIRE_APPROVAL", ())]})()

    assert "execution_started" not in required_events(blocked)
    assert "execution_started" in required_events(allowed)
    assert "approval_decided" in required_events(gated)


def test_completeness_counts_missing(ledger):
    cases = {c.id: c for c in load_cases()}
    case = cases["normal-001"]
    ledger.append(case.id, "instruction_received", {})   # 只记一条，缺一堆
    rate, missing = completeness(ledger, cases)
    assert rate == 0.0 and missing
