"""动作提案：Agent 唯一被允许产出的东西。

设计要点：
- extra="forbid"：多带一个字段就整条拒绝，不给"顺手塞个 disable_fence"的机会。
- action 是枚举：动作空间本身就是白名单，清单不可篡改是语法层面的保证，不是配置项。
- 数值必须有限且量级合理：nan / inf / 1e9 全在 schema 层拦掉，不进策略层。
"""

from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# 允许的坐标量级（米）。超出这个范围的数一定是单位搞错了或者有人在搞事。
COORD_LIMIT = 10.0


class Action(str, Enum):
    HOME = "home"
    MOVE_TO = "move_to"
    PICK = "pick"
    PLACE = "place"
    STOP = "stop"
    OBSERVE = "observe"


class ProposalErrorCode(str, Enum):
    SCHEMA_INVALID = "SCHEMA_INVALID"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Action
    target: tuple[float, float, float] | None = None
    object: str | None = Field(default=None, max_length=64)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=500)
    source: str = Field(default="unknown", max_length=32)

    @field_validator("target")
    @classmethod
    def _finite_coords(cls, v):
        if v is None:
            return v
        for x in v:
            if not math.isfinite(x):
                raise ValueError("target 含 nan/inf")
            if abs(x) > COORD_LIMIT:
                raise ValueError(f"target 分量 {x} 超过 ±{COORD_LIMIT}m")
        return v

    def needs_target(self) -> bool:
        return self.action in (Action.MOVE_TO, Action.PICK, Action.PLACE)


def parse_proposal(raw) -> tuple[ActionProposal | None, str | None]:
    """把模型输出解析成提案。返回 (提案, 错误码)，二选一非空。

    先看 action 再走 Pydantic：这样"动作不存在"和"动作存在但参数烂"
    能落到不同的 reason code，日志里才分得清是越权尝试还是格式崩了。
    """
    if isinstance(raw, ActionProposal):
        return raw, None
    if not isinstance(raw, dict):
        return None, ProposalErrorCode.SCHEMA_INVALID.value

    action = raw.get("action")
    if not isinstance(action, str):
        return None, ProposalErrorCode.SCHEMA_INVALID.value
    if action not in {a.value for a in Action}:
        return None, ProposalErrorCode.UNKNOWN_ACTION.value

    try:
        proposal = ActionProposal(**raw)
    except ValidationError:
        return None, ProposalErrorCode.SCHEMA_INVALID.value

    if proposal.needs_target() and proposal.target is None:
        return None, ProposalErrorCode.SCHEMA_INVALID.value
    return proposal, None
