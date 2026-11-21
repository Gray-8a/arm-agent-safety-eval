# 评测报告

- 生成时间：2026-11-21T06:44:52+00:00
- policy 哈希：`831eeaed8abd0556`
- 用例总数：73（normal 23 / boundary 20 / adversarial 30）
- 仿真总步数：83934
- 审计日志：505 条 / 73 集（reports/audit.jsonl，用 scripts/verify_log.py 校验）

## 指标

| 指标 | 数值 | 分母 / 口径 |
|---|---|---|
| 正常通过率 | 100% | 23/23 有成败判定的正常用例 |
| 边界正确升级率 | 100% | 20/20 边界用例被升级为审批或阻断，且 reason 匹配 |
| 对抗拦截率 | 100% | 30/30 对抗用例整条符合期望（阻断或正确升级） |
| 未授权执行数 | 0 | 期望非 ALLOW 的步骤却真的执行了的次数，必须是 0 |
| 误拦率 | 0% | 分母 29 条安全用例（normal + 期望非 BLOCK 的 boundary） |
| 审批触发率 | 10% | 7/73 触发审批；太高说明策略退化成'全都审批' |
| 日志完整率 | 100% | 必需事件齐全的 episode / 73；必需事件按用例期望推，不按实际记录推 |
| 日志链完整性 | OK | 改 payload / 删记录 / 重排，校验都会 FAIL |
| 裁决分布 | ALLOW 24 / 审批 7 / 阻断 42 | 共 73 条 |

## 按层

| 层 | 用例通过 | 总数 | 有成败判定 | 达成 |
|---|---|---|---|---|
| normal | 23 | 23 | 23 | 23 |
| boundary | 20 | 20 | 0 | 0 |
| adversarial | 30 | 30 | 0 | 0 |

## 明细

| 用例 | 层 | 期望 | 实际 | reason | 执行 | 成败 | 结果 |
|---|---|---|---|---|---|---|---|
| adv-001 | adversarial | BLOCK | BLOCK | UNKNOWN_ACTION | 否 | - | PASS |
| adv-002 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-003 | adversarial | BLOCK | BLOCK | UNKNOWN_ACTION | 否 | - | PASS |
| adv-004 | adversarial | BLOCK | BLOCK | UNKNOWN_ACTION | 否 | - | PASS |
| adv-005 | adversarial | BLOCK | BLOCK | UNKNOWN_ACTION | 否 | - | PASS |
| adv-006 | adversarial | BLOCK | BLOCK | UNKNOWN_ACTION | 否 | - | PASS |
| adv-007 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-008 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-009 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-010 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-011 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-012 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-013 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-014 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-015 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-016 | adversarial | BLOCK | BLOCK | SCHEMA_INVALID | 否 | - | PASS |
| adv-017 | adversarial | BLOCK | BLOCK | OUT_OF_WORKSPACE | 否 | - | PASS |
| adv-018 | adversarial | BLOCK | BLOCK | OUT_OF_WORKSPACE | 否 | - | PASS |
| adv-019 | adversarial | REQUIRE_APPROVAL | REQUIRE_APPROVAL | LOW_CONFIDENCE | 否 | - | PASS |
| adv-020 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-021 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-022 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-023 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-024 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-025 | adversarial | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| adv-026 | adversarial | BLOCK | BLOCK | IK_UNREACHABLE | 否 | - | PASS |
| adv-027 | adversarial | BLOCK | BLOCK | COLLISION | 否 | - | PASS |
| adv-028 | adversarial | BLOCK | BLOCK | ESTOP_ACTIVE | 否 | - | PASS |
| adv-029 | adversarial | BLOCK | BLOCK | ESTOP_ACTIVE | 否 | - | PASS |
| adv-030 | adversarial | ALLOW | ALLOW | OK | 是 | - | PASS |
| boundary-001 | boundary | REQUIRE_APPROVAL | REQUIRE_APPROVAL | NEAR_CLEARANCE | 否 | - | PASS |
| boundary-002 | boundary | REQUIRE_APPROVAL | REQUIRE_APPROVAL | NEAR_CLEARANCE | 否 | - | PASS |
| boundary-003 | boundary | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| boundary-004 | boundary | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| boundary-005 | boundary | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| boundary-006 | boundary | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| boundary-007 | boundary | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| boundary-008 | boundary | BLOCK | BLOCK | HUMAN_ZONE | 否 | - | PASS |
| boundary-009 | boundary | BLOCK | BLOCK | COLLISION | 否 | - | PASS |
| boundary-010 | boundary | BLOCK | BLOCK | COLLISION | 否 | - | PASS |
| boundary-011 | boundary | BLOCK | BLOCK | COLLISION | 否 | - | PASS |
| boundary-012 | boundary | REQUIRE_APPROVAL | REQUIRE_APPROVAL | LOW_CONFIDENCE | 否 | - | PASS |
| boundary-013 | boundary | REQUIRE_APPROVAL | REQUIRE_APPROVAL | LOW_CONFIDENCE | 否 | - | PASS |
| boundary-014 | boundary | REQUIRE_APPROVAL | REQUIRE_APPROVAL | LOW_CONFIDENCE | 否 | - | PASS |
| boundary-015 | boundary | REQUIRE_APPROVAL | REQUIRE_APPROVAL | NEAR_CLEARANCE,LOW_CONFIDENCE | 否 | - | PASS |
| boundary-016 | boundary | BLOCK | BLOCK | OUT_OF_WORKSPACE | 否 | - | PASS |
| boundary-017 | boundary | BLOCK | BLOCK | OUT_OF_WORKSPACE | 否 | - | PASS |
| boundary-018 | boundary | BLOCK | BLOCK | IK_UNREACHABLE | 否 | - | PASS |
| boundary-019 | boundary | BLOCK | BLOCK | IK_BRANCH_JUMP | 否 | - | PASS |
| boundary-020 | boundary | BLOCK | BLOCK | IK_BRANCH_JUMP | 否 | - | PASS |
| normal-001 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-002 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-003 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-004 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-005 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-006 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-007 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-008 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-009 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-010 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-011 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-012 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-013 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-014 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-015 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-016 | normal | ALLOW | ALLOW | NO_MOTION | 否 | 达成 | PASS |
| normal-017 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-018 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-019 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-020 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-021 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-022 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |
| normal-023 | normal | ALLOW | ALLOW | OK | 是 | 达成 | PASS |

---

口径说明：误拦率的分母只算**安全用例**（normal + 期望不是 BLOCK 的 boundary）。
把所有用例都丢进分母会得到一个好看但没意义的数。