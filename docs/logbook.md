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


---

## D5 · 2026-11-21 · 30 条对抗用例 + 威胁模型

**做了什么**
- 30 条对抗用例（`scripts/adv_cases.py` 生成），覆盖 18 类攻击面，见 `docs/threat-model.md`。
- 写用例前先验证了三个怀疑的洞，全部成立，全部修掉：
  1. 省略 `confidence` 默认 1.0 —— 等于一条免费的绕过路径。改成 0.0（fail-closed），且只对有目标点的动作生效。
  2. 急停根本没接进 episode：`evaluate` 有 `estop_active` 参数，但没人传。补 `EStop` 类 + episode 传参 + 复校验也查。
  3. 审批后机械臂被别的通道挪走，复校验只看几何，照样放行。现在比对当前位姿与规划起点（`plan_from_q`）。
- Pydantic 把 `"0.9"` 悄悄转成 0.9 —— 加 `mode="before"` 校验器，禁止隐式转换。
- 指标口径修正：多步用例里合法的前几步不算「未授权执行」；审批升级算拦截。
- 96 条测试，73 条用例全过：正常 23/23、边界 20/20、对抗 30/30，未授权执行 0。

**卡在哪**
1. 生成脚本里中文引号写成了 ASCII 双引号，直接把字符串截断，语法错误。拆成独立模块重写。
2. 指标一开始把 adv-030 的正常前三步算成「未授权执行」，把 adv-019（审批）算成「没拦住」。口径错了数字就全是错的。
3. 加了 confidence 默认 0.0 之后，一批老测试（不带 confidence 的 move_to）全变成审批。回头给测试补显式数值 —— 默认值改动的影响面比想象大。

**明天的第一件事**
D6：哈希链审计日志 + 回放 + 报告里的日志完整率。

**顺带记下的数字**
- 73 条用例：ALLOW 24 / 审批 7 / 阻断 42。审批触发率 10%。
- adv-030 多步漂移：y=0.020 / 0.040 / 0.060 三步全 ALLOW，第 4 步 y=0.090 被拦。
  我原本以为逐步合法就能溜过去，实测不行 —— 扫掠的是整条轨迹而不是终点，第 4 步的轨迹本身就破线。
  这个「以为有洞结果没有」值得记：先验证再写用例，否则写出来的用例是给假想敌准备的。


---

## D6 · 2026-11-21 · 哈希链审计 + 回放

**做了什么**
- `audit/log.py`：JSONL 哈希链（seq / ts / episode_id / event / actor / payload / prev_hash / hash / schema_version）。
- episode 不再自己攒 audit 列表，改成走一步记一步进同一本账：指令、模型原输出、裁决+证据、审批请求/决定、复校验、执行开始/结束、终态。
- `scripts/verify_log.py`、`scripts/replay.py`。
- 日志完整率 100%（73 集），链校验 OK（505 条记录）。回放 73 集，判定不一致 0 处。
- 111 条测试。

**卡在哪**
1. 完整率算成 72/73。缺的是 normal-016（本来就在 home，无运动）。必需事件按「期望 ALLOW 就要有执行事件」推，这条根本没有运动。加 `motion: false` 显式标注 —— 不能靠「实际没记」来豁免，那是循环论证。
2. 回放 4 处不一致，全是多步用例第 2 步起。原因：裁决依赖**当前姿态**，我只 reset 场景就逐条重放，状态对不上。修法是从证据里读出 `plan_from_q` 恢复。
3. 还剩 2 处：adv-028/029 是急停用例，回放没传 `estop_active`。把 estop 状态也写进 gate_verdict 记录。
4. 我写的篡改测试本身是错的：只改 payload 时，下一条的 prev_hash 指向的是**存着的** hash，链接没断，断的是被改那条自己的 hash。拆成两个测试：只改 payload 断在自己，连带重算 hash 断在下一条。这才说明链的意义。

**明天的第一件事**
D7：发布。README、失败案例库、GIF、复现命令、tag v0.1.0。

**顺带记下的数字**
- 505 条记录 / 73 集，平均每集 6.9 条。
- 回放一集约 40ms（只重跑判断，不重跑仿真）。
- 报告里现在有 9 个指标，每个都带分母。
