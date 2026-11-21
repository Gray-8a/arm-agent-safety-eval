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

---

## D2 · 2026-11-21 · 策略门 + 用例框架 + CI

**做了什么**
- `ActionProposal`（Pydantic，extra=forbid，枚举即白名单）+ `parse_proposal` 把"动作不存在"和"参数烂"分开报。
- `gate/policy.py`：策略只从 policy.yaml 读，带 policy_hash，不接受任何函数参数或模型输入。
- `gate/gate.py`：五级检查（schema/estop/工具/工作空间/规划+步长/几何扫掠/风险分级），fail-closed，只读。
- 两种 zone：exclusion（人区，5cm 缓冲 + 窄带审批）和 obstacle（桌子，只硬拦穿透）。
- 用例 YAML 框架 + `run_suite.py` 出 markdown 报告。4 条种子用例（normal 1 / boundary 1 / adversarial 2）全过。
- 61 条测试；`test_mutation_sensitivity` 把阈值改坏来证明阈值承重。
- GitHub Actions：ubuntu，不渲染，pytest -m "not live"。

**卡在哪**
1. gate 里规划会临时写 qpos，导致连续两次 evaluate 结果不同。加 save/restore + 哈希测试。
2. `_verdict(BLOCK, REASON, {...})` 把 dict 塞进了 *reasons。参数顺序改关键字。
3. 桌子 geom 叫 table_top，但 gate 按 `{zone}_geom` 找。统一命名成 table_geom——约定不一致的代价。
4. 一开始把"看不清"和"越权动作"都报 SCHEMA_INVALID，日志里分不出是攻击还是 bug。拆成 UNKNOWN_ACTION。

**明天的第一件事**
D3：正常用例做到 20 条，加 episode 层（裁决 -> 执行 -> 成败判定），出第一版完整指标表。

**顺带记下的数字**
- gate 单次裁决 3–10ms（含规划 20+ 路点和 23 个臂 geom 的扫掠距离）。
- 下探到 z=0.10 会穿桌面 -8.9mm；pick/place 的路径离桌面 17.0mm / 11.7mm。所以 obstacle 余量设 5mm 就够，不会误伤正常动作。
- 近似窄带（5–7cm）在 x=0.40,z=0.50 直线上对应 TCP 的 y ∈ [0.07, 0.09] 左右，只有 2cm 宽——窄带用例必须精确到毫米级，不能拍。

---

## D3 · 2026-11-21 · episode 层 + 20 条正常用例

**做了什么**
- `episode.py`：裁决 -> 审批 -> 执行 -> 成败判定。执行的是 `verdict.trajectory`（gate 校验过的同一条），不是重新规划。
- `harness.py` 重构成多步骤用例（steps + check + expect_success），支持 pick->place->home 这种序列。
- 20 条正常用例（脚本生成，参数集中在 `scripts/gen_cases.py` 一张表里）：10 组抓放 + 5 组纯移动 + home + 抓后 home + 2 条只抓 + 1 条三步序列。
- 指标表：正常通过率 100%（20/20），误拦率 0%（分母 21），未授权执行 0。
- 73 条测试。

**卡在哪**
1. `Verdict.trajectory` 为 None 时 episode 直接崩（home 已经在 home 的情况）。改成"没有轨迹 = 无事可做"，不执行也不算失败。
2. `STEP_TOO_LARGE` 把 place 步全挡了。原因是拿"关节插值段"去量笛卡尔步长 —— 一个 0.08 rad 的关节步在 TCP 上可能走 3cm。给 Trajectory 加 `is_joint` 掩码，笛卡尔步长只量纯笛卡尔段。
3. 掩码第一版写错了：只排除了 joint-joint 段，漏了"笛卡尔 -> 关节"的过渡段，还剩 5cm 的假步长。改成"任一端是关节段就排除"。
4. 更实的问题：抓完从低位直接关节插值回 READY，等于在桌子边上横扫。改成先垂直抬起 10cm 再关节插值（`LIFT_Z_TRIGGER`）。这条不是 bug，是设计缺陷。

**明天的第一件事**
D4：边界用例 20 条 + HMAC 单次审批 token + TOCTOU 复校验。

**顺带记下的数字**
- 一集 pick->place 约 4500 个物理步，全套 23 条用例 7.2 万步，跑批十几秒。
- place 路径离桌面最近 11.7mm，pick 是 17.0mm；抬升之后的路径这个数没变差。
- 20 条正常用例覆盖的坐标范围：x ∈ [0.25, 0.65]，y ∈ [-0.45, -0.05]，全在 human zone（y>=0.25）之外。

---

## D4 · 2026-11-21 · 边界用例 + 审批 token + TOCTOU

**做了什么**
- `gate/approval.py`：HMAC 签名（绑定 proposal_digest）、TTL、单次核销、审计事件。
- `gate.recheck(trajectory, sc)`：审批后执行前，拿同一条轨迹对现在的环境重量一次。
- `scripts/calibrate.py`：二分出"clearance == 某值"对应的目标点，坐标写进用例 notes。
- 20 条边界用例 + 3 条"恰好合规"的贴边用例（进正常层，做误拦率分母）。
- 45 条用例全过：正常 23/23、边界 20/20、对抗 2/2；审批触发率 13%（6/45），误拦率 0%（分母 29）。
- 91 条测试。

**卡在哪**
1. 标定脚本没 reset 场景 —— plan 会把 qpos 留在轨迹终点，下一次规划从上一次终点出发，量出来的 clearance 全是错的（一开始所有 z 都收敛到同一个 0.06）。
2. 桌面二分方向写反了：clearance 随 z 增大而增大，我却按"随 z 减小"写的。修完才拿到 6mm/4mm 两个点。
3. 在 y=0.0639（标定出的 70mm 点）上，gate 判成审批 —— 浮点误差让 c 变成 69.91mm，刚好卡在 band 上沿。贴边用例必须留 1mm 余量，不能正好压在阈值上。
4. `store.ask(verdict and step.proposal, ...)` —— 用 and 短路取第一个真值，能跑但语义是错的，改成直传。

**明天的第一件事**
D5：对抗用例扩到 30 条 + 威胁模型文档 + compromised-agent 注入。

**顺带记下的数字**
- 人区门槛（x=0.40, z=0.50）：70mm→ALLOW(y=0.0630)，60mm→审批(y=0.0757)，40mm→阻断(y=0.0993)，0mm→阻断(y=0.1456)。2cm 的目标位移能吃掉 4cm 的 clearance，所以边界用例必须精确到毫米。
- 桌面门槛（x=0.45, y=-0.25）：6mm→ALLOW(z=0.3650)，4mm→阻断(z=0.3629)。2mm 的差距，同样不能拍。
- 置信度：0.60→ALLOW，0.59→审批。判定用 `<` 不是 `<=`。
- 工作空间 AABB 内的点很多其实够不到（x=0.70 直接 IK_UNREACHABLE）。"工作空间"和"可达空间"是两件事，用例里两种都覆盖了。
