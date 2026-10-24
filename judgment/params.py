"""打分参数的"形状"、边界与版本化存储。

三个约束是设计出来的，不是凑出来的：

1. 参数只是一组数，不是结构。用户的反馈永远不能改动问句、不能改动
   "五个价值方向"，只能在这些数的边界内挪动——否则系统会因为几次点击
   悄悄长出另一套判断标准。
2. 所有权重之和恒为 1。阈值才有可比性：换一组权重之后，"0.6 分"
   仍然是同一件事。
3. 每次提交都留一个不可变版本。参数能被改，也必须能被改回去。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

WEIGHT_FIELDS = ("direction", "value", "quality", "exploration")
SCALAR_FIELDS = ("reduced", "direction_floor", "threshold")

# 边界不是"建议值"，是硬约束：越过边界的参数会让某一维彻底失效。
BOUNDS: dict[str, tuple[float, float]] = {
    "direction": (0.10, 0.60),
    "value": (0.10, 0.60),
    "quality": (0.15, 0.65),
    "exploration": (0.00, 0.30),
    "reduced": (0.00, 0.50),
    "direction_floor": (0.05, 0.45),
    "threshold": (0.35, 0.90),
}

# 单次提交允许的最大位移（L∞）。一步挪太多 = 把参数交给最后一次点击。
MAX_STEP = {
    "direction": 0.06,
    "value": 0.06,
    "quality": 0.06,
    "exploration": 0.06,
    "reduced": 0.08,
    "direction_floor": 0.05,
    "threshold": 0.04,
}

PRIOR = {
    "direction": 0.28,
    "value": 0.28,
    "quality": 0.36,
    "exploration": 0.08,
    "reduced": 0.15,
    "direction_floor": 0.25,
    # 阈值：0.60 → 0.40（标定）→ 0.50（2026-10-20，14 天模拟砍噪声）→ 0.45（2026-10-22）。
    # 0.50 是用 scoring.py 里那套旧默认权重（0.30/0.30/0.40）调出来的；那套权重和这里的
    # 先验（0.28/0.28/0.36/0.08）一直并存，每日链路实际用的是前者。2026-10-22 统一到这一份
    # 先验之后，同样内容整体低约 0.045，所以阈值同比平移：0.50 − 0.05 = 0.45。
    # 验证：10-21 的 28 条真实测量上，候选池（10 条）与 selected（5 条）与改前完全一致；
    # 仍用 0.50 会掉到 8 条（召回变窄）。
    # 另有分通道门槛（judgment/channels.py 的 THRESHOLDS）：C 通道 0.42——
    # 作品类内容分数结构性偏低，用同一条线会把视觉通道清空（实测 18 → 4 条）。
    "threshold": 0.45,
}


@dataclass(frozen=True)
class Parameters:
    direction: float = PRIOR["direction"]
    value: float = PRIOR["value"]
    quality: float = PRIOR["quality"]
    exploration: float = PRIOR["exploration"]
    reduced: float = PRIOR["reduced"]
    direction_floor: float = PRIOR["direction_floor"]
    threshold: float = PRIOR["threshold"]

    def as_dict(self) -> dict[str, float]:
        return {name: round(getattr(self, name), 6) for name in WEIGHT_FIELDS + SCALAR_FIELDS}

    def digest(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_weights(self):
        """转成打分器用的 Weights（保持两处定义只有一个真相）。"""
        from judgment.scoring import Weights

        return Weights(
            direction=self.direction,
            value=self.value,
            quality=self.quality,
            exploration=self.exploration,
            reduced=self.reduced,
            direction_floor=self.direction_floor,
        )


def _renormalise(values: dict[str, float]) -> dict[str, float]:
    """把四个权重压回和为 1，同时不越界。

    先裁剪到边界，再把差额按"离边界还有多少余量"分摊；
    反复几轮即可收敛，比事后强行除总和更稳（强行归一化会把某一维直接推过界）。
    """
    for _ in range(24):
        total = sum(values[name] for name in WEIGHT_FIELDS)
        gap = 1.0 - total
        if abs(gap) < 1e-9:
            break
        headroom = {}
        for name in WEIGHT_FIELDS:
            low, high = BOUNDS[name]
            headroom[name] = (high - values[name]) if gap > 0 else (values[name] - low)
        pool = sum(headroom.values())
        if pool <= 1e-12:
            break
        for name in WEIGHT_FIELDS:
            values[name] += gap * headroom[name] / pool
            low, high = BOUNDS[name]
            values[name] = min(max(values[name], low), high)
    return values


def project(raw: dict[str, float]) -> Parameters:
    """任何来路不明的数都要先过这里：裁剪、归一、再返回。"""
    values: dict[str, float] = {}
    for name in WEIGHT_FIELDS + SCALAR_FIELDS:
        if name not in raw:
            raise ValueError(f"参数缺少 {name}")
        low, high = BOUNDS[name]
        values[name] = min(max(float(raw[name]), low), high)
    values = _renormalise({name: values[name] for name in WEIGHT_FIELDS}) | {
        name: values[name] for name in SCALAR_FIELDS
    }
    return Parameters(**{name: round(values[name], 6) for name in WEIGHT_FIELDS + SCALAR_FIELDS})


def apply_steps(current: Parameters, desired: dict[str, float]) -> tuple[Parameters, dict[str, float]]:
    """按单步上限收敛到 desired，返回 (新参数, 实际位移)。"""
    current_map = current.as_dict()
    moved: dict[str, float] = {}
    for name in WEIGHT_FIELDS + SCALAR_FIELDS:
        if name not in desired:
            continue
        target = float(desired[name])
        delta = target - current_map[name]
        cap = MAX_STEP[name]
        delta = min(max(delta, -cap), cap)
        current_map[name] = current_map[name] + delta
        moved[name] = round(current_map[name] - current.as_dict()[name], 6)
    return project(current_map), moved


# --------------------------------------------------------------------------- 存储


@dataclass(frozen=True)
class ParameterVersion:
    version: int
    parameters: Parameters
    updated_at: str
    source: str
    events_applied: int
    note: str = ""
    replay: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "parameters": self.parameters.as_dict(),
            "digest": self.parameters.digest(),
            "updated_at": self.updated_at,
            "source": self.source,
            "events_applied": self.events_applied,
            "note": self.note,
            "replay": self.replay,
        }


class ParameterStore:
    """一个用户一份参数，放在 state/params/<user_id>/ 下。

    版本目录只增不改；current.json 指向最新版本。回滚 = 把 current 指回旧版本
    并再写一个新版本（而不是删历史），这样"什么时候回滚的"也有记录。
    """

    def __init__(self, root: Path | str, user_id: str) -> None:
        self.user_id = user_id
        self.root = Path(root) / user_id
        self.versions_dir = self.root / "versions"
        self.current_path = self.root / "current.json"

    def _version_path(self, version: int) -> Path:
        return self.versions_dir / f"v{version:04d}.json"

    def current(self) -> ParameterVersion:
        if not self.current_path.exists():
            params = project(PRIOR)
            return ParameterVersion(0, params, "", "PRIOR", 0, "初始先验")
        data = json.loads(self.current_path.read_text(encoding="utf-8"))
        return ParameterVersion(
            version=int(data["version"]),
            parameters=project(data["parameters"]),
            updated_at=data.get("updated_at", ""),
            source=data.get("source", "PRIOR"),
            events_applied=int(data.get("events_applied", 0)),
            note=data.get("note", ""),
            replay=data.get("replay") or {},
        )

    def write(self, version: ParameterVersion) -> Path:
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        payload = version.as_dict()
        if version.version > 0:
            self._version_path(version.version).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8"
            )
        # v0000 也落一份，方便"从先验开始"这件事本身可查
        else:
            self._version_path(0).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8"
            )
        self.current_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8"
        )
        return self.current_path

    def next_version(self, current: ParameterVersion) -> int:
        existing = [int(path.stem[1:]) for path in self.versions_dir.glob("v*.json")] if self.versions_dir.exists() else []
        return max([current.version, *existing], default=0) + 1

    def history(self) -> list[dict[str, Any]]:
        if not self.versions_dir.exists():
            return []
        found = []
        for path in sorted(self.versions_dir.glob("v*.json")):
            found.append(json.loads(path.read_text(encoding="utf-8")))
        return found
