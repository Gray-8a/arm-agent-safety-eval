"""端到端：跑出来的日志能不能验、能不能回放。

回放只重跑**判断**，不重跑执行 —— 判决才是交付物，仿真步数跨机器有浮点差异。
"""

from __future__ import annotations

import mujoco
import numpy as np
import pytest

from arm_eval.audit.log import Ledger, completeness
from arm_eval.gate.gate import Gate
from arm_eval.gate.policy import Policy
from arm_eval.harness import load_cases, run_suite
from arm_eval.scene import build

SUBSET = ("normal-001", "normal-016", "normal-017", "boundary-001", "boundary-003",
          "adv-001", "adv-019", "adv-028", "adv-030")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    all_cases = {c.id: c for c in load_cases()}
    cases = [all_cases[cid] for cid in SUBSET if cid in all_cases]
    path = tmp_path_factory.mktemp("audit") / "audit.jsonl"
    ledger = Ledger(path=path)
    results = run_suite(cases, ledger=ledger)
    return dict(cases=cases, by_id={c.id: c for c in cases}, results=results,
                ledger=ledger, path=path)


def test_chain_verifies(run):
    ok, problems = Ledger.verify(run["ledger"].records)
    assert ok, problems


def test_log_is_complete(run):
    rate, missing = completeness(run["ledger"], run["by_id"])
    assert rate == 1.0, missing


def test_records_are_written_to_disk(run):
    loaded = Ledger.load(run["path"])
    assert len(loaded) == len(run["ledger"].records)
    assert Ledger.verify(loaded)[0] is True


def test_tampering_on_disk_is_detected(run, tmp_path):
    loaded = Ledger.load(run["path"])
    loaded[5]["payload"]["injected"] = "改了"
    ok, problems = Ledger.verify(loaded)
    assert not ok


def test_every_verdict_is_replayable(run):
    """拿去重放：同状态、同提案、同裁决、同 reason。"""
    gate = Gate(Policy.load())
    eps = run["ledger"].episodes()
    mismatches = []
    for ep_id, info in eps.items():
        meta = next(r["payload"] for r in info["records"]
                    if r["event"] == "instruction_received")
        sc = build.make(scene_id=meta["scene"], seed=meta["seed"], cube_xy=meta["cube_xy"])
        sc.reset()
        for rec in info["records"]:
            if rec["event"] != "gate_verdict":
                continue
            step = rec["payload"]["step"]
            raw = next(r["payload"]["raw"] for r in info["records"]
                       if r["event"] == "model_raw_output" and r["payload"]["step"] == step)
            q = (rec["payload"].get("evidence") or {}).get("plan_from_q")
            if q is not None:
                sc.set_arm_qpos(np.asarray(q, dtype=float))
                mujoco.mj_forward(sc.model, sc.data)
            v = gate.evaluate(raw, sc, estop_active=bool(rec["payload"].get("estop", False)))
            if v.decision.value != rec["payload"]["decision"] or \
                    list(v.reason_codes) != list(rec["payload"]["reason_codes"]):
                mismatches.append(f"{ep_id} step{step}: "
                                  f"{rec['payload']['decision']} -> {v.decision.value}")
    assert not mismatches, mismatches


def test_approval_chain_is_logged(run):
    """审批用例必须留下 requester / decider / 谁批的。"""
    eps = run["ledger"].episodes()
    gated = [eid for eid, info in eps.items() if "approval_requested" in info["events"]]
    assert gated, "一条审批请求都没记"
    for eid in gated:
        decided = [r for r in eps[eid]["records"] if r["event"] == "approval_decided"]
        assert decided, f"{eid} 有请求没有决定"
        assert decided[0]["actor"] == "operator"


def test_no_execution_event_for_blocked(run):
    eps = run["ledger"].episodes()
    for eid in ("adv-001", "boundary-003"):
        if eid in eps:
            assert "execution_started" not in eps[eid]["events"]
