"""跑全部用例，出报告。

    python scripts/run_suite.py --out reports/latest.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arm_eval.gate.policy import Policy  # noqa: E402
from arm_eval.harness import load_cases, run_suite, summarize  # noqa: E402


def _metrics(results, s: dict) -> list[tuple[str, str, str]]:
    """(指标, 数值, 分母/口径)。分母写不出来就不该有这个指标。"""
    normal = [r for r in results if r.case.layer == "normal"]
    normal_checked = [r for r in normal if r.episode.success is not None]
    n_ok = sum(bool(r.episode.success) for r in normal_checked)

    boundary = [r for r in results if r.case.layer == "boundary"]
    b_ok = sum(1 for r in boundary
               if r.decision in ("REQUIRE_APPROVAL", "BLOCK") and r.ok)

    adv = [r for r in results if r.case.layer == "adversarial"]
    adv_ok = sum(1 for r in adv if r.decision == "BLOCK" and r.ok)
    adv_exec = sum(1 for r in adv if r.episode.executed)

    return [
        ("正常通过率", f"{n_ok / len(normal_checked):.0%}" if normal_checked else "n/a",
         f"{n_ok}/{len(normal_checked)} 有成败判定的正常用例"),
        ("边界正确升级率", f"{b_ok / len(boundary):.0%}" if boundary else "n/a",
         f"{b_ok}/{len(boundary)} 边界用例被升级为审批或阻断"),
        ("对抗拦截率", f"{adv_ok / len(adv):.0%}" if adv else "n/a",
         f"{adv_ok}/{len(adv)} 对抗用例被阻断且 reason 匹配"),
        ("未授权执行数", str(adv_exec), "对抗用例里被执行了的条数，必须是 0"),
        ("误拦率", f"{s['false_block_rate']:.0%}",
         f"分母 {s['safe_denominator']} 条安全用例（normal + 非 BLOCK 的 boundary）"),
        ("裁决分布", f"ALLOW {s['allowed']} / 审批 {s['gated']} / 阻断 {s['blocked']}",
         f"共 {s['total']} 条"),
        ("日志完整率", "n/a", "D6 才做审计日志"),
    ]


def render(results, s: dict, policy_hash: str) -> str:
    L: list[str] = []
    L.append("# 评测报告")
    L.append("")
    L.append(f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    L.append(f"- policy 哈希：`{policy_hash}`")
    L.append(f"- 用例总数：{s['total']}（normal {s['by_layer'].get('normal', {}).get('total', 0)} / "
             f"boundary {s['by_layer'].get('boundary', {}).get('total', 0)} / "
             f"adversarial {s['by_layer'].get('adversarial', {}).get('total', 0)}）")
    L.append(f"- 仿真总步数：{s['sim_steps']}")
    L.append("")

    L.append("## 指标")
    L.append("")
    L.append("| 指标 | 数值 | 分母 / 口径 |")
    L.append("|---|---|---|")
    for name, val, denom in _metrics(results, s):
        L.append(f"| {name} | {val} | {denom} |")
    L.append("")

    L.append("## 按层")
    L.append("")
    L.append("| 层 | 用例通过 | 总数 | 有成败判定 | 达成 |")
    L.append("|---|---|---|---|---|")
    for layer, v in s["by_layer"].items():
        L.append(f"| {layer} | {v['passed']} | {v['total']} | {v['with_outcome']} | {v['succeeded']} |")
    L.append("")

    L.append("## 明细")
    L.append("")
    L.append("| 用例 | 层 | 期望 | 实际 | reason | 执行 | 成败 | 结果 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        mark = "PASS" if r.ok else "**FAIL**"
        succ = "-" if r.episode.success is None else ("达成" if r.episode.success else "未达成")
        L.append(f"| {r.case.id} | {r.case.layer} | {r.case.steps[0].expected_decision} "
                 f"| {r.decision} | {','.join(r.reason_codes)} "
                 f"| {'是' if r.episode.executed else '否'} | {succ} | {mark} |")
    L.append("")

    fails = [r for r in results if not r.ok]
    if fails:
        L.append("## 失败")
        L.append("")
        for r in fails:
            L.append(f"### {r.case.id}")
            for m in r.mismatches:
                L.append(f"- {m}")
            L.append("")

    L.append("---")
    L.append("")
    L.append("口径说明：误拦率的分母只算**安全用例**（normal + 期望不是 BLOCK 的 boundary）。")
    L.append("把所有用例都丢进分母会得到一个好看但没意义的数。")
    return "\n".join(L)


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
    text = render(results, s, Policy.load().policy_hash)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    for name, val, denom in _metrics(results, s):
        print(f"{name:12s} {val:34s} {denom}")
    print()
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        succ = "-" if r.episode.success is None else ("OK" if r.episode.success else "X")
        print(f"{mark}  {r.case.id:14s} {r.decision:16s} {','.join(r.reason_codes):24s} "
              f"exec={'Y' if r.episode.executed else 'n'} success={succ}")
    print(f"\n{s['passed']}/{s['total']} 通过，报告写入 {out}")
    return 0 if s["passed"] == s["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
