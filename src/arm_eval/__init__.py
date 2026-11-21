"""arm-agent-safety-eval：仿真机械臂 Agent 的工作流安全评测套件。

分层（从上到下，权限递减）：
    agent  ->  提案（JSON）
    gate   ->  裁决 ALLOW / REQUIRE_APPROVAL / BLOCK
    executor -> 唯一能动仿真的一层
"""

__version__ = "0.1.0"
