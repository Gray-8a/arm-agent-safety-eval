"""运动学：只做 DLS 逆解和正向，不搞控制算法。

定位：把「提案里的目标点」翻译成关节角，是手段不是交付物。
"""

from __future__ import annotations

import mujoco
import numpy as np

from .build import Scenario

# 抓取姿态：手系 z 轴朝下（从上方接近）。绕 x 转 180°。
R_DOWN = np.array([[1.0, 0.0, 0.0],
                   [0.0, -1.0, 0.0],
                   [0.0, 0.0, -1.0]])


def _rot_log(r_err: np.ndarray) -> np.ndarray:
    """SO(3) 对数映射：旋转矩阵 -> 旋转向量。"""
    c = np.clip((np.trace(r_err) - 1.0) * 0.5, -1.0, 1.0)
    theta = float(np.arccos(c))
    if theta < 1e-8:
        return np.zeros(3)
    s = 2.0 * np.sin(theta)
    axis = np.array([r_err[2, 1] - r_err[1, 2],
                     r_err[0, 2] - r_err[2, 0],
                     r_err[1, 0] - r_err[0, 1]]) / s
    return axis * theta


def hand_rot(sc: Scenario) -> np.ndarray:
    return sc.data.xmat[sc.ids["hand_body"]].reshape(3, 3).copy()


def jacobian_arm(sc: Scenario) -> tuple[np.ndarray, np.ndarray]:
    """TCP 处、只取 7 个臂关节列的 (3×7 平移, 3×7 旋转) 雅可比。"""
    jacp = np.zeros((3, sc.model.nv))
    jacr = np.zeros((3, sc.model.nv))
    mujoco.mj_jac(sc.model, sc.data, jacp, jacr, sc.tcp_pos(), sc.ids["hand_body"])
    return jacp[:, sc.ids["arm_dadr"]], jacr[:, sc.ids["arm_dadr"]]


def fk(sc: Scenario, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """给定关节角，算 (TCP 世界坐标, hand 旋转)。不动 data 的持久状态。"""
    saved = sc.arm_qpos()
    sc.set_arm_qpos(q)
    mujoco.mj_kinematics(sc.model, sc.data)
    p, r = sc.tcp_pos(), hand_rot(sc)
    sc.set_arm_qpos(saved)
    mujoco.mj_kinematics(sc.model, sc.data)
    return p, r


def dls_ik(sc: Scenario, target: np.ndarray, q_init: np.ndarray | None = None,
           target_rot: np.ndarray | None = None, rot_weight: float = 0.4,
           iters: int = 80, damping: float = 0.06, tol: float = 1e-3,
           max_joint_step: float = 0.2) -> tuple[np.ndarray, float]:
    """6 自由度阻尼最小二乘逆解。返回 (q, 位置残差米)。

    姿态项权重不高：位置准才是抓取的关键，姿态只要别翻过去。
    """
    q = sc.arm_qpos() if q_init is None else np.array(q_init, dtype=float)
    target = np.asarray(target, dtype=float)
    lo, hi = sc.joint_limits()
    res = np.inf

    for _ in range(iters):
        sc.set_arm_qpos(q)
        mujoco.mj_kinematics(sc.model, sc.data)
        err_p = target - sc.tcp_pos()
        res = float(np.linalg.norm(err_p))
        if res < tol and target_rot is None:
            break
        if res < tol and target_rot is not None:
            if np.linalg.norm(_rot_log(target_rot @ hand_rot(sc).T)) < 0.02:
                break

        jp, jr = jacobian_arm(sc)
        if target_rot is not None:
            err_r = _rot_log(target_rot @ hand_rot(sc).T)
            j = np.vstack([jp, rot_weight * jr])
            e = np.concatenate([err_p, rot_weight * err_r])
        else:
            j, e = jp, err_p

        jjt = j @ j.T + (damping ** 2) * np.eye(j.shape[0])
        dq = j.T @ np.linalg.solve(jjt, e)
        q = np.clip(q + np.clip(dq, -max_joint_step, max_joint_step), lo, hi)

    sc.set_arm_qpos(q)
    mujoco.mj_kinematics(sc.model, sc.data)
    res = float(np.linalg.norm(target - sc.tcp_pos()))
    return q, res
