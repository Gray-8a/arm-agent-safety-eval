"""技能层：把「目标点」翻译成关节轨迹。

只做规划，不动 data 的持久状态（IK 内部的临时写入都会恢复）。
真正动仿真的一层是 executor.py。

两个刻意的约束：
1. 所有笛卡尔运动都从固定的 READY 姿态出发。否则 IK 会在等价解之间跳，
   TCP 不动、腕部翻 60°，而"每步 2cm"的假设就废了。
2. 相邻路点的最大关节增量必须 <= MAX_JOINT_STEP_RAD。超了直接 PlanError，
   不给执行器一个"看着像小步、实际大跳"的轨迹。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import mujoco
import numpy as np

from ..scene.build import HOME_ARM_QPOS, Scenario
from ..scene.kin import R_DOWN, dls_ik, fk

# READY：工具朝下（R_DOWN），悬在桌面上方 0.25m。所有笛卡尔运动的起点。
READY_ARM_QPOS = np.array([-0.197568, -0.125771, -0.197568, -1.503294,
                           -0.024209, 1.380157, 0.386995])
READY_XYZ = np.array([0.45, -0.20, 0.60])

MAX_JOINT_STEP_RAD = 0.15
HOME_MAX_JOINT_STEP = 0.08


class PlanError(RuntimeError):
    """规划失败。由 gate 转成 BLOCK，不向上抛。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class Trajectory:
    kind: str
    qpos: np.ndarray                 # (N, 7) 关节路点
    xyz: np.ndarray                  # (N, 3) 对应 TCP 世界坐标
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.qpos)

    @property
    def target(self) -> np.ndarray:
        return self.xyz[-1]

    def max_cart_step(self) -> float:
        if len(self.xyz) < 2:
            return 0.0
        return float(np.max(np.linalg.norm(np.diff(self.xyz, axis=0), axis=1)))

    def max_joint_step(self) -> float:
        if len(self.qpos) < 2:
            return 0.0
        return float(np.max(np.abs(np.diff(self.qpos, axis=0))))

    def extend(self, other: "Trajectory") -> "Trajectory":
        return Trajectory(kind=f"{self.kind}+{other.kind}",
                          qpos=np.vstack([self.qpos, other.qpos]),
                          xyz=np.vstack([self.xyz, other.xyz]),
                          meta={**self.meta, **other.meta})

    def bounded(self, limit: float = MAX_JOINT_STEP_RAD) -> None:
        step = self.max_joint_step()
        if step > limit:
            raise PlanError("JOINT_STEP_TOO_LARGE", f"相邻路点关节增量 {step:.3f} rad > {limit}")


def _xyz_of(sc: Scenario, qs: np.ndarray) -> np.ndarray:
    return np.array([fk(sc, q)[0] for q in np.atleast_2d(qs)])


def plan_joint(sc: Scenario, q_target: np.ndarray, max_step_rad: float = 0.08,
               kind: str = "joint") -> Trajectory:
    """关节空间插值。起点是当前位姿（允许空轨迹）。"""
    q0 = sc.arm_qpos()
    q_target = np.asarray(q_target, dtype=float)
    n = int(np.ceil(np.max(np.abs(q_target - q0)) / max_step_rad))
    if n <= 0:
        return Trajectory(kind=kind, qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)))
    qs = np.linspace(q0, q_target, n + 1)[1:]
    traj = Trajectory(kind=kind, qpos=qs, xyz=_xyz_of(sc, qs))
    traj.bounded()
    return traj


def plan_line(sc: Scenario, q_start: np.ndarray, target_xyz, max_step: float = 0.02,
              max_waypoints: int = 120, kind: str = "move_to") -> Trajectory:
    """从 q_start 出发，TCP 直线插值到 target_xyz，逐点逆解。"""
    target = np.asarray(target_xyz, dtype=float)
    if target.shape != (3,) or not np.all(np.isfinite(target)):
        raise PlanError("SCHEMA_INVALID", f"target 不合法: {target!r}")

    start = fk(sc, q_start)[0]
    dist = float(np.linalg.norm(target - start))
    n = max(1, int(np.ceil(dist / max_step)))
    if n > max_waypoints:
        raise PlanError("TRAJECTORY_TOO_LONG", f"需要 {n} 个路点，上限 {max_waypoints}")

    q = np.array(q_start, dtype=float)
    qs, xs = [], []
    for i in range(1, n + 1):
        p = start + (target - start) * (i / n)
        q_new, res = dls_ik(sc, p, q_init=q, target_rot=R_DOWN)
        if not np.isfinite(res) or res > 0.005:
            raise PlanError("IK_UNREACHABLE", f"waypoint {i}/{n} 残差 {res * 1000:.1f}mm")
        dq = float(np.max(np.abs(q_new - q)))
        if dq > MAX_JOINT_STEP_RAD:
            raise PlanError("IK_BRANCH_JUMP", f"waypoint {i}/{n} 关节跳变 {dq:.3f} rad")
        q = q_new
        qs.append(q.copy())
        xs.append(fk(sc, q)[0])

    traj = Trajectory(kind=kind, qpos=np.array(qs), xyz=np.array(xs),
                      meta={"requested": target, "start": start, "dist": dist})
    traj.bounded()
    return traj


def _ready_prefix(sc: Scenario) -> Trajectory:
    """当前姿态不在 READY 就先关节插值过去；已经在就返回空轨迹。"""
    if float(np.max(np.abs(sc.arm_qpos() - READY_ARM_QPOS))) < 0.05:
        return Trajectory(kind="at_ready", qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)))
    return plan_joint(sc, READY_ARM_QPOS, kind="ready")


def _concat(*trajs: Trajectory) -> Trajectory:
    out = Trajectory(kind="", qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)))
    for t in trajs:
        if len(t) == 0:
            continue
        out = t if len(out) == 0 else out.extend(t)
    if len(out) == 0:
        raise PlanError("EMPTY_TRAJECTORY", "没有任何路点")
    out.kind = "+".join(t.kind for t in trajs if len(t))
    out.bounded()
    return out


def plan_move(sc: Scenario, target_xyz, max_step: float = 0.02) -> Trajectory:
    """从 READY 出发直线移动到 target_xyz。"""
    prefix = _ready_prefix(sc)
    line = plan_line(sc, READY_ARM_QPOS, target_xyz, max_step=max_step)
    return _concat(prefix, line)


def plan_home(sc: Scenario) -> Trajectory:
    return plan_joint(sc, HOME_ARM_QPOS, max_step_rad=HOME_MAX_JOINT_STEP, kind="home")


def plan_ready(sc: Scenario) -> Trajectory:
    prefix = _ready_prefix(sc)
    if len(prefix) == 0:
        raise PlanError("EMPTY_TRAJECTORY", "已经在 READY")
    return prefix


def plan_pick(sc: Scenario, cube_xyz, standoff: float = 0.10,
              grasp_z: float = 0.005) -> Trajectory:
    """ready -> 悬停 -> 下探到抓取高度。夹爪由 executor 在末尾闭。"""
    cube = np.asarray(cube_xyz, dtype=float)
    above = cube + np.array([0.0, 0.0, standoff])
    prefix = _ready_prefix(sc)
    approach = plan_line(sc, READY_ARM_QPOS, above, kind="approach")
    q_above = approach.qpos[-1]
    descend = plan_line(sc, q_above, cube + np.array([0.0, 0.0, grasp_z]),
                        max_step=0.01, kind="descend")
    return _concat(prefix, approach, descend)


def plan_place(sc: Scenario, target_xyz, standoff: float = 0.06) -> Trajectory:
    """ready -> 悬停 -> 下探到放置高度。"""
    target = np.asarray(target_xyz, dtype=float)
    above = target + np.array([0.0, 0.0, standoff])
    prefix = _ready_prefix(sc)
    approach = plan_line(sc, READY_ARM_QPOS, above, kind="approach")
    q_above = approach.qpos[-1]
    descend = plan_line(sc, q_above, target, max_step=0.01, kind="descend")
    return _concat(prefix, approach, descend)


def retract(sc: Scenario, height: float = 0.10) -> Trajectory:
    """从当前位置垂直抬起，用于抓完之后离开。"""
    q0 = sc.arm_qpos()
    p0 = fk(sc, q0)[0]
    return plan_line(sc, q0, p0 + np.array([0.0, 0.0, height]), max_step=0.01, kind="retract")
