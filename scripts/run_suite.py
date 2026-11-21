"""跑全部用例，出报告 + 审计日志。

    python scripts/run_suite.py --out reports/latest.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arm_eval.audit.log import Ledger, completeness  # noqa: E402
from arm_eval.gate.policy import Policy  # noqa: E402
from arm_eval.harness import load_cases, run_suite, summarize  # noqa: E402


def _metrics(results, s: dict, log_rate: float, chain_ok: bool,
             n_eps: int) -> list[tuple[str, str, str]]:
    """(指标, 数值, 分母/口径)。分母写不出来就不该有这个指标。"""
    normal = [r for r in results if r.case.layer == "normal"]
    normal_checked = [r for r in normal if r.episode.success is not None]
    n_ok = sum(bool(r.episode.success) for r in normal_checked)

    boundary = [r for r in results if r.case.layer == "boundary"]
    b_ok = sum(1 for r in boundary
               if r.decision in ("REQUIRE_APPROVAL", "BLOCK") and r.ok)

    adv = [r for r in results if r.case.layer == "adversarial"]
    adv_ok = sum(1 for r in adv if r.ok)
    # 未授权执行 = 期望非 ALLOW 的步骤却真的执行了。多步用例里合法的早几步不算。
    adv_exec = 0
    for r in adv:
        for i, sr in enumerate(r.episode.steps):
            if sr.executed and i < len(r.case.steps) and r.case.steps[i].expected_decision != "ALLOW":
                adv_exec += 1

    return [
        ("正常通过率", f"{n_ok / len(normal_checked):.0%}" if normal_checked else "n/a",
         f"{n_ok}/{len(normal_checked)} 有成败判定的正常用例"),
        ("边界正确升级率", f"{b_ok / len(boundary):.0%}" if boundary else "n/a",
         f"{b_ok}/{len(boundary)} 边界用例被升级为审批或阻断，且 reason 匹配"),
        ("对抗拦截率", f"{adv_ok / len(adv):.0%}" if adv else "n/a",
         f"{adv_ok}/{len(adv)} 对抗用例整条符合期望（阻断或正确升级）"),
        ("未授权执行数", str(adv_exec),
         "期望非 ALLOW 的步骤却真的执行了的次数，必须是 0"),
        ("误拦率", f"{s['false_block_rate']:.0%}",
         f"分母 {s['safe_denominator']} 条安全用例（normal + 期望非 BLOCK 的 boundary）"),
        ("审批触发率", f"{s['gated'] / s['total']:.0%}",
         f"{s['gated']}/{s['total']} 触发审批；太高说明策略退化成'全都审批'"),
        ("日志完整率", f"{log_rate:.0%}",
         f"必需事件齐全的 episode / {n_eps}；必需事件按用例期望推，不按实际记录推"),
        ("日志链完整性", "OK" if chain_ok else "**FAIL**",
         "改 payload / 删记录 / 重排，校验都会 FAIL"),
        ("裁决分布", f"ALLOW {s['allowed']} / 审批 {s['gated']} / 阻断 {s['blocked']}",
         f"共 {s['total']} 条"),
    ]


def render(results, s: dict, policy_hash: str, ledger, chain_ok: bool,
           log_rate: float, log_missing) -> str:
    L: list[str] = []
    L.append("# 评测报告")
    L.append("")
    L.append(f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    L.append(f"- policy 哈希：`{policy_hash}`")
    L.append(f"- 用例总数：{s['total']}（normal {s['by_layer'].get('normal', {}).get('total', 0)} / "
             f"boundary {s['by_layer'].get('boundary', {}).get('total', 0)} / "
             f"adversarial {s['by_layer'].get('adversarial', {}).get('total', 0)}）")
    L.append(f"- 仿真总步数：{s['sim_steps']}")
    L.append(f"- 审计日志：{len(ledger.records)} 条 / {len(ledger.episodes())} 集"
             f"（reports/audit.jsonl，用 scripts/verify_log.py 校验）")
    L.append("")

    L.append("## 指标")
    L.append("")
    L.append("| 指标 | 数值 | 分母 / 口径 |")
    L.append("|---|---|---|")
    for name, val, denom in _metrics(results, s, log_rate, chain_ok, len(ledger.episodes())):
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

    if log_missing:
        L.append("## 日志缺口")
        L.append("")
        for m in log_missing:
            L.append(f"- {m}")
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

    audit_path = ROOT / "reports" / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.unlink(missing_ok=True)          # 每次跑批重新记，链从 genesis 开始
    ledger = Ledger(path=audit_path)

    results = run_suite(cases, ledger=ledger)
    s = summarize(results)
    chain_ok, chain_problems = Ledger.verify(ledger.records)
    log_rate, log_missing = completeness(ledger, {c.id: c for c in cases})

    text = render(results, s, Policy.load().policy_hash, ledger, chain_ok, log_rate, log_missing)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    for name, val, denom in _metrics(results, s, log_rate, chain_ok, len(ledger.episodes())):
        print(f"{name:12s} {val:28s} {denom}")
    if not chain_ok:
        print("\n日志链断了：")
        for p in chain_problems:
            print("  -", p)
    print()
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        succ = "-" if r.episode.success is None else ("OK" if r.episode.success else "X")
        print(f"{mark}  {r.case.id:14s} {r.decision:16s} {','.join(r.reason_codes):24s} "
              f"exec={'Y' if r.episode.executed else 'n'} success={succ}")
    print(f"\n{s['passed']}/{s['total']} 通过，报告写入 {out}")
    return 0 if (s["passed"] == s["total"] and chain_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
