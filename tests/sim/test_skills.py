"""技能层 + 执行器：轨迹有界、IK 收敛、抓放精度。

D1 的验收：一次 pick -> carry -> place 的落点误差 < 5mm。
"""

from __future__ import annotations

import numpy as np
import pytest

from arm_eval import executor as ex
from arm_eval.scene import build
from arm_eval.scene.kin import fk
from arm_eval.skills import plan


@pytest.fixture()
def sc():
    return build.make(seed=0)


def test_plan_line_reaches_target(sc):
    t = plan.plan_move(sc, [0.45, -0.10, 0.45])
    p = fk(sc, t.qpos[-1])[0]
    assert np.linalg.norm(p - np.array([0.45, -0.10, 0.45])) < 0.005


def test_trajectory_is_bounded(sc):
    """相邻路点的关节增量必须有界，否则"每步 2cm"是假的。"""
    t = plan.plan_pick(sc, sc.cube_xyz())
    assert t.max_joint_step() <= plan.MAX_JOINT_STEP_RAD
    assert t.max_cart_step() <= 0.021


def test_unreachable_target_raises(sc):
    with pytest.raises(plan.PlanError) as ei:
        plan.plan_move(sc, [1.40, 0.0, 0.10])
    assert ei.value.code == "IK_UNREACHABLE"


def test_nan_target_rejected(sc):
    with pytest.raises(plan.PlanError) as ei:
        plan.plan_move(sc, [0.4, float("nan"), 0.4])
    assert ei.value.code == "SCHEMA_INVALID"


def test_pick_place_accuracy(sc):
    """D1 的核心验收：抓起来、搬走、放下，落点误差 < 5mm。"""
    cube0 = sc.cube_xyz()
    ex.execute_trajectory(sc, plan.plan_pick(sc, cube0))
    ex.set_gripper(sc, 0.0)
    ex.step_physics(sc, 150)
    ex.attach_cube(sc)

    target = np.array([0.42, -0.05, build.CUBE_SPAWN_Z])
    ex.execute_trajectory(sc, plan.plan_place(sc, target))
    ex.detach_cube(sc)
    ex.step_physics(sc, 300)

    err = np.array(sc.cube_xyz()) - target
    assert np.linalg.norm(err[:2]) < 0.005, f"XY 落点误差 {np.round(err[:2] * 1000, 1)}mm"
    assert abs(err[2]) < 0.005


def test_estop_halts_mid_trajectory(sc):
    """急停是带外通道：跑到一半置位，必须立刻停，不允许跑完。"""
    import threading

    ev = threading.Event()
    t = plan.plan_move(sc, [0.45, 0.10, 0.55])  # 一段较长的运动
    ticks = {"n": 0}

    def on_tick(_i, _p):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            ev.set()

    res = ex.execute_trajectory(sc, t, stop_event=ev, on_tick=on_tick)
    assert res.halted is True
    assert res.halt_reason == "ESTOP_ACTIVE"
    assert res.waypoints_done < len(t), "急停后还跑完了整条轨迹"


def test_carried_cube_follows_tcp(sc):
    """kinematic carry：方块中心应该跟着 TCP 走。"""
    ex.execute_trajectory(sc, plan.plan_pick(sc, sc.cube_xyz()))
    ex.set_gripper(sc, 0.0)
    ex.step_physics(sc, 150)
    ex.attach_cube(sc)

    ex.execute_trajectory(sc, plan.retract(sc, 0.08))
    d = np.linalg.norm(np.array(sc.cube_xyz()) - sc.tcp_pos())
    assert d < 0.01, f"搬运中方块离 TCP {d * 1000:.1f}mm"
