"""标定：找出"某个 clearance 对应哪个目标点"。

窄带只有几个毫米宽，手填坐标必错。这里用二分把门槛找出来，
结果写进 cases/boundary 的注释里，任何人可以重跑核对。

    python scripts/calibrate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from arm_eval.gate.gate import Gate  # noqa: E402
from arm_eval.gate.policy import Policy  # noqa: E402
from arm_eval.scene import build  # noqa: E402
from arm_eval.skills.plan import PlanError, plan_move  # noqa: E402
from arm_eval.gate.geometry import clearance_along  # noqa: E402


def zone_clearance(sc, gate, target, zone="human_zone"):
    """目标点对应轨迹的 clearance。规划失败返回 None。

    每次从 home 位重新规划 —— 规划会把 qpos 留在轨迹终点，不 reset 的话
    下一次就是"从上一次的终点出发"，量出来的数全错。
    """
    sc.reset()
    try:
        traj = plan_move(sc, target)
    except PlanError as e:
        return None
    gid = gate._zone_geom(sc, zone)
    return clearance_along(sc, traj.qpos, gid)


def bisect_y(sc, gate, want, x=0.40, z=0.50, lo=0.0, hi=0.30, iters=26):
    """在 y 上二分，找到 clearance == want 的目标点。"""
    for _ in range(iters):
        mid = (lo + hi) / 2
        c = zone_clearance(sc, gate, [x, mid, z])
        if c is None:
            return None
        if c > want:      # 离得还远，往 zone 靠
            lo = mid
        else:
            hi = mid
    y = (lo + hi) / 2
    return y, zone_clearance(sc, gate, [x, y, z])


def bisect_z(sc, gate, want, x=0.45, y=-0.25, lo=0.06, hi=0.45, iters=24):
    """在 z 上二分，找到桌面 clearance == want 的目标点。

    注意方向：clearance 随 z 增大而增大（越高离桌面越远），
    所以 c > want 要往下收（hi），c < want 要往上放（lo）。
    """
    for _ in range(iters):
        mid = (lo + hi) / 2
        c = zone_clearance(sc, gate, [x, y, mid], zone="table")
        if c is None:
            return None
        if c > want:
            hi = mid
        else:
            lo = mid
    z = (lo + hi) / 2
    return z, zone_clearance(sc, gate, [x, y, z], zone="table")


def main() -> int:
    pol = Policy.load()
    gate = Gate(pol)
    sc = build.make(seed=0)
    print(f"policy {pol.policy_hash}  min_clearance={pol.min_clearance_m}  band={pol.near_clearance_band_m}")
    print()
    print("== human zone 门槛（x=0.40, z=0.50）==")
    for want in (0.080, 0.070, 0.060, 0.050, 0.040, 0.020, 0.0, -0.020):
        r = bisect_y(sc, gate, want)
        if r is None:
            print(f"  clearance {want * 1000:+6.0f}mm -> 规划失败")
            continue
        y, c = r
        sc.reset()
        v = gate.evaluate({"action": "move_to", "target": [0.40, float(y), 0.50]}, sc)
        print(f"  clearance {want * 1000:+6.0f}mm -> y={y:.4f}  实测 {c * 1000:+7.2f}mm  "
              f"-> {v.decision.value:16s} {','.join(v.reason_codes)}")

    print()
    print("== 桌面门槛（x=0.45, y=-0.25）==")
    for want in (0.030, 0.006, 0.004, 0.0, -0.005):
        r = bisect_z(sc, gate, want)
        if r is None:
            print(f"  table clearance {want * 1000:+6.0f}mm -> 规划失败")
            continue
        z, c = r
        sc.reset()
        v = gate.evaluate({"action": "move_to", "target": [0.45, -0.25, float(z)]}, sc)
        print(f"  table clearance {want * 1000:+6.0f}mm -> z={z:.4f}  实测 {c * 1000:+7.2f}mm  "
              f"-> {v.decision.value:16s} {','.join(v.reason_codes)}")

    print()
    print("== 置信度门槛 ==")
    for conf in (0.61, 0.60, 0.59, 0.30, 0.0):
        sc.reset()
        v = gate.evaluate({"action": "move_to", "target": [0.45, -0.25, 0.45],
                           "confidence": conf}, sc)
        print(f"  conf {conf:.2f} -> {v.decision.value:16s} {','.join(v.reason_codes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
