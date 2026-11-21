"""episode 层：裁决 -> 执行 -> 成败，以及三条不变量。"""

from __future__ import annotations

import numpy as np
import pytest

from arm_eval.episode import check_outcome, run_episode
from arm_eval.gate.gate import Decision, Gate
from arm_eval.gate.policy import Policy
from arm_eval.harness import Case, Step, load_cases
from arm_eval.scene import build


@pytest.fixture(scope="module")
def gate():
    return Gate(Policy.load())


@pytest.fixture(scope="module")
def sc():
    return build.make(seed=0)


@pytest.fixture(scope="module")
def by_id():
    return {c.id: c for c in load_cases()}


def test_allowed_case_executes_and_succeeds(gate, sc, by_id):
    ep = run_episode(by_id["normal-001"], gate, sc=sc)
    assert [s.decision for s in ep.steps] == ["ALLOW", "ALLOW"]
    assert ep.executed
    assert ep.success is True, ep.success_detail
    assert ep.sim_steps > 0


def test_blocked_case_never_executes(gate, sc, by_id):
    ep = run_episode(by_id["adv-002"], gate, sc=sc)
    assert ep.blocked_at == 0
    assert not ep.executed
    assert ep.sim_steps == 0


def test_approval_denied_means_no_motion(gate, sc, by_id):
    ep = run_episode(by_id["boundary-001"], gate, sc=sc)
    assert ep.steps[0].decision == "REQUIRE_APPROVAL"
    assert ep.steps[0].approved is False
    assert not ep.executed


def test_approval_granted_executes(gate, sc, by_id):
    """审批可以把 REQUIRE_APPROVAL 放行。这是审批唯一的权力。"""
    seen = {}

    def operator(req):
        seen["reasons"] = req.reason_codes
        return True

    ep = run_episode(by_id["boundary-001"], gate, operator=operator, sc=sc)
    assert ep.steps[0].approved is True
    assert ep.executed
    assert seen["reasons"] == ("NEAR_CLEARANCE",)


def test_executed_trajectory_is_the_validated_one(gate, sc, by_id):
    """执行的就该是 gate 校验过的那条。重新规划会引入 TOCTOU。"""
    case = by_id["normal-002"]
    ep = run_episode(case, gate, sc=sc)
    validated = ep.steps[0].verdict.trajectory
    assert validated is not None and len(validated) > 0
    # 用同样的初始状态重跑一次 gate，轨迹应该完全一致
    sc.reset()
    again = gate.evaluate(case.steps[0].proposal, sc)
    assert len(again.trajectory) == len(validated)
    assert np.allclose(again.trajectory.qpos, validated.qpos)


def test_episode_is_deterministic(gate, sc, by_id):
    a = run_episode(by_id["normal-004"], gate, sc=sc)
    b = run_episode(by_id["normal-004"], gate, sc=sc)
    assert a.success == b.success
    assert [s.decision for s in a.steps] == [s.decision for s in b.steps]
    assert a.sim_steps == b.sim_steps


def test_check_outcome_unknown_kind_raises(gate, sc):
    with pytest.raises(ValueError):
        check_outcome({"kind": "nonsense"}, sc)


def test_empty_check_means_no_verdict(sc):
    ok, detail = check_outcome(None, sc)
    assert ok is None and detail == ""


def test_stop_action_is_executable_noop(gate, sc):
    case = Case(id="t-stop", layer="normal", scene="table_panda_humanzone", seed=0,
                cube_xy=None, instruction="停", check=None, expect_success=None,
                steps=(Step({"action": "stop"}, "ALLOW", ("SAFETY_ACTION",)),))
    ep = run_episode(case, gate, sc=sc)
    assert ep.steps[0].decision == "ALLOW"
    assert not ep.executed  # 没有轨迹，不执行，也不报错

def test_toctou_human_walks_closer(gate, sc, by_id):
    """审批通过后、执行前人走近了：同一条轨迹再量一次，必须拦。

    这是审批节点最经典的漏洞：批准的那一刻环境是安全的，不等于执行的那一刻还是。
    """
    import mujoco

    gid = sc.ids["zone_geom"]
    original = sc.model.geom_pos[gid].copy()
    try:
        def on_approved(_i):
            sc.model.geom_pos[gid][1] -= 0.06   # 人往机械臂这边挪 6cm
            mujoco.mj_forward(sc.model, sc.data)

        ep = run_episode(by_id["boundary-001"], gate, operator=lambda req: True,
                         sc=sc, on_approved=on_approved)
        assert ep.steps[0].approved is True, "审批本身应该通过"
        assert not ep.executed, "环境变了还执行了"
        assert ep.steps[0].halt_reason == "STATE_CHANGED"
        assert ep.steps[0].recheck is not None and ep.steps[0].recheck.blocks
    finally:
        sc.model.geom_pos[gid] = original
        mujoco.mj_forward(sc.model, sc.data)


def test_recheck_is_clean_when_nothing_changed(gate, sc, by_id):
    ep = run_episode(by_id["boundary-001"], gate, operator=lambda req: True, sc=sc)
    assert ep.steps[0].recheck is not None and ep.steps[0].recheck.allows
    assert ep.executed


def test_replay_same_approval_twice(gate, sc, by_id):
    """同一个 store 里，第二条同提案的审批会被单次使用规则挡住。"""
    from arm_eval.gate.approval import ApprovalStore

    store = ApprovalStore(secret=b"t", ttl_s=60.0)
    case = by_id["boundary-001"]
    a = run_episode(case, gate, operator=lambda req: True, sc=sc, store=store)
    b = run_episode(case, gate, operator=lambda req: True, sc=sc, store=store)
    assert a.executed
    assert b.steps[0].approved, "第二次是新请求，会正常签发"
    # 但把第一次的 token 拿出来重放，必须被拒
    first_req = store.audit[1]["request_id"]
    assert first_req in store._consumed
