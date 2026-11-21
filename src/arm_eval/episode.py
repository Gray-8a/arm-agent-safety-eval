"""一集（episode）：裁决 -> 审批 -> 复校验 -> 执行 -> 成败判定 -> 落审计日志。

四个"必须"：
1. 执行的是 gate 校验过的那条轨迹（verdict.trajectory），不是重新规划一条。
2. 审批通过之后、执行之前再量一次距离（gate.recheck），同一条轨迹、现在的环境。
3. 急停每一步都查，包括复校验。
4. 走一步记一步，进哈希链日志 —— 不是事后拼出来的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import executor as ex
from .agent.schema import Action
from .audit.log import Ledger
from .gate.approval import ApprovalRequest, ApprovalStore
from .gate.estop import EStop
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
                on_approved: Callable[[int], None] | None = None,
                estop: EStop | None = None,
                ledger: Ledger | None = None,
                episode_id: str | None = None) -> EpisodeResult:
    if sc is None:
        sc = build.make(scene_id=case.scene, seed=case.seed, cube_xy=case.cube_xy)
    sc.reset()
    if store is None:
        store = ApprovalStore(ttl_s=gate.policy.approval_ttl_s, secret=b"episode-local-secret")

    ep_id = episode_id or case.id
    lg = ledger if ledger is not None else Ledger()

    def stopped() -> bool:
        return case.estop or (estop is not None and estop.is_set)

    lg.append(ep_id, "instruction_received", {
        "instruction": case.instruction,
        "scene": case.scene, "seed": case.seed,
        "cube_xy": None if case.cube_xy is None else list(case.cube_xy),
        "layer": case.layer,
    }, actor="operator")
    if case.estop:
        lg.append(ep_id, "estop_asserted", {"source": "case"}, actor="operator")

    out = EpisodeResult(case_id=case.id, layer=case.layer)
    for i, step in enumerate(case.steps):
        lg.append(ep_id, "model_raw_output", {"step": i, "raw": step.proposal}, actor="agent")
        verdict = gate.evaluate(step.proposal, sc, estop_active=stopped())
        lg.append(ep_id, "proposal_parsed", {
            "step": i, "reason_codes": list(verdict.reason_codes),
        }, actor="gate")
        lg.append(ep_id, "gate_verdict", {
            "step": i, "decision": verdict.decision.value,
            "reason_codes": list(verdict.reason_codes),
            "policy_hash": verdict.policy_hash,
            "estop": stopped(),          # 回放要能还原这个，否则 estop 用例对不上
            "evidence": verdict.evidence,
        }, actor="gate")

        sr = StepResult(index=i, proposal=step.proposal, verdict=verdict)

        if verdict.blocks:
            out.blocked_at = i
            out.steps.append(sr)
            break

        if verdict.needs_approval:
            outcome = store.ask(step.proposal, verdict, operator=operator, episode_id=ep_id)
            sr.approved = outcome.ok
            sr.approval_reason = outcome.reason
            if outcome.request is not None:
                lg.append(ep_id, "approval_requested", outcome.request.summary(), actor="gate")
            lg.append(ep_id, "approval_decided", {
                "step": i, "approved": outcome.ok, "reason": outcome.reason,
                "operator": getattr(outcome.token, "operator", None),
                "request_id": outcome.request.request_id if outcome.request else None,
            }, actor="operator")
            if not outcome.ok:
                out.blocked_at = i
                out.steps.append(sr)
                break

            if on_approved is not None:
                on_approved(i)

            again = gate.recheck(verdict.trajectory, sc, estop_active=stopped(),
                                 expected_start_q=verdict.evidence.get("plan_from_q"))
            sr.recheck = again
            lg.append(ep_id, "recheck_verdict", {
                "step": i, "decision": again.decision.value,
                "reason_codes": list(again.reason_codes),
                "evidence": again.evidence,
            }, actor="gate")
            if again.blocks:
                sr.halt_reason = again.reason_codes[0] if again.reason_codes else "STATE_CHANGED"
                out.blocked_at = i
                out.steps.append(sr)
                break

        if verdict.trajectory is None:
            out.steps.append(sr)
            continue

        lg.append(ep_id, "execution_started", {
            "step": i, "waypoints": len(verdict.trajectory),
            "target": verdict.evidence.get("target"),
        }, actor="executor")
        res = ex.execute_trajectory(sc, verdict.trajectory)
        sr.executed = True
        sr.exec_ok = res.ok
        sr.halt_reason = res.halt_reason
        sr.sim_steps = res.steps
        out.sim_steps += res.steps
        lg.append(ep_id, "execution_finished", {
            "step": i, "ok": res.ok, "sim_steps": res.steps,
            "halt_reason": res.halt_reason,
            "final_tcp": None if res.final_tcp is None else list(map(float, res.final_tcp)),
        }, actor="executor")

        _apply_grapple(sc, step.proposal)
        out.steps.append(sr)
        if not res.ok:
            break

    out.success, out.success_detail = check_outcome(case.check, sc)
    lg.append(ep_id, "final_state", {
        "success": out.success,
        "detail": out.success_detail,
        "cube_xyz": list(map(float, sc.cube_xyz())),
        "tcp": list(map(float, sc.tcp_pos())),
        "arm_qpos": list(map(float, sc.arm_qpos())),
        "attached": bool(sc.meta.get("attached")),
    }, actor="env")
    return out
