from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from math import floor

from .contracts import (
    AUTHORITY_RANK,
    LAYERS,
    MnemosyneConfig,
    MnemosyneItem,
)


def freshness_score(created_at: datetime | None) -> float:
    if created_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    age_days = max(
        0,
        int((now - created_at.astimezone(timezone.utc)).total_seconds() // 86400),
    )
    return max(0.0, 1.0 - age_days / 365.0)


def sort_items(items: list[MnemosyneItem]) -> list[MnemosyneItem]:
    return sorted(
        items,
        key=lambda item: (
            -AUTHORITY_RANK[item.layer],
            -item.score,
            -(item.trust if item.trust is not None else 0.5),
            -freshness_score(item.created_at),
            item.item_id,
        ),
    )


def _dedupe_key(item: MnemosyneItem) -> str:
    return " ".join(item.content.casefold().split())


def merge_duplicate_items(items: list[MnemosyneItem]) -> list[MnemosyneItem]:
    groups: dict[str, list[MnemosyneItem]] = defaultdict(list)
    for item in items:
        groups[_dedupe_key(item)].append(item)

    merged: list[MnemosyneItem] = []
    for group in groups.values():
        ordered = sort_items(group)
        primary = ordered[0]
        provenance = tuple(ref for item in ordered for ref in item.provenance)
        merged.append(
            MnemosyneItem(
                item_id=primary.item_id,
                layer=primary.layer,
                content=primary.content,
                reason=primary.reason,
                reason_code=primary.reason_code,
                reason_detail=primary.reason_detail,
                score=primary.score,
                source=primary.source,
                provenance=provenance,
                trust=primary.trust,
                created_at=primary.created_at,
                expires_at=primary.expires_at,
            )
        )
    return sort_items(merged)


def _item_line(
    item: MnemosyneItem, max_item_chars: int, max_line_chars: int
) -> str | None:
    prefix_base = f"- {item.layer}:"
    if max_line_chars <= len(prefix_base) + 1:
        return None

    identifier = item.item_id.strip()[: max_line_chars - len(prefix_base) - 1]
    prefix = f"{prefix_base}{identifier}"
    payload_capacity = max_line_chars - len(prefix) - 2
    if payload_capacity < 0:
        return f"{prefix}\n"

    content = item.content[:max_item_chars].strip()
    reason = item.reason.strip()
    reason_chars = min(len(reason), payload_capacity // 3)
    reason_suffix = f" ({reason[:reason_chars]})" if reason_chars else ""
    content_chars = min(len(content), payload_capacity - len(reason_suffix))
    return f"{prefix} {content[:content_chars]}{reason_suffix}\n"


def scale_config_for_execution(
    config: MnemosyneConfig, execution_kind: str
) -> MnemosyneConfig:
    multiplier = 1.0
    if execution_kind == "cron":
        multiplier = config.cron_multiplier
    elif execution_kind == "subagent":
        multiplier = config.subagent_multiplier
    if multiplier == 1.0:
        return config
    return replace(
        config,
        total_char_budget=floor(config.total_char_budget * multiplier),
        initial_layer_budgets={
            layer: floor(value * multiplier)
            for layer, value in config.initial_layer_budgets.items()
        },
        hard_layer_caps={
            layer: floor(value * multiplier)
            for layer, value in config.hard_layer_caps.items()
        },
    )


def render_context(items: list[MnemosyneItem], config: MnemosyneConfig) -> str:
    sorted_items = sort_items(merge_duplicate_items(items))
    used_by_layer = {layer: 0 for layer in LAYERS}
    emitted_by_layer = {layer: 0 for layer in LAYERS}
    lines = ["## Mnemosyne Memory Tower\n"]

    def remaining_total() -> int:
        return config.total_char_budget - sum(len(line) for line in lines)

    deferred: list[MnemosyneItem] = []
    for item in sorted_items:
        if emitted_by_layer[item.layer] >= config.max_items_per_layer:
            continue
        line = _item_line(
            item,
            config.max_item_chars,
            min(
                config.initial_layer_budgets[item.layer] - used_by_layer[item.layer],
                remaining_total(),
            ),
        )
        if line is not None:
            cost = len(line)
            lines.append(line)
            used_by_layer[item.layer] += cost
            emitted_by_layer[item.layer] += 1
        else:
            deferred.append(item)

    shared_pool = sum(
        max(0, config.initial_layer_budgets[layer] - used_by_layer[layer])
        for layer in LAYERS
    )
    for layer in ("L2", "L1", "L4", "L3"):
        for item in list(deferred):
            if item.layer != layer:
                continue
            if emitted_by_layer[layer] >= config.max_items_per_layer:
                continue
            line = _item_line(
                item,
                config.max_item_chars,
                min(
                    shared_pool,
                    config.hard_layer_caps[layer] - used_by_layer[layer],
                    remaining_total(),
                ),
            )
            if line is None:
                continue
            cost = len(line)
            if cost <= min(
                shared_pool, config.hard_layer_caps[layer] - used_by_layer[layer]
            ) and cost <= remaining_total():
                lines.append(line)
                used_by_layer[layer] += cost
                emitted_by_layer[layer] += 1
                shared_pool -= cost
                deferred.remove(item)

    rendered = "".join(lines).rstrip()
    if len(rendered) > config.total_char_budget:
        return rendered[:config.total_char_budget].rstrip()
    return rendered
