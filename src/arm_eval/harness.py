"""用例与跑批：把 YAML 用例喂给 gate，收结果。

一条用例 = 一个场景 + 一条指令 + 一份提案 + 一个期望裁决。
提案可以来自真模型（live），也可以是注入的（compromised-agent）。后者才是安全测试该用的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .gate.gate import Decision, Gate, Verdict
from .gate.policy import Policy
from .scene import build

ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = ROOT / "cases"
LAYERS = ("normal", "boundary", "adversarial")


@dataclass(frozen=True)
class Case:
    id: str
    layer: str
    scene: str
    seed: int
    instruction: str
    proposal: dict | None
    expected_decision: str
    expected_reasons: tuple[str, ...]
    may_execute: bool
    notes: str = ""
    path: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "Case":
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        exp = d.get("expected") or {}
        return cls(
            id=d["id"],
            layer=d.get("layer", path.parent.name),
            scene=d.get("scene", "table_panda_humanzone"),
            seed=int(d.get("seed", 0)),
            instruction=d.get("instruction", ""),
            proposal=d.get("proposal"),          # None = 由真模型生成
            expected_decision=exp["decision"],
            expected_reasons=tuple(exp.get("reason_codes", ())),
            may_execute=bool(exp.get("may_execute", exp["decision"] == "ALLOW")),
            notes=d.get("notes", ""),
            path=str(path),
        )


@dataclass
class CaseResult:
    case: Case
    verdict: Verdict
    ok: bool
    mismatches: list[str] = field(default_factory=list)

    @property
    def decision(self) -> str:
        return self.verdict.decision.value

    def to_row(self) -> dict:
        return dict(
            id=self.case.id, layer=self.case.layer,
            expected=self.case.expected_decision, actual=self.decision,
            reasons=",".join(self.verdict.reason_codes), ok=self.ok,
        )


def load_cases(case_dir: Path | str = CASE_DIR, layer: str | None = None) -> list[Case]:
    root = Path(case_dir)
    if not root.exists():
        return []
    files = sorted(root.rglob("*.yaml"))
    cases = [Case.from_yaml(f) for f in files]
    if layer is not None:
        cases = [c for c in cases if c.layer == layer]
    return cases


def run_case(case: Case, gate: Gate, sc=None) -> CaseResult:
    if case.proposal is None:
        raise ValueError(f"{case.id} 没有注入提案；live 模式需要真模型，见 D5")
    if sc is None:
        sc = build.make(scene_id=case.scene, seed=case.seed)
    sc.reset()
    verdict = gate.evaluate(case.proposal, sc)

    mism: list[str] = []
    if verdict.decision.value != case.expected_decision:
        mism.append(f"decision: 期望 {case.expected_decision} 实得 {verdict.decision.value}")
    for r in case.expected_reasons:
        if r not in verdict.reason_codes:
            mism.append(f"缺 reason {r}")
    if case.expected_decision == "BLOCK" and verdict.may_execute(approved=True):
        mism.append("BLOCK 的裁决居然可以被审批放行")
    return CaseResult(case=case, verdict=verdict, ok=not mism, mismatches=mism)


def run_suite(cases, policy: Policy | None = None) -> list[CaseResult]:
    gate = Gate(policy or Policy.load())
    sc = build.make(seed=0)
    return [run_case(c, gate, sc) for c in cases]


def summarize(results) -> dict:
    """按层统计。分母写清楚，不然数字好看但没意义。"""
    by_layer: dict[str, dict] = {}
    for layer in LAYERS:
        rows = [r for r in results if r.case.layer == layer]
        if not rows:
            continue
        by_layer[layer] = dict(total=len(rows), passed=sum(r.ok for r in rows))
    total = len(results)
    return dict(
        total=total,
        passed=sum(r.ok for r in results),
        by_layer=by_layer,
        blocked=sum(r.verdict.decision is Decision.BLOCK for r in results),
        gated=sum(r.verdict.decision is Decision.REQUIRE_APPROVAL for r in results),
        allowed=sum(r.verdict.decision is Decision.ALLOW for r in results),
    )
