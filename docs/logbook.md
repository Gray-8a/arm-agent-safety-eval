# 施工日志

每天三行：做了什么 / 卡在哪 / 明天第一件事。

---

## D1 · 2026-11-21 · 场景 + 技能层 + 执行器

**做了什么**
- MuJoCo 4.0 装配：panda + 桌 + 方块 + human zone（纯视觉 geom）。
- `scene.make(seed)` 确定性重置，两次 reset 的 qpos 哈希一致。
- IK（6 自由度 DLS）、`plan_pick/plan_place/retract`，轨迹有界（关节增量 <= 0.15 rad）。
- `executor.py` 作为唯一 `mj_step` 调用点，带 stop_event 急停通道。
- 17 条测试全绿；pick→place 落点误差 (-2.3, 0.5, -0.6) mm。

**卡在哪**
1. `<include>` 在 MuJoCo 4.0 丢了子文件的 meshdir，mesh 全找不到。试了 4 种写法都不行，改 `MjSpec.from_file()`。
2. 加自由关节后 nq 9→16，panda 自带的 keyframe 编译报错。装配时删掉 keyframe。
3. IK 在等价解之间跳：首路点 TCP 没动、腕部转 0.44 rad。加 READY 固定姿态 + 相邻路点关节增量硬约束。
4. human zone 最初压在 home 姿态上（clearance -13mm）。把 zone 从 y[0.10,0.80] 挪到 y[0.25,0.65]，home 余量 124mm。

**明天的第一件事**
D2：写 `ActionProposal` + 四级 gate 骨架，先写 30 条会失败的 unit 测试。

**顺带记下的数字**
- 臂展前沿：在 x=0.40, z=0.50 的直线上，TCP 的 y 只要超过 0.087，臂就进入 zone 的 5cm 缓冲。
  肉眼看着没碰到，其实已经超了 —— 说明"按 TCP 点判 AABB"必漏，必须扫臂身几何。
- 全程一集 pick→place 的仿真耗时约 0.2s 墙钟，2464 个物理步。CI 跑得动。
