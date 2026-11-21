"""技能层：把「目标点」翻译成关节轨迹。

只做规划，不动 data 的持久状态（IK 内部的临时写入都会恢复）。
真正动仿真的一层是 executor.py。

三条刻意的约束：
1. 笛卡尔运动从固定 READY 姿态出发。否则 IK 会在等价解之间跳，
   TCP 不动、腕部翻 60°，而"每步 2cm"的假设就废了。
2. 从低位（接近桌面）回收时先垂直抬起，再关节插值。否则是在桌子边上横扫。
3. 相邻路点的关节增量 <= MAX_JOINT_STEP_RAD。超了直接 PlanError，
   不给执行器一个"看着像小步、实际大跳"的轨迹。

轨迹里区分关节段和笛卡尔段：关节段的步长用 rad 量，笛卡尔段用米量。
混着量会得到一个既不是速度也不是精度的假数字。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..scene.build import HOME_ARM_QPOS, Scenario
from ..scene.kin import R_DOWN, dls_ik, fk

# READY：工具朝下（R_DOWN），悬在桌面上方 0.25m。所有笛卡尔运动的起点。
READY_ARM_QPOS = np.array([-0.197568, -0.125771, -0.197568, -1.503294,
                           -0.024209, 1.380157, 0.386995])
READY_XYZ = np.array([0.45, -0.20, 0.60])

MAX_JOINT_STEP_RAD = 0.15
HOME_MAX_JOINT_STEP = 0.08
LIFT_Z_TRIGGER = 0.45      # TCP 低于这个高度就认为"在桌子附近"，先抬再动
LIFT_HEIGHT = 0.10


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
    is_joint: np.ndarray | None = None   # (N,) 该路点是否来自关节插值

    def __post_init__(self) -> None:
        if self.is_joint is None:
            self.is_joint = np.zeros(len(self.qpos), dtype=bool)
        else:
            self.is_joint = np.asarray(self.is_joint, dtype=bool)

    def __len__(self) -> int:
        return len(self.qpos)

    @property
    def target(self) -> np.ndarray:
        return self.xyz[-1]

    def max_cart_step(self) -> float:
        """只看笛卡尔段之间的步长。

        关节段（以及"笛卡尔段 -> 关节段"的过渡）不参与：那一段是按 rad 管的，
        按米量会得出一个既不是速度也不是精度的假数字。
        """
        if len(self) < 2:
            return 0.0
        skip = self.is_joint[:-1] | self.is_joint[1:]
        d = np.linalg.norm(np.diff(self.xyz, axis=0), axis=1)
        cart = d[~skip]
        return float(np.max(cart)) if cart.size else 0.0

    def max_joint_step(self) -> float:
        if len(self) < 2:
            return 0.0
        return float(np.max(np.abs(np.diff(self.qpos, axis=0))))

    def extend(self, other: "Trajectory") -> "Trajectory":
        return Trajectory(kind=f"{self.kind}+{other.kind}",
                          qpos=np.vstack([self.qpos, other.qpos]),
                          xyz=np.vstack([self.xyz, other.xyz]),
                          meta={**self.meta, **other.meta},
                          is_joint=np.concatenate([self.is_joint, other.is_joint]))

    def bounded(self, limit: float = MAX_JOINT_STEP_RAD) -> None:
        step = self.max_joint_step()
        if step > limit:
            raise PlanError("JOINT_STEP_TOO_LARGE", f"相邻路点关节增量 {step:.3f} rad > {limit}")


EMPTY = Trajectory(kind="empty", qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)))


def _xyz_of(sc: Scenario, qs: np.ndarray) -> np.ndarray:
    return np.array([fk(sc, q)[0] for q in np.atleast_2d(qs)])


def plan_joint(sc: Scenario, q_target: np.ndarray, max_step_rad: float = 0.08,
               kind: str = "joint", q_start: np.ndarray | None = None) -> Trajectory:
    """关节空间插值。"""
    q0 = sc.arm_qpos() if q_start is None else np.asarray(q_start, dtype=float)
    q_target = np.asarray(q_target, dtype=float)
    n = int(np.ceil(np.max(np.abs(q_target - q0)) / max_step_rad))
    if n <= 0:
        return Trajectory(kind=kind, qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)),
                          is_joint=np.zeros(0, dtype=bool))
    qs = np.linspace(q0, q_target, n + 1)[1:]
    traj = Trajectory(kind=kind, qpos=qs, xyz=_xyz_of(sc, qs),
                      is_joint=np.ones(len(qs), dtype=bool))
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
                      meta={"requested": target, "start": start, "dist": dist},
                      is_joint=np.zeros(len(qs), dtype=bool))
    traj.bounded()
    return traj


def _concat(*trajs: Trajectory) -> Trajectory:
    keep = [t for t in trajs if len(t)]
    if not keep:
        raise PlanError("EMPTY_TRAJECTORY", "没有任何路点")
    out = keep[0]
    for t in keep[1:]:
        out = out.extend(t)
    out.kind = "+".join(t.kind for t in keep)
    out.bounded()
    return out


def _lift(sc: Scenario, height: float = LIFT_HEIGHT) -> Trajectory:
    q = sc.arm_qpos()
    p = fk(sc, q)[0]
    return plan_line(sc, q, p + np.array([0.0, 0.0, height]), max_step=0.01, kind="lift")


def _ready_prefix(sc: Scenario) -> Trajectory:
    """不在 READY 就先回去。低位先垂直抬，再关节插值。"""
    if float(np.max(np.abs(sc.arm_qpos() - READY_ARM_QPOS))) < 0.05:
        return Trajectory(kind="at_ready", qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)),
                          is_joint=np.zeros(0, dtype=bool))

    parts: list[Trajectory] = []
    q = sc.arm_qpos()
    if fk(sc, q)[0][2] < LIFT_Z_TRIGGER:
        lift = _lift(sc)
        parts.append(lift)
        q = lift.qpos[-1]
    parts.append(plan_joint(sc, READY_ARM_QPOS, q_start=q, kind="ready"))
    return _concat(*parts)


def plan_move(sc: Scenario, target_xyz, max_step: float = 0.02) -> Trajectory:
    prefix = _ready_prefix(sc)
    q_start = prefix.qpos[-1] if len(prefix) else READY_ARM_QPOS
    line = plan_line(sc, q_start, target_xyz, max_step=max_step)
    if len(prefix) == 0:
        return line
    return _concat(prefix, line)


def plan_home(sc: Scenario) -> Trajectory:
    parts: list[Trajectory] = []
    q = sc.arm_qpos()
    if fk(sc, q)[0][2] < LIFT_Z_TRIGGER:
        lift = _lift(sc)
        parts.append(lift)
        q = lift.qpos[-1]
    parts.append(plan_joint(sc, HOME_ARM_QPOS, max_step_rad=HOME_MAX_JOINT_STEP,
                            kind="home", q_start=q))
    keep = [p for p in parts if len(p)]
    if not keep:
        # 已经在 home：返回空轨迹，让 gate 判定成 ALLOW/NO_MOTION，而不是报错
        return Trajectory(kind="home", qpos=np.zeros((0, 7)), xyz=np.zeros((0, 3)),
                          is_joint=np.zeros(0, dtype=bool))
    return _concat(*keep)


def plan_ready(sc: Scenario) -> Trajectory:
    prefix = _ready_prefix(sc)
    if len(prefix) == 0:
        raise PlanError("EMPTY_TRAJECTORY", "已经在 READY")
    return prefix


def plan_pick(sc: Scenario, cube_xyz, standoff: float = 0.10,
              grasp_z: float = 0.005) -> Trajectory:
    """ready -> 悬停 -> 下探到抓取高度。夹爪由 episode 在末尾闭。"""
    cube = np.asarray(cube_xyz, dtype=float)
    above = cube + np.array([0.0, 0.0, standoff])
    prefix = _ready_prefix(sc)
    q_start = prefix.qpos[-1] if len(prefix) else sc.arm_qpos()
    approach = plan_line(sc, q_start, above, kind="approach")
    descend = plan_line(sc, approach.qpos[-1], cube + np.array([0.0, 0.0, grasp_z]),
                        max_step=0.01, kind="descend")
    return _concat(prefix, approach, descend)


def plan_place(sc: Scenario, target_xyz, standoff: float = 0.06) -> Trajectory:
    """ready -> 悬停 -> 下探到放置高度。"""
    target = np.asarray(target_xyz, dtype=float)
    above = target + np.array([0.0, 0.0, standoff])
    prefix = _ready_prefix(sc)
    q_start = prefix.qpos[-1] if len(prefix) else sc.arm_qpos()
    approach = plan_line(sc, q_start, above, kind="approach")
    descend = plan_line(sc, approach.qpos[-1], target, max_step=0.01, kind="descend")
    return _concat(prefix, approach, descend)


def retract(sc: Scenario, height: float = 0.10) -> Trajectory:
    return _lift(sc, height)
