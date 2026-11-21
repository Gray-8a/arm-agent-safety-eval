"""离屏渲染与拼图。只用于出报告 / README 图，不进测试路径。"""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw

from ..scene.build import Scenario

DEFAULT_CAM = dict(azimuth=120.0, elevation=-14.0,
                   lookat=(0.28, 0.05, 0.38), distance=2.0)


def frame(sc: Scenario, width: int = 960, height: int = 720, **cam_kw) -> np.ndarray:
    _ = width, height
    params = {**DEFAULT_CAM, **cam_kw}
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.lookat[:] = params["lookat"]
    cam.distance = params["distance"]
    cam.azimuth = params["azimuth"]
    cam.elevation = params["elevation"]

    r = mujoco.Renderer(sc.model, height, width)
    try:
        r.update_scene(sc.data, camera=cam)
        return r.render().copy()
    finally:
        r.close()


def contact_sheet(images, labels, out_path, cols: int = 2, label_h: int = 26) -> Path:
    """拼成 cols 列的联系表，每格下面一行 ASCII 标签（默认字体不支持中文）。"""
    assert len(images) == len(labels)
    h, w = images[0].shape[:2]
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, (h + label_h) * rows), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    for i, (img, label) in enumerate(zip(images, labels)):
        r, c = divmod(i, cols)
        x, y = c * w, r * (h + label_h)
        sheet.paste(Image.fromarray(img), (x, y))
        draw.text((x + 8, y + h + 6), label, fill=(210, 210, 210))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def single(img, out_path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(out)
    return out
