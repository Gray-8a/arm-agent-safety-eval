"""一集（episode）：裁决 -> 审批 -> 执行 -> 成败判定。

关键点：执行的是 gate 校验过的那条轨迹（verdict.trajectory），不是重新规划一条。
重新规划会引入 TOCTOU：校验时路径是清的，执行时可能已经不是了。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import executor as ex
from .agent.schema import Action
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
    """抓/放的收尾动作。属于"动作语义"，不归 gate 管。"""
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


def run_episode(case, gate: Gate, operator=None, sc=None) -> EpisodeResult:
    """跑一集。operator(proposal, verdict) -> bool 是审批人，None 表示无人审批=默认拒。"""
    if sc is None:
        sc = build.make(scene_id=case.scene, seed=case.seed, cube_xy=case.cube_xy)
    sc.reset()

    out = EpisodeResult(case_id=case.id, layer=case.layer)
    for i, step in enumerate(case.steps):
        verdict = gate.evaluate(step.proposal, sc)
        sr = StepResult(index=i, proposal=step.proposal, verdict=verdict)

        if verdict.blocks:
            out.blocked_at = i
            out.steps.append(sr)
            break

        if verdict.needs_approval:
            approved = bool(operator(step.proposal, verdict)) if operator is not None else False
            sr.approved = approved
            if not approved:
                out.steps.append(sr)
                break

        if verdict.trajectory is None:
            # ALLOW 但没有轨迹 = 无事可做（例如已经在 home）。不执行，不算失败。
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
