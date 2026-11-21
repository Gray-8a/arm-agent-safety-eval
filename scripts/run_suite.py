"""跑全部用例，出报告。

    python scripts/run_suite.py --out reports/latest.md
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arm_eval.harness import load_cases, run_suite, summarize  # noqa: E402
from arm_eval.gate.gate import Decision  # noqa: E402


def render(results, s: dict, policy_hash: str) -> str:
    lines: list[str] = []
    lines.append("# 评测报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"- policy 哈希：`{policy_hash}`")
    lines.append(f"- 用例总数：{s['total']}")
    lines.append("")

    lines.append("## 按层")
    lines.append("")
    lines.append("| 层 | 通过 | 总数 | 通过率 |")
    lines.append("|---|---|---|---|")
    for layer, v in s["by_layer"].items():
        rate = v["passed"] / v["total"]
        lines.append(f"| {layer} | {v['passed']} | {v['total']} | {rate:.0%} |")
    lines.append("")

    lines.append("## 裁决分布")
    lines.append("")
    lines.append(f"- ALLOW：{s['allowed']}")
    lines.append(f"- REQUIRE_APPROVAL：{s['gated']}")
    lines.append(f"- BLOCK：{s['blocked']}")
    lines.append("")

    lines.append("## 明细")
    lines.append("")
    lines.append("| 用例 | 层 | 期望 | 实际 | reason | 结果 |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        mark = "PASS" if r.ok else "**FAIL**"
        lines.append(f"| {r.case.id} | {r.case.layer} | {r.case.expected_decision} "
                     f"| {r.decision} | {','.join(r.verdict.reason_codes)} | {mark} |")
    lines.append("")

    fails = [r for r in results if not r.ok]
    if fails:
        lines.append("## 失败")
        lines.append("")
        for r in fails:
            lines.append(f"### {r.case.id}")
            for m in r.mismatches:
                lines.append(f"- {m}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("注：这是 D2 阶段的**部分**指标。完整的五指标（正常通过率 / 边界拦截率 /")
    lines.append("对抗拦截率 / 误拦率 / 日志完整率）要等 D3–D6 用例与日志层补齐。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/latest.md")
    ap.add_argument("--layer", default=None)
    args = ap.parse_args()

    cases = load_cases()
    if args.layer:
        cases = [c for c in cases if c.layer == args.layer]
    if not cases:
        print("没有用例")
        return 1

    results = run_suite(cases)
    s = summarize(results)
    text = render(results, s, _policy_hash())

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"{mark}  {r.case.id:24s} {r.decision:16s} {','.join(r.verdict.reason_codes)}")
    print(f"\n{s['passed']}/{s['total']} 通过，报告写入 {out}")
    return 0 if s["passed"] == s["total"] else 1


def _policy_hash() -> str:
    from arm_eval.gate.policy import Policy

    return Policy.load().policy_hash


if __name__ == "__main__":
    raise SystemExit(main())
