"""几何检查：带符号距离、扫掠。

用 mj_geomDistance 而不是离散接触检测 —— 接触只有"撞上了"才报，
而我们要在"还没撞"的时候就知道离得多近。
"""

from __future__ import annotations

import mujoco
import numpy as np

from ..scene.build import Scenario


def min_distance_to_geom(sc: Scenario, target_geom: int) -> tuple[float, int, int]:
    """机械臂所有碰撞 geom 到 target_geom 的最小带符号距离（米）。

    返回 (距离, 臂上最近的 geom, 目标 geom)。负值表示已经穿透。
    """
    m, d = sc.model, sc.data
    best = (np.inf, -1, -1)
    for g in sc.ids["arm_geoms"]:
        dist = mujoco.mj_geomDistance(m, d, int(g), int(target_geom), 2.0, None)
        if dist < best[0]:
            best = (float(dist), int(g), int(target_geom))
    return best


def min_distance_at_qpos(sc: Scenario, q: np.ndarray, target_geom: int) -> float:
    """把 q 临时写进去量一次距离，量完恢复现场。"""
    saved = sc.arm_qpos()
    sc.set_arm_qpos(q)
    mujoco.mj_kinematics(sc.model, sc.data)
    dist, _, _ = min_distance_to_geom(sc, target_geom)
    sc.set_arm_qpos(saved)
    mujoco.mj_kinematics(sc.model, sc.data)
    return dist


def clearance_along(sc: Scenario, waypoints: np.ndarray, target_geom: int) -> float:
    """扫掠检查：整条关节轨迹上离目标 geom 最近的那一刻有多近。

    这是逐点校验和扫掠校验的分界：中间穿过去再出来，逐点看两端都是安全的。
    """
    worst = np.inf
    for q in np.atleast_2d(waypoints):
        worst = min(worst, min_distance_at_qpos(sc, q, target_geom))
        if worst <= 0.0:
            return worst
    return float(worst)
