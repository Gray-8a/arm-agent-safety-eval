"""急停：带外通道。

三个性质：
1. 不在模型上下文里 —— LLM 看不到它，也就没法"说服"它。
2. 锁存 —— 置位之后只能由操作员复位，模型没有任何动作能清掉它。
3. 每个物理步都查 —— 不给"跑到一半"留窗口（在 executor 里实现）。
"""

from __future__ import annotations

import threading
import time


class EStop:
    def __init__(self) -> None:
        self._ev = threading.Event()
        self.audit: list[dict] = []

    @property
    def is_set(self) -> bool:
        return self._ev.is_set()

    def press(self, by: str = "operator") -> None:
        self._ev.set()
        self.audit.append({"event": "estop_pressed", "by": by, "ts": time.time()})

    def reset(self, by: str = "operator") -> bool:
        """只有人能复位。模型的任何 action 都走不到这里。"""
        if not self._ev.is_set():
            return False
        self._ev.clear()
        self.audit.append({"event": "estop_reset", "by": by, "ts": time.time()})
        return True

    def as_active(self) -> bool:
        return self._ev.is_set()
