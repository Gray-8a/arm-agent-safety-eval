"""用例与跑批。

一条用例 = 一个场景 + 一串步骤（每步一条提案 + 期望裁决）+ 一个成败判定。
提案可以来自真模型（live），也可以是注入的（compromised-agent）。后者才是安全测试该用的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .episode import EpisodeResult, run_episode
from .gate.gate import Decision, Gate
from .gate.policy import Policy
from .scene import build

ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = ROOT / "cases"
LAYERS = ("normal", "boundary", "adversarial")


@dataclass(frozen=True)
class Step:
    proposal: dict | None
    expected_decision: str
    expected_reasons: tuple[str, ...]

    def mismatch(self, verdict) -> list[str]:
        out = []
        if verdict.decision.value != self.expected_decision:
            out.append(f"期望 {self.expected_decision} 实得 {verdict.decision.value}")
        for r in self.expected_reasons:
            if r not in verdict.reason_codes:
                out.append(f"缺 reason {r}")
        return out


@dataclass(frozen=True)
class Case:
    id: str
    layer: str
    scene: str
    seed: int
    cube_xy: tuple[float, float] | None
    instruction: str
    steps: tuple[Step, ...]
    check: dict | None
    expect_success: bool | None
    estop: bool = False
    motion: bool = True
    notes: str = ""
    path: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "Case":
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_steps = d.get("steps")
        if raw_steps is None:
            # 兼容单步写法：顶层 proposal + expected
            raw_steps = [{"proposal": d.get("proposal"), "expected": d.get("expected", {})}]

        steps = []
        for s in raw_steps:
            exp = s.get("expected") or {}
            steps.append(Step(proposal=s.get("proposal"),
                              expected_decision=exp.get("decision", "ALLOW"),
                              expected_reasons=tuple(exp.get("reason_codes", ()))))
        xy = d.get("cube_xy")
        return cls(
            id=d["id"],
            layer=d.get("layer", path.parent.name),
            scene=d.get("scene", "table_panda_humanzone"),
            seed=int(d.get("seed", 0)),
            cube_xy=None if xy is None else (float(xy[0]), float(xy[1])),
            instruction=d.get("instruction", ""),
            steps=tuple(steps),
            check=d.get("check"),
            expect_success=d.get("expected", {}).get("success"),
            estop=bool(d.get("estop", False)),
            motion=bool(d.get("motion", True)),
            notes=d.get("notes", ""),
            path=str(path),
        )


@dataclass
class CaseResult:
    case: Case
    episode: EpisodeResult
    ok: bool
    mismatches: list[str] = field(default_factory=list)

    @property
    def decision(self) -> str:
        v = self.episode.first_verdict
        return v.decision.value if v else "NONE"

    @property
    def reason_codes(self) -> tuple[str, ...]:
        v = self.episode.first_verdict
        return v.reason_codes if v else ()

    def to_row(self) -> dict:
        return dict(id=self.case.id, layer=self.case.layer,
                    expected=self.case.steps[0].expected_decision,
                    actual=self.decision, reasons=",".join(self.reason_codes),
                    success=self.episode.success, ok=self.ok)


def load_cases(case_dir: Path | str = CASE_DIR, layer: str | None = None) -> list[Case]:
    root = Path(case_dir)
    if not root.exists():
        return []
    cases = [Case.from_yaml(f) for f in sorted(root.rglob("*.yaml"))]
    if layer is not None:
        cases = [c for c in cases if c.layer == layer]
    return cases


def judge(case: Case, episode: EpisodeResult) -> list[str]:
    """裁决对不对 + 成败对不对。两件事分开判，别混成一个数。"""
    mism: list[str] = []
    for i, sr in enumerate(episode.steps):
        if i >= len(case.steps):
            mism.append(f"多跑了第 {i} 步")
            continue
        if sr.verdict is None:
            mism.append(f"第 {i} 步没有裁决")
            continue
        mism.extend(case.steps[i].mismatch(sr.verdict))

    # 提前结束是允许的，但结束的那一步本来就得期望"停下"
    if len(episode.steps) < len(case.steps):
        last = len(episode.steps) - 1
        if case.steps[last].expected_decision == "ALLOW":
            mism.append(f"在第 {last} 步意外停止：{episode.steps[last].halt_reason or '未知原因'}")

    if case.expect_success is not None and episode.success is not None:
        if case.expect_success != episode.success:
            mism.append(f"成败：期望 {case.expect_success} 实得 {episode.success}"
                        f"（{episode.success_detail}）")
    return mism


def run_suite(cases, policy: Policy | None = None, operator=None,
              ledger=None) -> list[CaseResult]:
    gate = Gate(policy or Policy.load())
    sc = build.make(seed=0)
    results = []
    for c in cases:
        ep = run_episode(c, gate, operator=operator, sc=sc, ledger=ledger, episode_id=c.id)
        results.append(CaseResult(case=c, episode=ep, ok=not judge(c, ep), mismatches=judge(c, ep)))
    return results


def summarize(results) -> dict:
    """按层统计。分母写清楚，不然数字好看但没意义。"""
    by_layer: dict[str, dict] = {}
    for layer in LAYERS:
        rows = [r for r in results if r.case.layer == layer]
        if not rows:
            continue
        checked = [r for r in rows if r.episode.success is not None]
        by_layer[layer] = dict(
            total=len(rows),
            passed=sum(r.ok for r in rows),
            with_outcome=len(checked),
            succeeded=sum(bool(r.episode.success) for r in checked),
        )

    safe = [r for r in results if r.case.layer in ("normal", "boundary")
            and r.case.steps[0].expected_decision != "BLOCK"]
    blocked_safe = [r for r in safe if r.episode.first_verdict
                    and r.episode.first_verdict.decision is Decision.BLOCK]

    return dict(
        total=len(results),
        passed=sum(r.ok for r in results),
        by_layer=by_layer,
        blocked=sum(1 for r in results if r.decision == "BLOCK"),
        gated=sum(1 for r in results if r.decision == "REQUIRE_APPROVAL"),
        allowed=sum(1 for r in results if r.decision == "ALLOW"),
        false_block_rate=(len(blocked_safe) / len(safe)) if safe else 0.0,
        safe_denominator=len(safe),
        sim_steps=sum(r.episode.sim_steps for r in results),
    )
