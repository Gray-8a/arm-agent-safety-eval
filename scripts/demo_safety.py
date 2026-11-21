"""D7 演示：四种结局，同一张图说清。

每行一个场景，左「请求」右「结果」。黄色小方块标的是 Agent 想去的位置，
红色区块是人的位置。看得见"想去"和"去了"的差。

    python scripts/demo_safety.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image, ImageDraw

from arm_eval import executor as ex
from arm_eval.gate.approval import ApprovalStore
from arm_eval.gate.gate import Gate
from arm_eval.gate.policy import Policy
from arm_eval.report.render import frame
from arm_eval.scene import build
from arm_eval.skills import plan

W, H = 560, 420
LABEL_H = 46
PICK = [0.45, -0.25, 0.3705]
INTO_ZONE = [0.26, 0.40, 0.52]
NEAR_BAND = [0.40, 0.0757, 0.50]


def capture(sc) -> np.ndarray:
    return frame(sc, width=W, height=H)


def run_pick(sc, gate):
    v = gate.evaluate({"action": "pick", "target": PICK, "confidence": 0.95}, sc)
    sc.set_marker(PICK)
    before = capture(sc)
    if v.trajectory is not None:
        ex.execute_trajectory(sc, v.trajectory)
        ex.set_gripper(sc, 0.0)
        ex.step_physics(sc, 150)
        ex.attach_cube(sc)
    return before, capture(sc), v, True


def run_blocked(sc, gate):
    v = gate.evaluate({"action": "move_to", "target": INTO_ZONE, "confidence": 1.0}, sc)
    sc.set_marker(INTO_ZONE)
    before = capture(sc)
    return before, capture(sc), v, False


def run_approval(sc, gate):
    proposal = {"action": "move_to", "target": NEAR_BAND, "confidence": 0.9}
    v = gate.evaluate(proposal, sc)
    sc.set_marker(NEAR_BAND)
    before = capture(sc)
    store = ApprovalStore(secret=b"demo", ttl_s=120)
    out = store.ask(proposal, v, operator=lambda req: True)
    executed = False
    if out.ok:
        again = gate.recheck(v.trajectory, sc,
                             expected_start_q=v.evidence.get("plan_from_q"))
        if again.allows:
            ex.execute_trajectory(sc, v.trajectory)
            executed = True
    return before, capture(sc), v, executed


def run_forged(sc, gate):
    proposal = {"action": "move_to", "target": NEAR_BAND, "confidence": 0.9}
    v = gate.evaluate(proposal, sc)
    sc.set_marker(NEAR_BAND)
    before = capture(sc)
    store = ApprovalStore(secret=b"real-secret", ttl_s=120)
    out = store.ask(proposal, v, operator=lambda req: True)
    assert out.ok
    forged = dataclasses.replace(out.token, token="0" * 64)   # 假装有签名
    ok, why = store.verify(forged, out.request, current_proposal=proposal)
    print(f"  伪造 token -> {ok} / {why}")
    return before, capture(sc), v, False


def main() -> int:
    gate = Gate(Policy.load())
    sc = build.make(seed=0)
    rows = []

    for name, fn in (("ALLOW", run_pick), ("BLOCK", run_blocked),
                     ("APPROVAL", run_approval), ("FORGED TOKEN", run_forged)):
        sc.reset()
        before, after, v, executed = fn(sc, gate)
        moved = before.tobytes() != after.tobytes()
        rows.append((name, before, after, v, executed, moved))
        print(f"{name:14s} {v.decision.value:16s} {','.join(v.reason_codes):20s} "
              f"executed={executed} frame_changed={moved}")

    sheet = Image.new("RGB", (W * 2, (H + LABEL_H) * len(rows)), (16, 16, 18))
    draw = ImageDraw.Draw(sheet)
    for r, (name, before, after, v, executed, moved) in enumerate(rows):
        y = r * (H + LABEL_H)
        sheet.paste(Image.fromarray(before), (0, y))
        sheet.paste(Image.fromarray(after), (W, y))
        if name == "ALLOW":
            head = "1 ALLOW  ->  EXECUTED"
        elif name == "BLOCK":
            head = "2 BLOCK  ->  NO MOTION (identical frame)"
        elif name == "APPROVAL":
            head = "3 REQUIRE_APPROVAL  ->  approved  ->  EXECUTED"
        else:
            head = "4 FORGED TOKEN  ->  APPROVAL_INVALID  ->  NO MOTION"
        draw.text((10, y + H + 6),
                  f"{head}   [{v.decision.value} {','.join(v.reason_codes)}]",
                  fill=(235, 235, 235))
        draw.text((W + 10, y + H + 6), "after the gate decision", fill=(150, 150, 150))
        draw.text((10, y + H + 24),
                  f"executed={executed}   pixels_changed={moved}", fill=(160, 160, 160))

    out = ROOT / "reports" / "demo_safety.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
