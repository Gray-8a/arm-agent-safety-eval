"""策略：从磁盘上的 policy.yaml 读，不接受任何模型可控输入。

这句话是整个项目的地基：
策略不是模型给的，不是函数参数传的，是进程启动时从文件读的。
所以"说服模型改规则"这条攻击链在物理上不存在。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "configs" / "policy.yaml"


@dataclass(frozen=True)
class Bounds:
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    def contains(self, p) -> bool:
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))


@dataclass(frozen=True)
class Zone:
    name: str
    kind: str
    bounds: Bounds
    min_clearance_m: float

    @property
    def is_exclusion(self) -> bool:
        return self.kind == "exclusion"

    @property
    def is_obstacle(self) -> bool:
        return self.kind == "obstacle"


@dataclass(frozen=True)
class Policy:
    workspace: Bounds
    zones: tuple[Zone, ...]
    max_step_m: float
    max_waypoint_joint_delta_rad: float
    joint_limit_margin_rad: float
    min_clearance_m: float
    near_clearance_band_m: float
    confidence_threshold: float
    approval_ttl_s: float
    allowed_actions: frozenset[str]
    policy_hash: str
    raw: dict

    # --- 载入 ---
    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "Policy":
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]

        def bnd(d) -> Bounds:
            return Bounds(
                lo=(float(d["x"][0]), float(d["y"][0]), float(d["z"][0])),
                hi=(float(d["x"][1]), float(d["y"][1]), float(d["z"][1])),
            )

        ws = bnd(data["workspace"])
        lim = data["limits"]
        default_clear = float(lim["min_clearance_m"])
        zones = tuple(
            Zone(name=z["name"], kind=z.get("kind", "exclusion"), bounds=bnd(z),
                 min_clearance_m=float(z.get("min_clearance_m", default_clear)))
            for z in data.get("zones", [])
        )
        risk = data["risk"]
        appr = data["approval"]
        return cls(
            workspace=ws,
            zones=zones,
            max_step_m=float(lim["max_step_m"]),
            max_waypoint_joint_delta_rad=float(lim["max_waypoint_joint_delta_rad"]),
            joint_limit_margin_rad=float(lim["joint_limit_margin_deg"]) * 3.141592653589793 / 180.0,
            min_clearance_m=default_clear,
            near_clearance_band_m=float(risk["near_clearance_band_m"]),
            confidence_threshold=float(risk["confidence_threshold"]),
            approval_ttl_s=float(appr["ttl_s"]),
            allowed_actions=frozenset(data["actions"]["allowed"]),
            policy_hash=digest,
            raw=data,
        )

    def exclusion_zones(self) -> tuple[Zone, ...]:
        return tuple(z for z in self.zones if z.is_exclusion)
