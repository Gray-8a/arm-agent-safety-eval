"""场景层：模型能装、重置确定、初始位安全。

这几条是后面所有评测的地基。地基不稳，拦截率数字全是噪音。
"""

from __future__ import annotations

import numpy as np
import pytest

from arm_eval.scene import build
from arm_eval.gate.geometry import min_distance_to_geom


@pytest.fixture(scope="module")
def sc():
    return build.make(seed=0)


def test_model_shape(sc):
    """nq=16（7 臂 + 2 指 + 7 方块），nu=8（7 臂 + 1 夹爪）。"""
    assert sc.model.nq == 16
    assert sc.model.nu == 8
    assert len(sc.ids["arm_geoms"]) > 0


def test_reset_is_deterministic(sc):
    """连续 reset 后 qpos 哈希必须一致。"""
    sc.reset()
    h1 = sc.qpos_hash()
    sc.reset()
    assert sc.qpos_hash() == h1


def test_two_instances_match():
    """同 seed 的两个实例，重置后状态一致 —— 换机器跑结论才可比。"""
    a = build.make(seed=0)
    b = build.make(seed=0)
    a.reset()
    b.reset()
    assert a.qpos_hash() == b.qpos_hash()


def test_seed_changes_cube_pose():
    a = build.make(seed=0, jitter=0.02)
    b = build.make(seed=1, jitter=0.02)
    assert not np.allclose(a.cube_xyz()[:2], b.cube_xyz()[:2])


def test_home_pose_is_safe(sc):
    """home 位离 human zone 至少 10cm。初始状态就不安全的话，后面没法谈。"""
    sc.reset()
    dist, _, _ = min_distance_to_geom(sc, sc.ids["zone_geom"])
    assert dist > 0.10, f"home clearance {dist * 1000:.1f}mm 太小"


def test_cube_rests_on_table(sc):
    """方块初始就落在桌面上，不靠"先掉一下"来找平衡。"""
    sc.reset()
    z = float(sc.cube_xyz()[2])
    assert abs(z - build.CUBE_SPAWN_Z) < 1e-6
    assert abs(z - (build.TABLE_TOP_Z + build.CUBE_HALF)) < 2e-3


def test_zone_geom_is_not_physical(sc):
    """human zone 是纯视觉 geom：物理上不挡，只有策略层能拦。"""
    z = sc.ids["zone_geom"]
    assert sc.model.geom_contype[z] == 0
    assert sc.model.geom_conaffinity[z] == 0


def test_tcp_offset_is_between_fingers(sc):
    """home 位 TCP 应该在机械臂前方、桌面上方，不是原点。"""
    sc.reset()
    p = sc.tcp_pos()
    assert p[0] > 0.3 and p[0] < 0.8
    assert 0.3 < p[2] < 0.8
