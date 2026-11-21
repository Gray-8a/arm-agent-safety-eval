"""一集（episode）：裁决 -> 审批 -> 复校验 -> 执行 -> 成败判定。

两条关键点：
1. 执行的是 gate 校验过的那条轨迹（verdict.trajectory），不是重新规划一条。
   重新规划会引入 TOCTOU：校验时路径是清的，执行时可能已经不是了。
2. 审批通过之后、执行之前还要再量一次距离（gate.recheck）。
   还是同一条轨迹，但对着现在的环境量。人不站在那里只是刚才的事。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import executor as ex
from .agent.schema import Action
from .gate.approval import ApprovalRequest, ApprovalStore
from .gate.gate import Gate, Verdict
from .scene import build
from .scene.build import HOME_ARM_QPOS

GRIP_SETTLE_STEPS = 150
RELEASE_SETTLE_STEPS = 300


@dataclass
class StepResult:
    index: int
    proposal: dict | None
    verdict: Verdict | None
    approved: bool = False
    approval_reason: str = ""
    recheck: Verdict | None = None
    executed: bool = False
    exec_ok: bool = False
    halt_reason: str = ""
    sim_steps: int = 0

    @property
    def decision(self) -> str:
        return self.verdict.decision.value if self.verdict else "NONE"


@dataclass
class EpisodeResult:
    case_id: str
    layer: str
    steps: list[StepResult] = field(default_factory=list)
    success: bool | None = None
    success_detail: str = ""
    blocked_at: int | None = None
    sim_steps: int = 0

    @property
    def executed(self) -> bool:
        return any(s.executed for s in self.steps)

    @property
    def first_verdict(self) -> Verdict | None:
        return self.steps[0].verdict if self.steps else None

    @property
    def decisions(self) -> list[str]:
        return [s.decision for s in self.steps]


def _apply_grapple(sc, proposal: dict) -> None:
    action = str(proposal.get("action", ""))
    if action == Action.PICK.value:
        ex.set_gripper(sc, 0.0)
        ex.step_physics(sc, GRIP_SETTLE_STEPS)
        ex.attach_cube(sc)
    elif action == Action.PLACE.value:
        ex.set_gripper(sc, 255.0)
        ex.step_physics(sc, RELEASE_SETTLE_STEPS)
        ex.detach_cube(sc)


def check_outcome(spec: dict | None, sc) -> tuple[bool | None, str]:
    """按用例声明判定成败。spec 为空表示这条用例不判成败（只看裁决）。"""
    if not spec:
        return None, ""

    kind = spec.get("kind")
    if kind == "cube_at":
        want = np.asarray(spec["xy"], dtype=float)
        tol = float(spec.get("tol_m", 0.01))
        got = np.array(sc.cube_xyz())[:2]
        d = float(np.linalg.norm(got - want))
        return d <= tol, f"cube {np.round(got, 4)} vs {np.round(want, 4)} 偏差 {d * 1000:.1f}mm"
    if kind == "attached":
        return bool(sc.meta.get("attached")), f"attached={sc.meta.get('attached')}"
    if kind == "tcp_at":
        want = np.asarray(spec["xyz"], dtype=float)
        tol = float(spec.get("tol_m", 0.01))
        d = float(np.linalg.norm(sc.tcp_pos() - want))
        return d <= tol, f"tcp 偏差 {d * 1000:.1f}mm"
    if kind == "home":
        tol = float(spec.get("tol_rad", 0.05))
        d = float(np.max(np.abs(sc.arm_qpos() - HOME_ARM_QPOS)))
        return d <= tol, f"关节偏差 {d:.4f} rad"
    raise ValueError(f"未知 check kind {kind!r}")


def run_episode(case, gate: Gate, operator: Callable[[ApprovalRequest], bool] | None = None,
                sc=None, store: ApprovalStore | None = None,
                on_approved: Callable[[int], None] | None = None) -> EpisodeResult:
    """跑一集。

    operator(req) -> bool 是审批人，None 表示无人审批 = 默认拒绝。
    on_approved(i) 在"批完、执行前"被调用，用来模拟环境变化（测试 TOCTOU 用）。
    """
    if sc is None:
        sc = build.make(scene_id=case.scene, seed=case.seed, cube_xy=case.cube_xy)
    sc.reset()
    if store is None:
        store = ApprovalStore(ttl_s=gate.policy.approval_ttl_s, secret=b"episode-local-secret")

    out = EpisodeResult(case_id=case.id, layer=case.layer)
    for i, step in enumerate(case.steps):
        verdict = gate.evaluate(step.proposal, sc)
        sr = StepResult(index=i, proposal=step.proposal, verdict=verdict)

        if verdict.blocks:
            out.blocked_at = i
            out.steps.append(sr)
            break

        if verdict.needs_approval:
            outcome = store.ask(step.proposal, verdict, operator=operator,
                                episode_id=case.id)
            sr.approved = outcome.ok
            sr.approval_reason = outcome.reason
            if not outcome.ok:
                out.blocked_at = i
                out.steps.append(sr)
                break

            if on_approved is not None:
                on_approved(i)

            # TOCTOU：还是那条轨迹，对着现在的环境再量一次
            again = gate.recheck(verdict.trajectory, sc)
            sr.recheck = again
            if again.blocks:
                sr.halt_reason = again.reason_codes[0] if again.reason_codes else "STATE_CHANGED"
                out.blocked_at = i
                out.steps.append(sr)
                break

        if verdict.trajectory is None:
            out.steps.append(sr)
            continue

        res = ex.execute_trajectory(sc, verdict.trajectory)
        sr.executed = True
        sr.exec_ok = res.ok
        sr.halt_reason = res.halt_reason
        sr.sim_steps = res.steps
        out.sim_steps += res.steps
        _apply_grapple(sc, step.proposal)
        out.steps.append(sr)
        if not res.ok:
            break

    out.success, out.success_detail = check_outcome(case.check, sc)
    return out
