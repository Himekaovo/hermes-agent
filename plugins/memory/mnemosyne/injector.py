from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from math import floor
from pathlib import Path
from typing import Mapping

from .contracts import (
    AUTHORITY_RANK,
    LAYERS,
    MnemosyneConfig,
    MnemosyneItem,
    MnemosyneSourceRef,
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


def collect_l1_items(
    hermes_home: Path,
    *,
    include_sensitive: bool,
    query: str = "",
) -> tuple[list[MnemosyneItem], list[dict[str, object]]]:
    items: list[MnemosyneItem] = []
    diagnostics: list[dict[str, object]] = []
    memory_dir = Path(hermes_home) / "memories"
    filenames = ["MEMORY.md"] + (["USER.md"] if include_sensitive else [])
    for name in filenames:
        path = memory_dir / name
        try:
            resolved = path.resolve()
            resolved.relative_to(Path(hermes_home).resolve())
        except Exception:
            return [], [{"reason": "security_invariant_failure", "source": name}]
        try:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            diagnostics.append({"reason": "l1_encoding_error", "source": name})
            continue
        except OSError:
            diagnostics.append({"reason": "l1_filesystem_error", "source": name})
            continue
        stripped = content.strip()
        if not stripped:
            continue
        source = f"memories/{name}"
        item_id = f"L1:{name}"
        items.append(MnemosyneItem(
            item_id=item_id,
            layer="L1",
            content=stripped,
            reason=f"core profile entry from {name}",
            reason_code="l1_core_file",
            reason_detail=(("file", name),),
            score=1.0,
            source=source,
            provenance=(),
            trust=1.0,
        ))
    return items, diagnostics


def _l4_source_refs(record: Mapping[str, object]) -> tuple[MnemosyneSourceRef, ...]:
    raw_refs = record.get("source_refs")
    if not isinstance(raw_refs, list):
        return ()

    refs: list[MnemosyneSourceRef] = []
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping):
            continue
        layer = raw_ref.get("layer")
        item_id = raw_ref.get("item_id")
        if not isinstance(layer, str) or not isinstance(item_id, str):
            continue
        reason_detail = raw_ref.get("reason_detail")
        if not isinstance(reason_detail, Mapping):
            reason_detail = {}
        try:
            refs.append(MnemosyneSourceRef(
                layer=layer,
                item_id=item_id,
                source=str(raw_ref.get("source") or "mnemosyne:l4"),
                reason_code=str(raw_ref.get("reason_code") or "l4_source_ref"),
                reason_detail=tuple(reason_detail.items()),
            ))
        except (TypeError, ValueError):
            continue
    return tuple(refs)


def collect_l4_items(l4_records: list[dict[str, object]]) -> list[MnemosyneItem]:
    items: list[MnemosyneItem] = []
    for record in l4_records:
        read_state = record.get("read_state")
        if not isinstance(read_state, dict):
            continue
        score = float(read_state.get("decay_score", 0.0))
        archive = bool(read_state.get("archive_recommended", False))
        reason = "L4 candidate"
        if archive:
            reason += "; archive recommended"
        items.append(MnemosyneItem(
            item_id=f"L4:{record.get('record_id')}",
            layer="L4",
            content=str(record.get("content") or ""),
            reason=reason,
            reason_code="l4_candidate_decay",
            reason_detail=(
                ("governance_state", str(record.get("governance_state"))),
                ("archive_recommended", archive),
            ),
            score=score,
            source="mnemosyne:l4",
            provenance=_l4_source_refs(record),
            trust=float(record.get("confidence", 0.5)),
        ))
    return items
