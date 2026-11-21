"""校验审计日志的哈希链。

    python scripts/verify_log.py --log reports/audit.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arm_eval.audit.log import Ledger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="reports/audit.jsonl")
    args = ap.parse_args()

    path = ROOT / args.log
    records = Ledger.load(path)
    if not records:
        print(f"没有记录：{path}")
        return 1

    ok, problems = Ledger.verify(records)
    eps = {}
    for r in records:
        eps.setdefault(r["episode_id"], 0)
        eps[r["episode_id"]] += 1

    print(f"记录 {len(records)} 条，episode {len(eps)} 个，文件 {path}")
    print(f"链校验：{'OK' if ok else 'FAIL'}")
    for p in problems:
        print("  -", p)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
