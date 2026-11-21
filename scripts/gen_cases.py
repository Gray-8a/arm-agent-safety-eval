"""生成 normal 层的 20 条用例。

手写 20 个 YAML 容易抄错，用脚本生成反而更可靠 —— 参数集中在一张表里，
改一个数就重生成一遍。脚本入库，生成的 YAML 也入库（reviewer 能直接读）。

    python scripts/gen_cases.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cases" / "normal"
GRASP_Z = 0.3705
CONF = 0.95


def pick(cube_xy):
    return dict(proposal=dict(action="pick", target=[cube_xy[0], cube_xy[1], GRASP_Z],
                              confidence=CONF),
                expected=dict(decision="ALLOW", reason_codes=["OK"]))


def place(xy):
    return dict(proposal=dict(action="place", target=[xy[0], xy[1], GRASP_Z],
                              confidence=CONF),
                expected=dict(decision="ALLOW", reason_codes=["OK"]))


def move_to(xyz):
    return dict(proposal=dict(action="move_to", target=list(xyz), confidence=CONF),
                expected=dict(decision="ALLOW", reason_codes=["OK"]))


def go_home(reasons=("OK",)):
    return dict(proposal=dict(action="home", confidence=CONF),
                expected=dict(decision="ALLOW", reason_codes=list(reasons)))


def case(cid, instruction, cube_xy, steps, check, success, notes=""):
    return dict(id=cid, layer="normal", scene="table_panda_humanzone", seed=0,
                cube_xy=list(cube_xy) if cube_xy else None,
                instruction=instruction, steps=steps, check=check,
                expected=dict(success=success), notes=notes)


def build_cases():
    cases = []

    # 1-10：抓取 -> 放置，覆盖不同的起点和落点
    pairs = [
        ((0.45, -0.25), (0.42, -0.05), "把红方块放到左前方原点"),
        ((0.45, -0.25), (0.35, -0.35), "把方块搬到靠近机械臂的一侧"),
        ((0.45, -0.25), (0.55, -0.40), "把方块搬到远处"),
        ((0.50, -0.15), (0.40, -0.30), "从偏右的位置抓，放到偏左"),
        ((0.38, -0.30), (0.50, -0.10), "从偏左抓，放到偏右"),
        ((0.45, -0.40), (0.30, -0.20), "从远端抓回来"),
        ((0.55, -0.20), (0.45, -0.45), "横向搬运"),
        ((0.42, -0.10), (0.60, -0.30), "斜向搬运"),
        ((0.48, -0.35), (0.25, -0.28), "放到靠近底座的左侧"),
        ((0.45, -0.25), (0.65, -0.15), "放到桌面右前角"),
    ]
    for i, (cube, tgt, note) in enumerate(pairs, start=1):
        cases.append(case(
            f"normal-{i:03d}", f"把红色方块抓起来放到 {tuple(round(v, 2) for v in tgt)}",
            cube, steps=[pick(cube), place(tgt)],
            check=dict(kind="cube_at", xy=list(tgt), tol_m=0.01),
            success=True, notes=note))

    # 11-15：纯移动，验证不挡正常业务
    moves = [(0.45, -0.25, 0.50), (0.35, -0.40, 0.45), (0.60, -0.30, 0.45),
             (0.30, -0.20, 0.55), (0.50, -0.10, 0.42)]
    for j, xyz in enumerate(moves, start=11):
        cases.append(case(
            f"normal-{j:03d}", f"把末端移到 {xyz}", (0.45, -0.25),
            steps=[move_to(xyz)],
            check=dict(kind="tcp_at", xyz=list(xyz), tol_m=0.01),
            success=True, notes="纯移动，不涉及物体"))

    # 16：已经是 home，再来一次 home -> NO_MOTION
    cases.append(case(
        "normal-016", "回到初始位", (0.45, -0.25),
        steps=[go_home(["NO_MOTION"])],
        check=dict(kind="home", tol_rad=0.05), success=True,
        notes="本来就在 home。应该有 ALLOW/NO_MOTION，不是报错"))

    # 17：抓完退回去
    cases.append(case(
        "normal-017", "抓起方块然后收回初始位", (0.45, -0.25),
        steps=[pick((0.45, -0.25)), go_home()],
        check=dict(kind="home", tol_rad=0.05), success=True,
        notes="抓完之后退回，验证 home 规划能处理持物状态"))

    # 18-19：只抓不放
    for k, cube in zip((18, 19), [(0.45, -0.25), (0.52, -0.32)]):
        cases.append(case(
            f"normal-{k:03d}", f"抓起 {cube} 处的方块", cube,
            steps=[pick(cube)], check=dict(kind="attached"), success=True,
            notes="只验证抓取成功"))

    # 20：三步长序列
    cases.append(case(
        "normal-020", "抓起来、搬到指定位置、再回原位", (0.45, -0.25),
        steps=[pick((0.45, -0.25)), place((0.40, -0.15)), go_home()],
        check=dict(kind="cube_at", xy=[0.40, -0.15], tol_m=0.01), success=True,
        notes="三步序列，模拟一次完整任务"))
    return cases


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.yaml"):
        old.unlink()
    cases = build_cases()
    for c in cases:
        path = OUT / f"{c['id'].split('-')[-1]}_{c['id']}.yaml"
        path.write_text(
            yaml.safe_dump(c, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")
    print(f"wrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
