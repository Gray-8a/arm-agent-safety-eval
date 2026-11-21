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

    def operator(proposal, verdict):
        seen["reasons"] = verdict.reason_codes
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
