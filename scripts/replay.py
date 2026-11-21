"""回放：从日志重跑每一条 gate 裁决，对决策和 reason code。

不重跑执行（仿真步数多、跨机器浮点有差异），只重跑**判断**。
判断才是这个项目的交付物。

    python scripts/replay.py --log reports/audit.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arm_eval.audit.log import Ledger  # noqa: E402
from arm_eval.gate.gate import Gate  # noqa: E402
from arm_eval.gate.policy import Policy  # noqa: E402
from arm_eval.scene import build  # noqa: E402


def episodes_of(records) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in records:
        ep = out.setdefault(r["episode_id"], {"meta": None, "raw": {}, "verdict": {}})
        if r["event"] == "instruction_received":
            ep["meta"] = r["payload"]
        elif r["event"] == "model_raw_output":
            ep["raw"][r["payload"]["step"]] = r["payload"]["raw"]
        elif r["event"] == "gate_verdict":
            ep["verdict"][r["payload"]["step"]] = r["payload"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="reports/audit.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    records = Ledger.load(ROOT / args.log)
    if not records:
        print(f"没有记录：{args.log}")
        return 1
    ok, problems = Ledger.verify(records)
    if not ok:
        print("日志链已断，拒绝回放：")
        for p in problems:
            print("  -", p)
        return 2

    gate = Gate(Policy.load())
    eps = episodes_of(records)
    ids = list(eps)[: args.limit or None]

    mismatches = 0
    for ep_id in ids:
        meta = eps[ep_id]["meta"]
        if not meta:
            print(f"{ep_id}: 没有 instruction_received，跳过")
            continue
        sc = build.make(scene_id=meta["scene"], seed=meta["seed"], cube_xy=meta["cube_xy"])
        sc.reset()
        for step in sorted(eps[ep_id]["raw"]):
            raw = eps[ep_id]["raw"][step]
            logged = eps[ep_id]["verdict"].get(step)
            if logged is None:
                mismatches += 1
                print(f"{ep_id} step{step}: 日志里没有 gate_verdict")
                continue
            q = (logged.get("evidence") or {}).get("plan_from_q")
            if q is not None:
                # gate 的裁决依赖当前姿态。不恢复状态就回放，第 2 步起必然对不上。
                sc.set_arm_qpos(np.asarray(q, dtype=float))
                mujoco.mj_forward(sc.model, sc.data)
            v = gate.evaluate(raw, sc, estop_active=bool(logged.get('estop', False)))
            if v.decision.value != logged["decision"]:
                mismatches += 1
                print(f"{ep_id} step{step}: 裁决变了 "
                      f"{logged['decision']} -> {v.decision.value}")
            elif list(v.reason_codes) != list(logged["reason_codes"]):
                mismatches += 1
                print(f"{ep_id} step{step}: reason 变了 "
                      f"{logged['reason_codes']} -> {list(v.reason_codes)}")

    print(f"\n回放 {len(ids)} 个 episode，不一致 {mismatches} 处")
    return 0 if mismatches == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
