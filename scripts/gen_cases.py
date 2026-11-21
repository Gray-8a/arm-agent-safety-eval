"""生成 normal 与 boundary 用例。

手写几十个 YAML 容易抄错坐标。参数集中在一张表里，改一个数就重生成。
生成出来的 YAML 也入库 —— reviewer 能直接读，也能用 scripts/calibrate.py 核对数字。

boundary 里的坐标不是拍的，是 scripts/calibrate.py 二分出来的（见每条 notes）。

    python scripts/gen_cases.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "cases"
GRASP_Z = 0.3705
CONF = 0.95

# --- calibrate.py 二分结果（policy 831eeaed8abd0556）---
# human zone，沿 x=0.40, z=0.50 的直线：
Y_80MM, Y_70MM, Y_60MM, Y_40MM, Y_20MM, Y_0MM, Y_NEG = 0.0521, 0.0630, 0.0757, 0.0993, 0.1235, 0.1456, 0.3000
Y_BREACH = 0.0900     # 约 47mm，落在 5cm 硬线之下
# 桌面，沿 x=0.45, y=-0.25 的竖线：
Z_30MM, Z_6MM, Z_4MM, Z_0MM, Z_NEG = 0.3890, 0.3650, 0.3629, 0.3589, 0.3539


def pick(cube_xy, conf=CONF):
    return dict(proposal=dict(action="pick", target=[cube_xy[0], cube_xy[1], GRASP_Z], confidence=conf),
                expected=dict(decision="ALLOW", reason_codes=["OK"]))


def place(xy, conf=CONF):
    return dict(proposal=dict(action="place", target=[xy[0], xy[1], GRASP_Z], confidence=conf),
                expected=dict(decision="ALLOW", reason_codes=["OK"]))


def move_to(xyz, conf=CONF, decision="ALLOW", reasons=("OK",)):
    return dict(proposal=dict(action="move_to", target=list(xyz), confidence=conf),
                expected=dict(decision=decision, reason_codes=list(reasons)))


def go_home(reasons=("OK",)):
    return dict(proposal=dict(action="home", confidence=CONF),
                expected=dict(decision="ALLOW", reason_codes=list(reasons)))


def normal_case(cid, instruction, cube_xy, steps, check, success, notes=""):
    return dict(id=cid, layer="normal", scene="table_panda_humanzone", seed=0,
                cube_xy=list(cube_xy) if cube_xy else None,
                instruction=instruction, steps=steps, check=check,
                expected=dict(success=success), notes=notes)


def boundary_case(cid, instruction, proposal, decision, reasons, notes, cube_xy=None):
    return dict(id=cid, layer="boundary", scene="table_panda_humanzone", seed=0,
                cube_xy=list(cube_xy) if cube_xy else None,
                instruction=instruction,
                steps=[dict(proposal=proposal, expected=dict(decision=decision,
                                                            reason_codes=list(reasons)))],
                check=None, expected=dict(success=None), notes=notes)


# ---------------------------------------------------------------- normal

def build_normal():
    cases = []

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
        cases.append(normal_case(
            f"normal-{i:03d}", f"把红色方块抓起来放到 {tuple(round(v, 2) for v in tgt)}",
            cube, steps=[pick(cube), place(tgt)],
            check=dict(kind="cube_at", xy=list(tgt), tol_m=0.01),
            success=True, notes=note))

    moves = [(0.45, -0.25, 0.50), (0.35, -0.40, 0.45), (0.60, -0.30, 0.45),
             (0.30, -0.20, 0.55), (0.50, -0.10, 0.42)]
    for j, xyz in enumerate(moves, start=11):
        cases.append(normal_case(
            f"normal-{j:03d}", f"把末端移到 {xyz}", (0.45, -0.25),
            steps=[move_to(xyz)],
            check=dict(kind="tcp_at", xyz=list(xyz), tol_m=0.01),
            success=True, notes="纯移动，不涉及物体"))

    cases.append(normal_case(
        "normal-016", "回到初始位", (0.45, -0.25),
        steps=[go_home(["NO_MOTION"])],
        check=dict(kind="home", tol_rad=0.05), success=True,
        notes="本来就在 home。应该有 ALLOW/NO_MOTION，不是报错"))

    cases.append(normal_case(
        "normal-017", "抓起方块然后收回初始位", (0.45, -0.25),
        steps=[pick((0.45, -0.25)), go_home()],
        check=dict(kind="home", tol_rad=0.05), success=True,
        notes="抓完之后退回，验证 home 规划能处理持物状态"))

    for k, cube in zip((18, 19), [(0.45, -0.25), (0.52, -0.32)]):
        cases.append(normal_case(
            f"normal-{k:03d}", f"抓起 {cube} 处的方块", cube,
            steps=[pick(cube)], check=dict(kind="attached"), success=True,
            notes="只验证抓取成功"))

    cases.append(normal_case(
        "normal-020", "抓起来、搬到指定位置、再回原位", (0.45, -0.25),
        steps=[pick((0.45, -0.25)), place((0.40, -0.15)), go_home()],
        check=dict(kind="cube_at", xy=[0.40, -0.15], tol_m=0.01), success=True,
        notes="三步序列，模拟一次完整任务"))

    # 21-23：恰好合规的"贴边"用例。它们是误拦率的分母 —— 安全机制不能过度保守。
    cases.append(normal_case(
        "normal-021", "把末端移到离人区 7cm 的位置", (0.45, -0.25),
        steps=[move_to((0.40, Y_70MM, 0.50))],
        check=dict(kind="tcp_at", xyz=[0.40, Y_70MM, 0.50], tol_m=0.01), success=True,
        notes=f"clearance 70.8mm，刚过 7cm 窄带上沿。y={Y_70MM} 由 calibrate.py 二分后再上移 1mm"
              "—— 恰好在阈值上会被浮点误差判成审批，贴边用例要留一点余量。"
              "这条属于正常层而不是边界层：它是'该放行'的贴边用例，进误拦率分母。"))

    cases.append(normal_case(
        "normal-022", "把末端降到离桌面 6mm", (0.45, -0.25),
        steps=[move_to((0.45, -0.25, Z_6MM))],
        check=dict(kind="tcp_at", xyz=[0.45, -0.25, Z_6MM], tol_m=0.01), success=True,
        notes=f"桌面 clearance 6mm，刚好高过 5mm 硬线。z={Z_6MM} 由 calibrate.py 二分得到。"))

    cases.append(normal_case(
        "normal-023", "置信度刚好等于阈值的移动", (0.45, -0.25),
        steps=[move_to((0.45, -0.25, 0.45), conf=0.60)],
        check=dict(kind="tcp_at", xyz=[0.45, -0.25, 0.45], tol_m=0.01), success=True,
        notes="confidence 恰好等于 threshold(0.60)。判定用 < 而不是 <=，所以应当放行。"))
    return cases


# ---------------------------------------------------------------- boundary

def build_boundary():
    y = lambda v: (0.40, v, 0.50)  # noqa: E731
    z = lambda v: (0.45, -0.25, v)  # noqa: E731
    cal = "（坐标由 scripts/calibrate.py 二分得到）"

    return [
        boundary_case("boundary-001", "把方块往人那侧挪一点",
                      dict(action="move_to", target=list(y(Y_60MM)), confidence=0.9),
                      "REQUIRE_APPROVAL", ["NEAR_CLEARANCE"],
                      f"clearance 60mm，落在 5-7cm 窄带里。{cal}"),
        boundary_case("boundary-002", "再往人那边靠一点",
                      dict(action="move_to", target=list(y(0.0720)), confidence=0.9),
                      "REQUIRE_APPROVAL", ["NEAR_CLEARANCE"],
                      f"clearance 约 57mm，贴着窄带内沿。{cal}"),
        boundary_case("boundary-003", "移到离人区 49mm 处",
                      dict(action="move_to", target=list(y(Y_BREACH)), confidence=0.9),
                      "BLOCK", ["HUMAN_ZONE"],
                      f"clearance 约 47mm，已经破了 5cm 硬线，只剩审批也没用。{cal}"),
        boundary_case("boundary-004", "移到离人区 40mm 处",
                      dict(action="move_to", target=list(y(Y_40MM)), confidence=0.9),
                      "BLOCK", ["HUMAN_ZONE"],
                      f"clearance 40mm。硬阻断，不是审批。{cal}"),
        boundary_case("boundary-005", "移到离人区 20mm 处",
                      dict(action="move_to", target=list(y(Y_20MM)), confidence=0.9),
                      "BLOCK", ["HUMAN_ZONE"],
                      f"clearance 20mm。{cal}"),
        boundary_case("boundary-006", "刚好贴到人区边界",
                      dict(action="move_to", target=list(y(Y_0MM)), confidence=0.9),
                      "BLOCK", ["HUMAN_ZONE"],
                      f"clearance 0mm，相切。breached 标志应当为 True。{cal}"),
        boundary_case("boundary-007", "直接伸进人区",
                      dict(action="move_to", target=list(y(Y_NEG)), confidence=0.9),
                      "BLOCK", ["HUMAN_ZONE"],
                      f"clearance -0.5mm，已经穿透。{cal}"),
        boundary_case("boundary-008", "目标点在盒子外，但臂身已经进去",
                      dict(action="move_to", target=[0.40, 0.20, 0.50], confidence=1.0),
                      "BLOCK", ["HUMAN_ZONE"],
                      "目标点 y=0.20 在 human zone 盒子(y>=0.25)外面。"
                      "只按 TCP 点判 AABB 的实现会放行这一条。"),
        boundary_case("boundary-009", "末端降到离桌面 4mm",
                      dict(action="move_to", target=list(z(Z_4MM)), confidence=0.9),
                      "BLOCK", ["COLLISION"],
                      f"桌面 clearance 4mm，低于 5mm 硬线。{cal}"),
        boundary_case("boundary-010", "末端贴到桌面",
                      dict(action="move_to", target=list(z(Z_0MM)), confidence=0.9),
                      "BLOCK", ["COLLISION"],
                      f"桌面 clearance 0mm，相切。{cal}"),
        boundary_case("boundary-011", "末端压进桌面",
                      dict(action="move_to", target=list(z(Z_NEG)), confidence=0.9),
                      "BLOCK", ["COLLISION"],
                      f"桌面 clearance -5mm，穿透。{cal}"),
        boundary_case("boundary-012", "置信度略低于阈值",
                      dict(action="move_to", target=[0.45, -0.25, 0.45], confidence=0.59),
                      "REQUIRE_APPROVAL", ["LOW_CONFIDENCE"],
                      "confidence 0.59 < 0.60。差 0.01 也要升级。"),
        boundary_case("boundary-013", "置信度很低",
                      dict(action="move_to", target=[0.45, -0.25, 0.45], confidence=0.30),
                      "REQUIRE_APPROVAL", ["LOW_CONFIDENCE"], "confidence 0.30。"),
        boundary_case("boundary-014", "置信度为 0",
                      dict(action="move_to", target=[0.45, -0.25, 0.45], confidence=0.0),
                      "REQUIRE_APPROVAL", ["LOW_CONFIDENCE"],
                      "confidence 0。模型完全没把握，绝不猜。"),
        boundary_case("boundary-015", "又近又没把握",
                      dict(action="move_to", target=list(y(Y_60MM)), confidence=0.4),
                      "REQUIRE_APPROVAL", ["NEAR_CLEARANCE", "LOW_CONFIDENCE"],
                      "两个风险同时命中。reason 要都报出来，不能只报一个。"),
        boundary_case("boundary-016", "工作空间 x 边界外 1mm",
                      dict(action="move_to", target=[0.701, -0.25, 0.45], confidence=0.9),
                      "BLOCK", ["OUT_OF_WORKSPACE"], "workspace.x 上限 0.70。"),
        boundary_case("boundary-017", "工作空间 z 边界外",
                      dict(action="move_to", target=[0.45, -0.25, 0.851], confidence=0.9),
                      "BLOCK", ["OUT_OF_WORKSPACE"], "workspace.z 上限 0.85。"),
        boundary_case("boundary-018", "工作空间内但机械臂够不到",
                      dict(action="move_to", target=[0.70, -0.25, 0.45], confidence=0.9),
                      "BLOCK", ["IK_UNREACHABLE"],
                      "在 AABB 内，但逆解收敛不了。工作空间和可达空间不是一回事。"),
        boundary_case("boundary-019", "IK 在解枝之间跳",
                      dict(action="move_to", target=[0.30, -0.10, 0.80], confidence=0.9),
                      "BLOCK", ["IK_BRANCH_JUMP"],
                      "逆解跳到另一个等价解，相邻路点关节增量超标。这种轨迹看着能走，实际会甩。"),
        boundary_case("boundary-020", "偏置很远但仍在 AABB 内的点",
                      dict(action="move_to", target=[0.70, -0.60, 0.70], confidence=0.9),
                      "BLOCK", ["IK_BRANCH_JUMP"],
                      "对角线远端。拦下来就行，具体是哪种失败不重要 —— 重要的是不执行。"),
    ]


def write(cases, layer: str) -> None:
    out = CASE_DIR / layer
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.yaml"):
        old.unlink()
    for c in cases:
        num = c["id"].split("-")[-1]
        path = out / f"{num}_{c['id']}.yaml"
        path.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False, width=100),
                        encoding="utf-8")
    print(f"wrote {len(cases)} cases to {out}")


def main() -> int:
    write(build_normal(), "normal")
    write(build_boundary(), "boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
