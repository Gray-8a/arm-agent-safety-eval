"""执行器：全项目唯一允许调 mj_step 的地方。

这条不是纪律，是结构约束 —— tests/unit/test_single_choke_point.py 会扫 AST，
别的文件出现 mj_step 直接 CI 红。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import mujoco
import numpy as np

from .scene.build import TCP_OFFSET, Scenario
from .skills.plan import Trajectory

CTRL_EVERY = 10  # 50Hz 控制 / 500Hz 物理


@dataclass
class ExecResult:
    ok: bool
    halted: bool = False
    halt_reason: str = ""
    steps: int = 0
    waypoints_done: int = 0
    max_joint_err: float = 0.0
    final_tcp: np.ndarray | None = None
    trace: list = field(default_factory=list)


def _sync_carried(sc: Scenario) -> None:
    """被人扛着的方块跟着手走（kinematic carry，见 docs/limitations.md）。"""
    if not sc.meta.get("attached"):
        return
    rot = sc.data.xmat[sc.ids["hand_body"]].reshape(3, 3)
    pos = sc.data.xpos[sc.ids["hand_body"]] + rot @ TCP_OFFSET
    a = sc.meta["cube_qadr"]
    sc.data.qpos[a:a + 3] = pos
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, rot.flatten())
    sc.data.qpos[a + 3:a + 7] = q
    d = sc.model.jnt_dofadr[sc.ids["cube_joint"]]
    sc.data.qvel[d:d + 6] = 0.0


def set_gripper(sc: Scenario, value: float) -> None:
    sc.data.ctrl[sc.ids["grip_act"]] = float(value)


def attach_cube(sc: Scenario) -> None:
    sc.meta["attached"] = True
    _sync_carried(sc)


def detach_cube(sc: Scenario) -> None:
    sc.meta["attached"] = False


def step_physics(sc: Scenario, n: int = 1) -> None:
    for _ in range(n):
        _sync_carried(sc)
        mujoco.mj_step(sc.model, sc.data)


def execute_trajectory(sc: Scenario, traj: Trajectory, ctrl_hz: int = 50,
                       settle_tol: float = 0.015, max_ctrl_ticks: int = 40,
                       stop_event: threading.Event | None = None,
                       on_tick=None) -> ExecResult:
    """逐路点跟随。每 10 个物理步下一次 ctrl。

    stop_event 就是急停带外通道：每个物理步都查，不给"跑到一半"留窗口。
    """
    res = ExecResult(ok=True)
    for i, q_wp in enumerate(traj.qpos):
        sc.data.ctrl[sc.ids["arm_act_ids"]] = q_wp
        converged = False
        for _ in range(max_ctrl_ticks * CTRL_EVERY):
            if stop_event is not None and stop_event.is_set():
                res.ok = False
                res.halted = True
                res.halt_reason = "ESTOP_ACTIVE"
                res.final_tcp = sc.tcp_pos()
                return res
            step_physics(sc, 1)
            res.steps += 1
            err = float(np.max(np.abs(sc.arm_qpos() - q_wp)))
            res.max_joint_err = max(res.max_joint_err, err)
            if on_tick is not None and res.steps % CTRL_EVERY == 0:
                on_tick(i, sc.tcp_pos())
            if err < settle_tol:
                converged = True
                break
        if not converged:
            res.ok = False
            res.halt_reason = res.halt_reason or "WAYPOINT_NOT_REACHED"
        res.waypoints_done = i + 1

    res.final_tcp = sc.tcp_pos()
    return res
