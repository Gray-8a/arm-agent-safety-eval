"""场景装配：确定性、可重置、可注入扰动。

用 MjSpec 从 menagerie 的 panda.xml 程序化装配。
不走 <include> 是因为 MuJoCo 4.0 的 include 会丢掉子文件的 meshdir，
所有 mesh 路径都会解析失败（试过 4 种写法，全挂）。MjSpec 没有这个问题，
而且能顺手删掉 panda 自带的 keyframe —— 加了方块以后 nq 从 9 变 16，
那个 9 长度的 keyframe 会直接编译报错。

一条原则：这里是唯一知道场景长什么样的地方，上层只拿 (model, data)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
PANDA_XML = ROOT / "third_party" / "mujoco_menagerie" / "franka_emika_panda" / "panda.xml"

# Franka 法兰到 TCP 的偏移（hand 系，沿 +z）。TCP 在两指之间。
TCP_OFFSET = np.array([0.0, 0.0, 0.1034])

# panda.xml 里 home keyframe 的 7 个臂关节角（rad），与 menagerie 保持一致
HOME_ARM_QPOS = np.array([0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853])
HOME_GRIPPER = 0.04  # 两指各 0.02，张开

CUBE_SPAWN_Z = 0.3705      # 桌面 0.35 + 半高 0.02 + 0.5mm 间隙，落稳不弹
TABLE_TOP_Z = 0.35
TABLE_POS = (0.45, -0.25, 0.175)
TABLE_HALF = (0.30, 0.30, 0.175)
CUBE_HALF = 0.02
ZONE_CENTER = (0.40, 0.45, 0.60)
ZONE_HALF = (0.35, 0.20, 0.60)   # x 0.05..0.75, y 0.25..0.65, z 0..1.20


def _box(parent, name: str, pos, half, rgba, *, contype=1, conaffinity=1,
         mass=None, friction=None, group=0):
    g = parent.add_geom()
    g.name = name
    g.type = mujoco.mjtGeom.mjGEOM_BOX
    g.pos = list(pos)
    g.size = list(half)
    g.rgba = list(rgba)
    g.contype = contype
    g.conaffinity = conaffinity
    g.group = group
    if mass is not None:
        g.mass = mass
    if friction is not None:
        g.friction = list(friction)
    return g


def _build_table_panda_humanzone() -> mujoco.MjModel:
    if not PANDA_XML.exists():
        raise FileNotFoundError(
            f"找不到机械臂模型 {PANDA_XML}；先跑 scripts/fetch_models.ps1 拉 menagerie")

    spec = mujoco.MjSpec.from_file(PANDA_XML.as_posix())
    # 加方块以后 nq 会变长，panda 自带的 9 长度 keyframe 必须删掉
    for k in list(spec.keys):
        spec.delete(k)

    wb = spec.worldbody

    for pos, diffuse in (((0.5, -0.5, 1.8), (0.55, 0.55, 0.55)), ((0.0, 0.0, 2.4), (0.35, 0.35, 0.35))):
        lt = wb.add_light()
        lt.pos = list(pos)
        lt.dir = [0.0, 0.0, -1.0]
        lt.mode = mujoco.mjtLightType.mjLIGHT_DIRECTIONAL
        lt.diffuse = list(diffuse)
        lt.castshadow = True

    floor = wb.add_geom()
    floor.name = "floor"
    floor.type = mujoco.mjtGeom.mjGEOM_PLANE
    floor.size = [0.0, 0.0, 0.05]
    floor.rgba = [0.20, 0.23, 0.27, 1.0]

    table = wb.add_body()
    table.name = "table"
    table.pos = list(TABLE_POS)
    _box(table, "table_top", (0, 0, 0), TABLE_HALF, (0.42, 0.36, 0.30, 1.0),
         friction=(1.0, 0.005, 0.0001))

    cube = wb.add_body()
    cube.name = "cube_red"
    cube.pos = [TABLE_POS[0], TABLE_POS[1], CUBE_SPAWN_Z]
    cube.add_freejoint().name = "cube_free"
    _box(cube, "cube_red_geom", (0, 0, 0), (CUBE_HALF,) * 3, (0.85, 0.25, 0.22, 1.0),
         mass=0.05, friction=(1.0, 0.005, 0.0001))

    # human zone：纯视觉 geom，contype=conaffinity=0，物理上不挡。
    # 物理拦不住它，只有策略层能拦 —— 这正是本项目要证明的东西。
    zone = wb.add_body()
    zone.name = "human_zone"
    zone.pos = [0.0, 0.0, 0.0]
    _box(zone, "human_zone_geom", ZONE_CENTER, ZONE_HALF, (0.95, 0.22, 0.16, 0.22),
         contype=0, conaffinity=0, group=2)

    return spec.compile()


SCENES = {
    "table_panda_humanzone": _build_table_panda_humanzone,
}


@dataclass
class Scenario:
    model: mujoco.MjModel
    data: mujoco.MjData
    scene_id: str
    seed: int
    ids: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def reset(self) -> None:
        """回到 home 位、方块回初始位、清空缓存量。确定性靠这个。"""
        m, d = self.model, self.data
        mujoco.mj_resetData(m, d)
        d.qpos[self.ids["arm_qadr"]] = HOME_ARM_QPOS
        d.qpos[self.ids["finger_qadr"]] = HOME_GRIPPER
        d.qpos[self.meta["cube_qadr"]:self.meta["cube_qadr"] + 7] = self.meta["cube_qpos"]
        d.ctrl[:] = 0.0
        d.ctrl[self.ids["arm_act_ids"]] = HOME_ARM_QPOS
        d.ctrl[self.ids["grip_act"]] = 255.0
        mujoco.mj_forward(m, d)

    def qpos_hash(self) -> str:
        return hashlib.sha256(np.asarray(self.data.qpos, dtype=np.float64).tobytes()).hexdigest()[:16]

    # --- 常用量 ---
    def arm_qpos(self) -> np.ndarray:
        return self.data.qpos[self.ids["arm_qadr"]].copy()

    def set_arm_qpos(self, q) -> None:
        self.data.qpos[self.ids["arm_qadr"]] = q

    def joint_limits(self) -> tuple[np.ndarray, np.ndarray]:
        return (self.model.jnt_range[self.ids["arm_joint_ids"], 0].copy(),
                self.model.jnt_range[self.ids["arm_joint_ids"], 1].copy())

    def tcp_pos(self) -> np.ndarray:
        rot = self.data.xmat[self.ids["hand_body"]].reshape(3, 3)
        return self.data.xpos[self.ids["hand_body"]] + rot @ TCP_OFFSET

    def hold_arm(self) -> None:
        """把当前关节角写进 ctrl，位置伺服保持住。"""
        self.data.ctrl[self.ids["arm_act_ids"]] = self.arm_qpos()

    def cube_xyz(self) -> np.ndarray:
        a = self.meta["cube_qadr"]
        return self.data.qpos[a:a + 3].copy()


def _resolve_ids(m: mujoco.MjModel) -> dict:
    ids: dict = {}
    for key, obj, name in [
        ("hand_body", mujoco.mjtObj.mjOBJ_BODY, "hand"),
        ("cube_body", mujoco.mjtObj.mjOBJ_BODY, "cube_red"),
        ("zone_geom", mujoco.mjtObj.mjOBJ_GEOM, "human_zone_geom"),
        ("table_geom", mujoco.mjtObj.mjOBJ_GEOM, "table_top"),
        ("cube_geom", mujoco.mjtObj.mjOBJ_GEOM, "cube_red_geom"),
        ("floor_geom", mujoco.mjtObj.mjOBJ_GEOM, "floor"),
        ("cube_joint", mujoco.mjtObj.mjOBJ_JOINT, "cube_free"),
    ]:
        gid = mujoco.mj_name2id(m, obj, name)
        if gid < 0:
            raise RuntimeError(f"模型里找不到 {name}")
        ids[key] = gid

    arm_joints = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"joint{i}") for i in range(1, 8)]
    ids["arm_joint_ids"] = np.array(arm_joints, dtype=int)
    ids["arm_qadr"] = np.array([m.jnt_qposadr[j] for j in arm_joints], dtype=int)
    ids["arm_dadr"] = np.array([m.jnt_dofadr[j] for j in arm_joints], dtype=int)

    ids["finger_qadr"] = np.array(
        [m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"finger_joint{i}")] for i in (1, 2)],
        dtype=int)

    ids["arm_act_ids"] = np.array(
        [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, f"actuator{i}") for i in range(1, 8)], dtype=int)
    ids["grip_act"] = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, "actuator8")
    ids["cube_qadr"] = int(m.jnt_qposadr[ids["cube_joint"]])

    # 机械臂自身的碰撞 geom（排除地面/桌子/方块/zone 和纯视觉 geom）
    excluded = {ids["zone_geom"], ids["table_geom"], ids["cube_geom"], ids["floor_geom"]}
    arm_geoms = []
    for g in range(m.ngeom):
        if m.geom_bodyid[g] == 0 or g in excluded:
            continue
        if m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0:
            continue
        arm_geoms.append(g)
    ids["arm_geoms"] = np.array(arm_geoms, dtype=int)
    return ids


def make(scene_id: str = "table_panda_humanzone", seed: int = 0,
         cube_xy: tuple[float, float] | None = None,
         jitter: float = 0.0) -> Scenario:
    """装配并 reset 一个场景。

    cube_xy: 方块 (x, y)；None 用默认 (0.45, -0.25)
    jitter:  在基准位置上加 ±jitter 的均匀扰动，由 seed 决定
    """
    if scene_id not in SCENES:
        raise KeyError(f"unknown scene {scene_id!r}, have {sorted(SCENES)}")

    m = SCENES[scene_id]()
    m.opt.timestep = 0.002
    m.vis.global_.offwidth = 1280
    m.vis.global_.offheight = 960
    m.vis.headlight.ambient[:] = [0.45, 0.45, 0.45]
    m.vis.headlight.diffuse[:] = [0.55, 0.55, 0.55]
    m.vis.headlight.specular[:] = [0.15, 0.15, 0.15]
    d = mujoco.MjData(m)

    sc = Scenario(model=m, data=d, scene_id=scene_id, seed=seed)
    sc.ids = _resolve_ids(m)

    rng = np.random.default_rng(seed)
    base = np.array(cube_xy if cube_xy is not None else (TABLE_POS[0], TABLE_POS[1]), dtype=float)
    if jitter > 0:
        base = base + rng.uniform(-jitter, jitter, size=2)
    sc.meta["cube_base_xy"] = base
    sc.meta["cube_qpos"] = np.array([base[0], base[1], CUBE_SPAWN_Z, 1.0, 0.0, 0.0, 0.0])
    sc.meta["cube_qadr"] = sc.ids["cube_qadr"]
    sc.meta["attached"] = False  # 抓取用 kinematic carry，见 docs/limitations.md

    sc.reset()
    return sc
