"""D1 收尾：一次完整 pick -> carry -> place，出四格图 + 落点误差。

    python scripts/demo_d1.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np

from arm_eval import executor as ex
from arm_eval.gate.geometry import min_distance_to_geom
from arm_eval.report.render import contact_sheet
from arm_eval.scene import build
from arm_eval.skills import plan


def main() -> int:
    sc = build.make(seed=0)
    shots, labels = [], []
    t0 = time.time()

    def snap(label: str, cam: dict | None = None) -> None:
        from arm_eval.report.render import frame
        shots.append(frame(sc, **(cam or {})))
        labels.append(label)

    clr = min_distance_to_geom(sc, sc.ids["zone_geom"])[0]
    snap(f"1 HOME  clearance to human zone {clr * 1000:.0f} mm")

    cube0 = sc.cube_xyz()
    traj = plan.plan_pick(sc, cube0)
    res = ex.execute_trajectory(sc, traj)
    print(f"pick   ok={res.ok} steps={res.steps} tcp={np.round(sc.tcp_pos(), 4)}")

    ex.set_gripper(sc, 0.0)
    ex.step_physics(sc, 150)
    ex.attach_cube(sc)
    snap("2 GRASP  fingers closed, cube attached")

    ex.execute_trajectory(sc, plan.retract(sc, 0.08))
    snap("3 CARRY  cube lifted, carried kinematically to TCP")

    target = np.array([0.42, -0.05, build.CUBE_SPAWN_Z])
    ex.execute_trajectory(sc, plan.plan_place(sc, target))

    ex.detach_cube(sc)
    ex.set_gripper(sc, 255.0)
    ex.step_physics(sc, 300)
    err = (np.array(sc.cube_xyz()) - target) * 1000
    print(f"place  err = ({err[0]:.1f}, {err[1]:.1f}, {err[2]:.1f}) mm")
    snap(f"4 PLACED  err {np.linalg.norm(err[:2]):.1f} mm in XY  ({time.time() - t0:.2f}s sim)")

    out = contact_sheet(shots, labels, ROOT / "reports" / "d1_pick_place.png")
    print(f"saved {out}")
    ok = np.linalg.norm(err[:2]) < 5.0
    print("D1 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
