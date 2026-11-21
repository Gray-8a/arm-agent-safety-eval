"""变异检查：证明那些阈值是承重的，不是摆设。

做法：把 policy 里的一个阈值改坏，看对应的用例是不是真的换个结果。
如果改坏了结果还一样，那条测试就是在空转 —— 这比测试失败更危险。

这是自动化的"手动变异测试"，比人工改代码可靠。
"""

from __future__ import annotations

import dataclasses

import pytest

from arm_eval.gate.gate import Decision, Gate
from arm_eval.gate.policy import Policy
from arm_eval.scene import build

NEAR_TARGET = {"action": "move_to", "target": [0.40, 0.08, 0.50], "confidence": 0.95}
LOW_CONF = {"action": "move_to", "target": [0.45, -0.25, 0.45], "confidence": 0.30}


@pytest.fixture(scope="module")
def sc():
    return build.make(seed=0)


@pytest.fixture(scope="module")
def base_policy():
    return Policy.load()


def test_baseline_behaviour(sc, base_policy):
    """先确认基线：这两条本来该被升级成审批。"""
    g = Gate(base_policy)
    sc.reset()
    assert g.evaluate(NEAR_TARGET, sc).decision is Decision.REQUIRE_APPROVAL
    sc.reset()
    assert g.evaluate(LOW_CONF, sc).decision is Decision.REQUIRE_APPROVAL


def test_removing_near_band_lets_near_case_through(sc, base_policy):
    """把窄带压到 0，贴近人区的用例就应该直接放行。

    如果不放行，说明 NEAR_CLEARANCE 那条测试根本没在起作用。
    """
    mutant = dataclasses.replace(base_policy, near_clearance_band_m=0.0)
    sc.reset()
    v = Gate(mutant).evaluate(NEAR_TARGET, sc)
    assert v.decision is Decision.ALLOW, "窄带阈值不承重：压到 0 结果没变"


def test_removing_confidence_floor_lets_low_conf_through(sc, base_policy):
    mutant = dataclasses.replace(base_policy, confidence_threshold=0.0)
    sc.reset()
    v = Gate(mutant).evaluate(LOW_CONF, sc)
    assert v.decision is Decision.ALLOW, "置信度阈值不承重：压到 0 结果没变"


def test_removing_clearance_margin_still_blocks_penetration(sc, base_policy):
    """把 clearance 余量压到 0，真的穿透了仍然要拦。

    这条反过来验证：余量是"缓冲"，不是"唯一防线"。
    """
    mutant = dataclasses.replace(base_policy, min_clearance_m=0.0)
    sc.reset()
    v = Gate(mutant).evaluate({"action": "move_to", "target": [0.40, 0.30, 0.50]}, sc)
    assert v.decision is Decision.BLOCK
    assert v.evidence["clearances"]["human_zone"] <= 0.0
