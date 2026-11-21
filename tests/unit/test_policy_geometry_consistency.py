"""policy.yaml 里的 zone 几何必须和场景里真实的 geom 一致。

这条是给 limitations.md 里那个已知隐患兜底的：几何有两份（策略一份、场景一份），
手工同步迟早会错。对不上就直接红，不要让"策略里写着 25cm、场景里其实 10cm"这种事溜过去。
"""

from __future__ import annotations

import mujoco
import numpy as np
import pytest

from arm_eval.gate.policy import Policy
from arm_eval.scene import build


@pytest.fixture(scope="module")
def sc():
    return build.make(seed=0)


def zone_aabb(sc, name: str):
    """axis-aligned box geom 的世界坐标包围盒。"""
    gid = mujoco.mj_name2id(sc.model, mujoco.mjtObj.mjOBJ_GEOM, f"{name}_geom")
    assert gid >= 0, f"场景里没有 {name}_geom"
    mujoco.mj_forward(sc.model, sc.data)
    center = np.array(sc.data.geom_xpos[gid], dtype=float)
    half = np.array(sc.model.geom_size[gid], dtype=float)
    return center - half, center + half


def test_every_policy_zone_exists_in_scene(sc):
    for zone in Policy.load().zones:
        zone_aabb(sc, zone.name)  # 找不到就 assert 失败


def test_zone_bounds_match_geometry(sc):
    tol = 1e-3
    for zone in Policy.load().zones:
        lo, hi = zone_aabb(sc, zone.name)
        got = np.array([*lo, *hi])
        want = np.array([*zone.bounds.lo, *zone.bounds.hi])
        diff = np.abs(got - want)
        assert np.all(diff < tol), (
            f"zone {zone.name} 策略与场景不符：\n"
            f"  policy={np.round(want, 4)}\n  scene ={np.round(got, 4)}")


def test_zone_geoms_are_axis_aligned(sc):
    """这套判据假设没有旋转。真加了旋转要改成 OBB，别装作没有。"""
    for zone in Policy.load().zones:
        gid = mujoco.mj_name2id(sc.model, mujoco.mjtObj.mjOBJ_GEOM, f"{zone.name}_geom")
        rot = np.array(sc.data.geom_xmat[gid]).reshape(3, 3)
        assert np.allclose(rot, np.eye(3), atol=1e-6), f"{zone.name}_geom 被旋转过"


def test_human_zone_still_not_physical(sc):
    gid = sc.ids["zone_geom"]
    assert sc.model.geom_contype[gid] == 0
    assert sc.model.geom_conaffinity[gid] == 0


def test_human_zone_clearance_from_home(sc):
    """home 姿态离人区至少 10cm —— 场景改了对不上，这条会先红。"""
    from arm_eval.gate.geometry import min_distance_to_geom

    sc.reset()
    d, _, _ = min_distance_to_geom(sc, sc.ids["zone_geom"])
    assert d > 0.10, f"home clearance {d * 1000:.1f}mm"
