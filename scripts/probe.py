"""探雷脚本：第一件事是确认模型能编译、能往前推、IK 能收敛。

    python scripts/probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from arm_eval.scene import build
from arm_eval.scene.kin import R_DOWN, dls_ik, fk


def main() -> int:
    sc = build.make(seed=0)
    m, d = sc.model, sc.data

    print(f"mujoco        {__import__('mujoco').__version__}")
    print(f"nq/nv/nu      {m.nq}/{m.nv}/{m.nu}")
    print(f"timestep      {m.opt.timestep}")
    print(f"arm qadr      {sc.ids['arm_qadr']}")
    print(f"arm geoms     {len(sc.ids['arm_geoms'])} 个参与距离计算")
    print(f"home qpos     {np.round(sc.arm_qpos(), 4)}")
    print(f"home tcp      {np.round(sc.tcp_pos(), 4)}")
    print(f"cube          {np.round(sc.cube_xyz(), 4)}")

    lo, hi = sc.joint_limits()
    print(f"joint range   lo={np.round(lo, 3)}")
    print(f"              hi={np.round(hi, 3)}")

    # 前推 100 步，看数值有没有炸
    for _ in range(100):
        d.ctrl[sc.ids["arm_act_ids"]] = sc.arm_qpos()
        d.ctrl[sc.ids["grip_act"]] = 255.0
        import mujoco
        mujoco.mj_step(m, d)
    print(f"after 100stp  tcp={np.round(sc.tcp_pos(), 4)} finite={np.all(np.isfinite(d.qpos))}")

    # 复位后 IK 打一个目标点
    sc.reset()
    target = np.array([0.45, -0.25, 0.50])
    q, res = dls_ik(sc, target, target_rot=R_DOWN)
    p, _ = fk(sc, q)
    print(f"ik -> {np.round(target, 4)}  residual={res * 1000:.2f}mm  fk={np.round(p, 4)}")
    print(f"ik qpos       {np.round(q, 4)}")

    # 距离检查
    from arm_eval.gate.geometry import min_distance_to_geom
    dist, g1, g2 = min_distance_to_geom(sc, sc.ids["zone_geom"])
    print(f"clearance     {dist * 1000:.1f}mm  (arm geom {g1} vs zone geom {g2})")

    sc.reset()
    print(f"reset hash    {sc.qpos_hash()}")
    sc.reset()
    print(f"reset hash    {sc.qpos_hash()}  (应与上一行相同)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
