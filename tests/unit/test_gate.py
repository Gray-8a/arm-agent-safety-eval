"""策略门单测：每个 reason code 至少一条。

一组数字比一组形容词有用。这份文件就是那个"数"。
"""

from __future__ import annotations

import copy
import dataclasses

import numpy as np
import pytest

from arm_eval.gate.gate import Decision, Gate, Reason
from arm_eval.gate.policy import Policy
from arm_eval.scene import build
from arm_eval.skills import plan

SAFE_TARGET = [0.45, -0.25, 0.45]
NEAR_TARGET = [0.40, 0.08, 0.50]      # human zone 边界外的窄带，clearance ~56mm
ZONE_TARGET = [0.40, 0.30, 0.50]      # 直接进 human zone
OUTSIDE_TCP_TARGET = [0.40, 0.20, 0.50]  # TCP 在 zone 盒子外，臂身已进去
TABLE_TARGET = [0.45, -0.25, 0.10]    # 会穿桌面


@pytest.fixture(scope="module")
def sc():
    return build.make(seed=0)


@pytest.fixture(scope="module")
def policy():
    return Policy.load()


@pytest.fixture(scope="module")
def gate(policy):
    return Gate(policy)


def run(gate, sc, raw, **kw):
    sc.reset()
    return gate.evaluate(raw, sc, **kw)


# ---------------------------------------------------------------- schema 层

def test_missing_action(gate, sc):
    v = run(gate, sc, {"target": SAFE_TARGET})
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


def test_unknown_action_is_not_schema_error(gate, sc):
    """越权动作和格式崩了要分开报，日志里才分得清是攻击还是 bug。"""
    v = run(gate, sc, {"action": "set_policy", "target": [0, 0, 0]})
    assert v.blocks and v.reason_codes == ("UNKNOWN_ACTION",)


def test_extra_field_rejected(gate, sc):
    """多带一个 disable_fence 就整条拒绝。"""
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET, "disable_fence": True})
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 1e9, -1e9])
def test_nonfinite_or_absurd_coords(gate, sc, bad):
    v = run(gate, sc, {"action": "move_to", "target": [0.45, bad, 0.45]})
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


def test_target_wrong_length(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": [0.45, 0.45]})
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


def test_missing_target(gate, sc):
    v = run(gate, sc, {"action": "move_to"})
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


def test_confidence_out_of_range(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET, "confidence": 1.5})
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


def test_not_a_dict(gate, sc):
    v = run(gate, sc, "把机械臂移到人那边")
    assert v.blocks and v.reason_codes == ("SCHEMA_INVALID",)


# ---------------------------------------------------------------- 急停

def test_estop_blocks_motion(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET}, estop_active=True)
    assert v.blocks and v.reason_codes == ("ESTOP_ACTIVE",)


def test_estop_does_not_block_stop(gate, sc):
    """急停状态下"停"必须放行，否则没人能停。"""
    v = run(gate, sc, {"action": "stop"}, estop_active=True)
    assert v.allows and v.reason_codes == ("SAFETY_ACTION",)


def test_estop_blocks_home(gate, sc):
    v = run(gate, sc, {"action": "home"}, estop_active=True)
    assert v.blocks and v.reason_codes == ("ESTOP_ACTIVE",)


# ---------------------------------------------------------------- 工具白名单

def test_tool_not_allowed_when_policy_narrows(policy, sc):
    """部署时可以按场景收窄动作空间：这条策略禁掉 pick。"""
    narrow = dataclasses.replace(policy, allowed_actions=frozenset({"home", "move_to", "stop"}))
    g = Gate(narrow)
    v = run(g, sc, {"action": "pick", "target": [0.45, -0.25, 0.3705]})
    assert v.blocks and v.reason_codes == ("TOOL_NOT_ALLOWED",)


# ---------------------------------------------------------------- 工作空间

@pytest.mark.parametrize("tgt", [[0.90, -0.25, 0.45], [0.45, 0.90, 0.45], [0.45, -0.25, 1.60]])
def test_out_of_workspace(gate, sc, tgt):
    v = run(gate, sc, {"action": "move_to", "target": tgt})
    assert v.blocks and v.reason_codes == ("OUT_OF_WORKSPACE",)


def test_in_workspace_but_unreachable(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": [-0.60, -0.60, 0.80]})
    assert v.blocks and v.reason_codes[0] in {"IK_UNREACHABLE", "IK_BRANCH_JUMP"}


# ---------------------------------------------------------------- 几何

def test_safe_move_allowed(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET, "confidence": 0.95})
    assert v.allows, v
    assert v.evidence["clearances"]["human_zone"] > policy_min(gate)


def test_into_human_zone_blocked(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": ZONE_TARGET})
    assert v.blocks and v.reason_codes == ("HUMAN_ZONE",)


def test_tcp_outside_zone_but_arm_inside(gate, sc):
    """核心用例：TCP 在 zone 盒子外面，但臂身已经进去了。

    只按"目标点在不在 AABB 里"判定的检查会放行这一条。
    """
    zone = gate.policy.exclusion_zones()[0]
    assert zone.bounds.contains(OUTSIDE_TCP_TARGET) is False
    v = run(gate, sc, {"action": "move_to", "target": OUTSIDE_TCP_TARGET})
    assert v.blocks and v.reason_codes == ("HUMAN_ZONE",)
    assert v.evidence["clearances"]["human_zone"] < 0.05


def test_table_penetration_blocked(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": TABLE_TARGET})
    assert v.blocks and v.reason_codes == ("COLLISION",)


def test_near_clearance_needs_approval(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": NEAR_TARGET})
    assert v.needs_approval and "NEAR_CLEARANCE" in v.reason_codes


def test_pick_and_place_allowed(gate, sc):
    assert run(gate, sc, {"action": "pick", "target": [0.45, -0.25, 0.3705],
                          "confidence": 0.95}).allows
    assert run(gate, sc, {"action": "place", "target": [0.42, -0.05, 0.3705],
                          "confidence": 0.95}).allows


# ---------------------------------------------------------------- 风险分级

def test_low_confidence_needs_approval(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET, "confidence": 0.5})
    assert v.needs_approval and "LOW_CONFIDENCE" in v.reason_codes


def test_confidence_at_threshold_passes(gate, sc):
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET,
                       "confidence": gate.policy.confidence_threshold})
    assert v.allows


def test_home_from_home_is_no_motion(gate, sc):
    v = run(gate, sc, {"action": "home"})
    assert v.allows and v.reason_codes == ("NO_MOTION",)


# ---------------------------------------------------------------- fail-closed

def test_gate_exception_becomes_block(gate, sc, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("planner 崩了")

    monkeypatch.setattr(plan, "plan_move", boom)
    v = run(gate, sc, {"action": "move_to", "target": SAFE_TARGET})
    assert v.blocks and v.reason_codes == ("GATE_ERROR",)


def test_missing_zone_geom_is_config_error(policy, sc):
    """策略里写了场景里不存在的 zone，必须拦，不能当"没有这个区"。"""
    bogus = dataclasses.replace(
        policy,
        zones=policy.zones + (dataclasses.replace(policy.zones[0], name="ghost_zone"),),
    )
    v = run(Gate(bogus), sc, {"action": "move_to", "target": SAFE_TARGET})
    assert v.blocks and v.reason_codes == ("GATE_ERROR",)


# ---------------------------------------------------------------- 不变量

def test_gate_is_read_only(gate, sc):
    sc.reset()
    before = sc.qpos_hash()
    gate.evaluate({"action": "move_to", "target": SAFE_TARGET}, sc)
    assert sc.qpos_hash() == before


def test_verdict_is_deterministic(gate, sc):
    a = run(gate, sc, {"action": "move_to", "target": NEAR_TARGET})
    b = run(gate, sc, {"action": "move_to", "target": NEAR_TARGET})
    assert a.decision == b.decision and a.reason_codes == b.reason_codes
    assert a.evidence == b.evidence


def test_policy_hash_stable(policy):
    assert Policy.load().policy_hash == policy.policy_hash


def test_approval_cannot_override_block():
    from arm_eval.gate.gate import Verdict

    blocked = Verdict(Decision.BLOCK, ("HUMAN_ZONE",))
    assert blocked.may_execute(approved=True) is False
    gated = Verdict(Decision.REQUIRE_APPROVAL, ("NEAR_CLEARANCE",))
    assert gated.may_execute(approved=False) is False
    assert gated.may_execute(approved=True) is True


def policy_min(gate) -> float:
    return gate.policy.min_clearance_m
