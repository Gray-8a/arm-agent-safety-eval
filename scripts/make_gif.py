"""把一次成功的抓取做成 GIF。

只做"通过"的那一段 —— 被拦的场景是静止的，做成动图没有信息量。

    python scripts/make_gif.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image

from arm_eval import executor as ex
from arm_eval.gate.gate import Gate
from arm_eval.gate.policy import Policy
from arm_eval.report.render import frame
from arm_eval.scene import build

W, H = 480, 360
EVERY_N_TICKS = 8
PICK = [0.45, -0.25, 0.3705]
PLACE = [0.42, -0.05, 0.3705]


def main() -> int:
    gate = Gate(Policy.load())
    sc = build.make(seed=0)
    frames = []

    def grab(_i, _p):
        if len(frames) and len(frames) % 1 == 0:
            pass
        frames.append(frame(sc, width=W, height=H))

    tick = {"n": 0}

    def on_tick(_i, _p):
        tick["n"] += 1
        if tick["n"] % EVERY_N_TICKS == 0:
            frames.append(frame(sc, width=W, height=H))

    v = gate.evaluate({"action": "pick", "target": PICK, "confidence": 0.95}, sc)
    assert v.allows, v.reason_codes
    sc.set_marker(PICK)
    ex.execute_trajectory(sc, v.trajectory, on_tick=on_tick)
    ex.set_gripper(sc, 0.0)
    ex.step_physics(sc, 150)
    ex.attach_cube(sc)

    v2 = gate.evaluate({"action": "place", "target": PLACE, "confidence": 0.95}, sc)
    assert v2.allows, v2.reason_codes
    sc.set_marker(PLACE)
    ex.execute_trajectory(sc, v2.trajectory, on_tick=on_tick)
    ex.detach_cube(sc)
    ex.step_physics(sc, 200)
    frames.append(frame(sc, width=W, height=H))

    imgs = [Image.fromarray(f) for f in frames]
    out = ROOT / "reports" / "demo_pick_place.gif"
    out.parent.mkdir(parents=True, exist_ok=True)
    # 12.5 fps ≈ 每 8 个控制 tick 一帧（控制 50Hz）
    imgs[0].save(out, save_all=True, append_images=imgs[1:], duration=80, loop=0,
                 optimize=True)
    print(f"{len(imgs)} 帧 -> {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
