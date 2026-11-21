# 失败案例库

这张表是我做完之后最想给人看的东西 —— 不是"我做了个东西它很好"，
是"我踩了这些坑，每一个都留下了会复现它的测试"。

分两类：**实现 bug**（代码写错了）和**判定错误**（代码没错，但我在用错误的度量/口径下结论）。
第二类更危险，因为它不会报错。

## 一、实现 bug

| # | 症状 | 根因 | 定位方式 | 现在的护栏 |
|---|---|---|---|---|
| B1 | 第一个路点 TCP 一动不动，但关节转了 0.44 rad | IK 在等价解之间跳，`plan_line` 只约束了笛卡尔步长 | 打印相邻路点的关节增量 | 固定 READY 姿态 + 相邻路点关节增量 ≤ 0.15 rad，超了 `PlanError` |
| B2 | 10 条 place 用例全被 `STEP_TOO_LARGE` 挡 | 用**关节插值段**去量笛卡尔步长。0.08 rad 的关节步在 TCP 上能走 3cm | 看 evidence 里的 `max_cart_step_m` 与实际路点 | `Trajectory.is_joint` 掩码，笛卡尔步长只量纯笛卡尔段 |
| B3 | 掩码加了还有 5cm 的假步长 | 只排除了 joint-joint 段，漏了「笛卡尔 → 关节」的过渡段 | 同上 | 改成「任一端是关节段就排除」 |
| B4 | 抓完从低位直接关节插值回 READY，在桌子边上横扫 | 设计缺陷：真实系统都是先垂直抬再动 | 看轨迹：TCP 高度没变就先横移了 | `LIFT_Z_TRIGGER`：TCP 低于 0.45 先直线抬 10cm |
| B5 | 已经在 home 再发 home，episode 直接崩 | `plan_home` 抛 `EMPTY_TRAJECTORY`，但上层没处理"无轨迹" | 用例 normal-016 | 空轨迹 → `ALLOW/NO_MOTION`，不执行也不算失败 |
| B6 | 急停在真实路径上完全无效 | `evaluate(estop_active=...)` 这个参数**从来没人传** | 写对抗用例时探针一跑，`executed=True` | `EStop` 类 + episode 每步传参 + 复校验也查 |
| B7 | 审批后手臂被挪走，照样执行 | `recheck` 只量几何，不看位姿 | 写 D5 探针 | 比对当前位姿与 `plan_from_q`，差 > 0.02 rad 就 `STATE_CHANGED` |
| B8 | 回放 4 处判定不一致，全在多步用例第 2 步起 | 回放只 reset 场景，没恢复**裁决时**的姿态 | `scripts/replay.py` 的输出 | 从证据里读 `plan_from_q` 恢复；estop 状态也写进记录 |
| B9 | 策略里写了场景里不存在的 zone，gate 静默当作"没有这个区" | 名字找不到时返回 -1 继续走 | 代码审查 | 抛错 → `BLOCK/GATE_ERROR`（fail-closed） |

## 二、判定错误（更危险）

| # | 症状 | 根因 | 现在的护栏 |
|---|---|---|---|
| J1 | 标定脚本量出来的 clearance 全是 -199mm 这种离谱值 | `plan_move` 会把 qpos 留在轨迹终点，二分循环里没 reset，每次都"从上一次的终点出发" | `zone_clearance` 里先 `sc.reset()`；`calibrate.py` 可重跑核对 |
| J2 | 标定出的 70mm 点被判成审批 | 浮点误差算出 69.91mm，正好压在窄带上沿 | 贴边用例留 1mm 余量；用例 notes 里写明"不能压在阈值上" |
| J3 | "对抗拦截率 93%、未授权执行 1" | 多步用例里合法的前几步被算成未授权执行；审批升级被算成没拦住 | 口径改成「期望非 ALLOW 却真执行了的次数」；报告里每个指标都带分母 |
| J4 | 日志完整率 72/73 | 必需事件按「期望 ALLOW 就要有执行事件」推，"本来就在 home"那条没有运动 | 用例显式标 `motion: false`。**不能用"实际没记"来豁免，那是循环论证** |
| J5 | 篡改测试自己写错了 | 只改 payload 时，下一条的 `prev_hash` 指向的是存着的 hash，链接没断 | 拆成两个测试：只改 payload 断在自己，连带重算 hash 断在下一条 |
| J6 | `confidence` 省略时默认 1.0 | 默认值指向了**不安全**的那一侧 | 改成 0.0（fail-closed），且只对有目标点的动作生效 |
| J7 | `{"confidence": "0.9"}` 被判合法 | Pydantic 默认宽松转换，字符串悄悄变成数 | `mode="before"` 校验器，禁止隐式转换 |
| J8 | 以为「多步漂移」是个洞，实测不是 | 判断建立在"逐点校验"的想象上；实际扫的是整条轨迹 | 先跑探针再写用例。见 adv-030 |

## 三、复现命令

```powershell
# 全部用例 + 指标 + 审计日志
python scripts/run_suite.py

# 日志链校验（改一行就 FAIL）
python scripts/verify_log.py

# 回放：从日志重跑每一条裁决，对决策和 reason
python scripts/replay.py

# 边界阈值标定（B/J 类问题的来源）
python scripts/calibrate.py

# 四种结局的可视化证据
python scripts/demo_safety.py
```

## 四、一句话

B 类错误会崩，J 类错误会**通过**。
J1 和 J3 当时跑出来分别是「所有 clearance 都是负数」和「93% 拦截率」——
后者尤其像真的。
