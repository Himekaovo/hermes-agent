from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import Mapping, Optional

LAYERS = ("L1", "L2", "L3", "L4")
AUTHORITY_RANK = {"L1": 4, "L2": 3, "L3": 2, "L4": 1}
DEFAULT_TOTAL_CHAR_BUDGET = 6000
MAX_TOTAL_CHAR_BUDGET = 12000
DEFAULT_MAX_ITEMS_PER_LAYER = 8
DEFAULT_MAX_ITEM_CHARS = 1000
DEFAULT_INITIAL_LAYER_BUDGETS = {"L1": 1200, "L2": 3200, "L3": 800, "L4": 800}
DEFAULT_HARD_LAYER_CAPS = {"L1": 2400, "L2": 6000, "L3": 1600, "L4": 1600}
DEFAULT_CRON_MULTIPLIER = 0.5
DEFAULT_SUBAGENT_MULTIPLIER = 0.5

_ABS_PATH_RE = re.compile(r"(?<!\w)/(?:Users|home|private|tmp|var|etc)/[^\s)]+")
_SQL_RE = re.compile(r"\bSELECT\s+.+?\bFROM\b", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=([^\s]+)"
)


def _clamp_score(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _freeze_value(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return tuple((str(key), _freeze_value(value[key])) for key in sorted(value))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_value(item) for item in value)
    return str(value)


def freeze_reason_detail(value: object) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, Mapping):
        return ()
    return tuple((str(key), _freeze_value(value[key])) for key in sorted(value))


def sanitize_reason_text(text: str) -> str:
    clean = _SQL_RE.sub("[redacted_sql]", text or "")
    clean = _ABS_PATH_RE.sub("[redacted_path]", clean)
    clean = _SECRET_VALUE_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]", clean
    )
    return " ".join(clean.split())[:500]


@dataclass(frozen=True)
class MnemosyneSourceRef:
    layer: str
    item_id: str
    source: str
    reason_code: str
    reason_detail: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"invalid layer: {self.layer}")
        object.__setattr__(
            self, "reason_detail", freeze_reason_detail(dict(self.reason_detail))
        )


@dataclass(frozen=True)
class MnemosyneItem:
    item_id: str
    layer: str
    content: str
    reason: str
    reason_code: str
    reason_detail: tuple[tuple[str, object], ...]
    score: float
    source: str
    provenance: tuple[MnemosyneSourceRef, ...]
    trust: Optional[float] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"invalid layer: {self.layer}")
        object.__setattr__(self, "score", _clamp_score(self.score))
        object.__setattr__(self, "reason", sanitize_reason_text(self.reason))
        object.__setattr__(
            self, "reason_detail", freeze_reason_detail(dict(self.reason_detail))
        )
        object.__setattr__(self, "provenance", tuple(self.provenance))


@dataclass(frozen=True)
class MnemosyneConfig:
    total_char_budget: int
    max_total_char_budget: int
    max_items_per_layer: int
    max_item_chars: int
    initial_layer_budgets: dict[str, int]
    hard_layer_caps: dict[str, int]
    max_l4_file_chars: int
    max_l4_record_chars: int

    @classmethod
    def from_mapping(
        cls,
        mapping: Optional[Mapping[str, object]],
        *,
        execution_kind: str = "interactive",
    ) -> "MnemosyneConfig":
        data = dict(mapping or {})
        total = min(
            int(data.get("total_char_budget", DEFAULT_TOTAL_CHAR_BUDGET)),
            MAX_TOTAL_CHAR_BUDGET,
        )
        initial = dict(DEFAULT_INITIAL_LAYER_BUDGETS)
        initial.update(
            {
                key: int(value)
                for key, value in dict(data.get("initial_layer_budgets", {})).items()
                if key in LAYERS
            }
        )
        hard = dict(DEFAULT_HARD_LAYER_CAPS)
        hard.update(
            {
                key: int(value)
                for key, value in dict(data.get("hard_layer_caps", {})).items()
                if key in LAYERS
            }
        )
        multiplier = 1.0
        if execution_kind == "cron":
            multiplier = float(data.get("cron_multiplier", DEFAULT_CRON_MULTIPLIER))
        elif execution_kind == "subagent":
            multiplier = float(data.get("subagent_multiplier", DEFAULT_SUBAGENT_MULTIPLIER))
        if multiplier != 1.0:
            total = floor(total * multiplier)
            initial = {
                layer: floor(value * multiplier) for layer, value in initial.items()
            }
            hard = {layer: floor(value * multiplier) for layer, value in hard.items()}
        hard = {layer: max(hard[layer], initial[layer]) for layer in LAYERS}
        return cls(
            total_char_budget=max(0, total),
            max_total_char_budget=MAX_TOTAL_CHAR_BUDGET,
            max_items_per_layer=int(
                data.get("max_items_per_layer", DEFAULT_MAX_ITEMS_PER_LAYER)
            ),
            max_item_chars=int(data.get("max_item_chars", DEFAULT_MAX_ITEM_CHARS)),
            initial_layer_budgets=initial,
            hard_layer_caps=hard,
            max_l4_file_chars=int(data.get("max_l4_file_chars", 1048576)),
            max_l4_record_chars=int(data.get("max_l4_record_chars", 4096)),
        )
