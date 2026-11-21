"""策略门：LLM 之下的授权边界。

顺序固定：schema -> estop -> 工具 -> 工作空间 -> 规划 -> 几何 -> 风险。
每一级失败就停，不往下走（省时间，也避免用坏数据做后续判断）。

两条不变量：
1. fail-closed —— 这里抛任何异常，结果都是 BLOCK，不是 ALLOW，也不是 500。
2. 只读 —— gate 不改 data 的持久状态，也不执行。规划时 fk/IK 的临时写入都会恢复。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import mujoco

from ..agent.schema import Action, ActionProposal, parse_proposal
from ..scene.build import Scenario
from ..skills import plan as planner
from .geometry import clearance_along, min_distance_to_geom
from .policy import Policy


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class Reason(str, Enum):
    OK = "OK"
    SAFETY_ACTION = "SAFETY_ACTION"
    NO_MOTION = "NO_MOTION"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    ESTOP_ACTIVE = "ESTOP_ACTIVE"
    OUT_OF_WORKSPACE = "OUT_OF_WORKSPACE"
    JOINT_LIMIT = "JOINT_LIMIT"
    STEP_TOO_LARGE = "STEP_TOO_LARGE"
    HUMAN_ZONE = "HUMAN_ZONE"
    COLLISION = "COLLISION"
    NEAR_CLEARANCE = "NEAR_CLEARANCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    IK_UNREACHABLE = "IK_UNREACHABLE"
    IK_BRANCH_JUMP = "IK_BRANCH_JUMP"
    TRAJECTORY_TOO_LONG = "TRAJECTORY_TOO_LONG"
    CONFIG_ERROR = "CONFIG_ERROR"
    GATE_ERROR = "GATE_ERROR"
    # 审批链
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
    APPROVAL_REPLAY = "APPROVAL_REPLAY"
    # 审批通过之后、执行之前，环境变了
    STATE_CHANGED = "STATE_CHANGED"


PLAN_ERROR_MAP = {
    "IK_UNREACHABLE": Reason.IK_UNREACHABLE,
    "IK_BRANCH_JUMP": Reason.IK_BRANCH_JUMP,
    "TRAJECTORY_TOO_LONG": Reason.TRAJECTORY_TOO_LONG,
    "JOINT_STEP_TOO_LARGE": Reason.STEP_TOO_LARGE,
    "SCHEMA_INVALID": Reason.SCHEMA_INVALID,
}


@dataclass(frozen=True, eq=False)
class Verdict:
    decision: Decision
    reason_codes: tuple[str, ...]
    evidence: dict = field(default_factory=dict)
    policy_hash: str = ""
    trajectory: object = None   # gate 校验过的那条轨迹；执行它，不要重新规划

    @property
    def allows(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision is Decision.REQUIRE_APPROVAL

    @property
    def blocks(self) -> bool:
        return self.decision is Decision.BLOCK

    def may_execute(self, approved: bool = False) -> bool:
        """唯一的执行许可判定。审批只能把 REQUIRE_APPROVAL 放行，放不动 BLOCK。"""
        if self.decision is Decision.ALLOW:
            return True
        if self.decision is Decision.REQUIRE_APPROVAL:
            return bool(approved)
        return False


class Gate:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    # --- 对外 ---
    def evaluate(self, raw, sc: Scenario, *, estop_active: bool = False) -> Verdict:
        """只读地裁决一条提案。返回后 sc 的关节状态与调用前一致。"""
        saved_q = sc.arm_qpos()
        try:
            return self._evaluate(raw, sc, estop_active)
        except Exception as exc:  # noqa: BLE001 —— fail-closed 就在这里
            return self._verdict(Decision.BLOCK, Reason.GATE_ERROR,
                                 error=f"{type(exc).__name__}: {exc}")
        finally:
            # 规划过程会临时写 qpos，这里恢复，保证 gate 是纯函数式的
            sc.set_arm_qpos(saved_q)
            mujoco.mj_kinematics(sc.model, sc.data)
            mujoco.mj_comPos(sc.model, sc.data)

    # --- 内部 ---
    def _verdict(self, decision: Decision, *reasons: Reason,
                 trajectory=None, **evidence) -> Verdict:
        return Verdict(decision=decision,
                       reason_codes=tuple(r.value for r in reasons if r is not None),
                       evidence=evidence,
                       policy_hash=self.policy.policy_hash,
                       trajectory=trajectory)

    def _evaluate(self, raw, sc: Scenario, estop_active: bool) -> Verdict:
        p = self.policy

        # 1. schema
        proposal, err = parse_proposal(raw)
        if err is not None:
            return self._verdict(Decision.BLOCK, Reason(err))
        assert proposal is not None

        # 安全动作永远放行：急停状态下"停"不能被拦，否则没人能停
        if proposal.action is Action.STOP:
            return self._verdict(Decision.ALLOW, Reason.SAFETY_ACTION)
        if proposal.action is Action.OBSERVE:
            return self._verdict(Decision.ALLOW, Reason.NO_MOTION)

        # 2. 急停（带外通道，不看模型说什么）
        if estop_active:
            return self._verdict(Decision.BLOCK, Reason.ESTOP_ACTIVE,
                                 action=proposal.action.value)

        # 3. 工具白名单（第二道：部署时按场景收窄）
        if proposal.action.value not in p.allowed_actions:
            return self._verdict(Decision.BLOCK, Reason.TOOL_NOT_ALLOWED,
                                 action=proposal.action.value,
                                 allowed=sorted(p.allowed_actions))

        # 4. 工作空间
        if proposal.needs_target():
            tgt = proposal.target
            if not p.workspace.contains(tgt):
                return self._verdict(Decision.BLOCK, Reason.OUT_OF_WORKSPACE,
                                     target=list(tgt),
                                     workspace=[list(p.workspace.lo), list(p.workspace.hi)])

        # 5. 规划（这里会抛 PlanError，由下面统一映射）
        try:
            traj = self._plan(proposal, sc)
        except planner.PlanError as exc:
            return self._verdict(Decision.BLOCK, PLAN_ERROR_MAP.get(exc.code, Reason.GATE_ERROR),
                                 action=proposal.action.value,
                                 detail=exc.detail)

        if len(traj) == 0:
            # 已经在目标位姿上，没有要动的东西。放行，但记清楚是"没动"不是"动了没事"。
            return self._verdict(Decision.ALLOW, Reason.NO_MOTION,
                                 action=proposal.action.value)

        # 6. 步长 / 关节余量
        step = traj.max_cart_step()
        if step > p.max_step_m:
            return self._verdict(Decision.BLOCK, Reason.STEP_TOO_LARGE,
                                 max_cart_step_m=round(step, 5),
                                 limit=p.max_step_m)

        jstep = traj.max_joint_step()
        if jstep > p.max_waypoint_joint_delta_rad:
            return self._verdict(Decision.BLOCK, Reason.STEP_TOO_LARGE,
                                 max_joint_step_rad=round(jstep, 4),
                                 limit=p.max_waypoint_joint_delta_rad)

        q_lo, q_hi = sc.joint_limits()
        margin = p.joint_limit_margin_rad
        worst_q = traj.qpos[-1]
        if (worst_q < q_lo + margin).any() or (worst_q > q_hi - margin).any():
            return self._verdict(Decision.BLOCK, Reason.JOINT_LIMIT,
                                 q=list(map(float, worst_q)))

        # 7. 几何扫掠（整条轨迹，不是只看终点）
        measured = self._clearances(sc, traj)
        evidence = dict(
            action=proposal.action.value,
            target=None if proposal.target is None else [float(x) for x in proposal.target],
            waypoints=len(traj),
            max_cart_step_m=round(step, 5),
            clearances={z.name: round(c, 5) for z, c in measured},
            confidence=proposal.confidence,
        )

        near_zone: tuple[str, float] | None = None
        for zone, c in measured:
            if zone.is_exclusion:
                if c < zone.min_clearance_m:
                    return self._verdict(Decision.BLOCK, Reason.HUMAN_ZONE, trajectory=traj,
                                         breached=c <= 0.0, zone=zone.name, **evidence)
                if (c < zone.min_clearance_m + p.near_clearance_band_m) and near_zone is None:
                    near_zone = (zone.name, c)
            elif zone.is_obstacle:
                if c < zone.min_clearance_m:
                    return self._verdict(Decision.BLOCK, Reason.COLLISION, trajectory=traj,
                                         breached=c <= 0.0, zone=zone.name, **evidence)

        # 8. 风险分级
        warnings: list[Reason] = []
        if near_zone is not None:
            warnings.append(Reason.NEAR_CLEARANCE)
            evidence["near_zone"] = near_zone[0]
        if proposal.confidence < p.confidence_threshold:
            warnings.append(Reason.LOW_CONFIDENCE)
        if warnings:
            return self._verdict(Decision.REQUIRE_APPROVAL, *warnings, trajectory=traj, **evidence)

        return self._verdict(Decision.ALLOW, Reason.OK, trajectory=traj, **evidence)

    # --- 局部实现 ---
    def recheck(self, trajectory, sc: Scenario) -> Verdict:
        """审批通过之后、执行之前的复校验（TOCTOU）。

        只做一件事：拿**原来那条轨迹**对**现在**的环境再量一次距离。
        环境变了就拦。注意这里不重新规划 —— 重新规划等于换了一条没人批过的路径。
        """
        try:
            measured = self._clearances(sc, trajectory)
            evidence = dict(clearances={z.name: round(c, 5) for z, c in measured},
                            recheck=True, waypoints=len(trajectory))
            for zone, c in measured:
                if c < zone.min_clearance_m:
                    return self._verdict(Decision.BLOCK, Reason.STATE_CHANGED,
                                         trajectory=trajectory, zone=zone.name,
                                         breached=c <= 0.0, **evidence)
            return self._verdict(Decision.ALLOW, Reason.OK, trajectory=trajectory, **evidence)
        except Exception as exc:  # noqa: BLE001
            return self._verdict(Decision.BLOCK, Reason.GATE_ERROR,
                                 error=f"{type(exc).__name__}: {exc}")

    def _plan(self, proposal: ActionProposal, sc: Scenario) -> planner.Trajectory:
        step = min(0.020, self.policy.max_step_m)
        a = proposal.action
        if a is Action.HOME:
            return planner.plan_home(sc)
        if a is Action.MOVE_TO:
            return planner.plan_move(sc, proposal.target, max_step=step)
        if a is Action.PICK:
            return planner.plan_pick(sc, proposal.target, standoff=0.10, grasp_z=0.005)
        if a is Action.PLACE:
            return planner.plan_place(sc, proposal.target, standoff=0.06)
        raise planner.PlanError("SCHEMA_INVALID", f"没有 {a} 的规划器")

    def _zone_geom(self, sc: Scenario, zone_name: str) -> int:
        gid = mujoco.mj_name2id(sc.model, mujoco.mjtObj.mjOBJ_GEOM, f"{zone_name}_geom")
        if gid < 0:
            # 策略里写了场景里不存在的 zone —— 配置错，必须拦，不能当作"没有这个区"
            raise RuntimeError(f"policy 声明了 zone {zone_name!r}，但场景里没有对应 geom")
        return gid

    def _clearances(self, sc: Scenario, traj: planner.Trajectory) -> list[tuple]:
        """每个 zone 沿整条轨迹的最小带符号距离。"""
        out = []
        for zone in self.policy.zones:
            gid = self._zone_geom(sc, zone.name)
            out.append((zone, clearance_along(sc, traj.qpos, gid)))
        return out
