"""Deterministic governance for profile-scoped built-in memory files."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_START_RE = re.compile(r"<!--\s*PROTECTED_START:\s*([^>]+?)\s*-->")
_END_RE = re.compile(r"<!--\s*PROTECTED_END:\s*([^>]+?)\s*-->")
_BOOL_RE = re.compile(
    r"(?P<subject>[\w -]{2,80}?)(?P<negative>does not|do not|never|not)"
    r"\s+(?P<verb>prefer|like|want|use|choose)\s+(?P<object>[\w -]+)",
    re.IGNORECASE,
)
_POS_BOOL_RE = re.compile(
    r"(?P<subject>[\w -]{2,80}?)\s+(?P<verb>prefers?|likes?|wants?|uses?|chooses?)"
    r"\s+(?P<object>[\w -]+)",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value)}


def _normalize(value: str) -> str:
    return " ".join(sorted(_tokens(value)))


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def snapshot(
    path: Path,
    versions_dir: Path,
    *,
    target: str,
    reason: str,
    operation: str,
) -> dict[str, Any]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    created_at = _now()
    snapshot_id = f"{created_at.replace(':', '')}-{target}-{digest[:8]}"
    versions_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = versions_dir / f"{snapshot_id}.md"
    snapshot_path.write_bytes(raw)
    entries = path.read_text(encoding="utf-8").split("\n§\n") if raw else []
    metadata = {
        "id": snapshot_id,
        "target": target,
        "created_at": created_at,
        "source_path": str(path),
        "sha256": digest,
        "reason": reason,
        "operation": operation,
        "entry_count": len(entries),
        "byte_count": len(raw),
    }
    (versions_dir / f"{snapshot_id}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def list_snapshots(
    versions_dir: Path, *, target: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for sidecar in versions_dir.glob("*.json"):
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or (target and value.get("target") != target):
            continue
        if not (versions_dir / f"{value.get('id', sidecar.stem)}.md").exists():
            continue
        records.append(value)
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return records[: max(0, limit)]


def rollback(
    snapshot_id: str,
    *,
    target_path: Path,
    versions_dir: Path,
    target: str,
) -> dict[str, Any]:
    source = versions_dir / f"{snapshot_id}.md"
    if not source.exists():
        return {"success": False, "error": f"Snapshot not found: {snapshot_id}"}
    if target_path.exists():
        snapshot(
            target_path,
            versions_dir,
            target=target,
            reason=f"before rollback {snapshot_id}",
            operation="rollback",
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(source.read_bytes())
    return {"success": True, "snapshot_id": snapshot_id, "target": target}


def protected_entries(entries: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if "<!-- SLOW_UPDATE -->" in entry:
            found.append({"name": "SLOW_UPDATE", "entry_index": index})
        starts = list(_START_RE.finditer(entry))
        ends = list(_END_RE.finditer(entry))
        for start in starts:
            name = start.group(1).strip()
            if any(end.group(1).strip() == name and end.start() > start.end() for end in ends):
                found.append({"name": name, "entry_index": index})
    return found


def _quality_score(entries: list[str]) -> float:
    if not entries:
        return 1.0
    scores = []
    for entry in entries:
        stripped = entry.strip()
        if not stripped or stripped == "§":
            scores.append(0.0)
        elif len(_tokens(stripped)) < 2:
            scores.append(0.2)
        elif len(stripped) > 12000:
            scores.append(0.3)
        else:
            scores.append(min(1.0, 0.45 + len(_tokens(stripped)) / 40))
    return round(sum(scores) / len(scores), 2)


def _preference_signature(entry: str) -> tuple[str, str, str] | None:
    negative = _BOOL_RE.search(entry)
    if negative:
        return (
            " ".join(negative.group("subject").casefold().split()),
            negative.group("object").casefold().strip(),
            "negative",
        )
    positive = _POS_BOOL_RE.search(entry)
    if positive:
        return (
            " ".join(positive.group("subject").casefold().split()),
            positive.group("object").casefold().strip(),
            "positive",
        )
    return None


def _step_match(governance_dir: Path, summary: str) -> dict[str, Any] | None:
    normalized = _normalize(summary)
    for record in _read_jsonl(governance_dir / "step_buffer.jsonl"):
        if record.get("count", 0) >= 2 and _similarity(normalized, str(record.get("normalized_pattern", ""))) >= 0.65:
            return {"pattern": record.get("pattern", ""), "count": record.get("count", 0)}
    return None


def _recommendations(governance_dir: Path, summary: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in _read_jsonl(governance_dir / "meta_skill.jsonl"):
        strategy = str(record.get("strategy", "")).strip()
        if not strategy:
            continue
        item = grouped.setdefault(strategy, {"strategy": strategy, "successes": 0, "failures": 0})
        item["successes" if record.get("result") == "success" else "failures"] += 1
    return [
        item for item in grouped.values()
        if item["successes"] > item["failures"] and _similarity(summary, item["strategy"]) >= 0.2
    ]


def preflight(
    *,
    target: str,
    current_entries: list[str],
    proposed_entries: list[str],
    operation: str,
    governance_dir: Path,
) -> dict[str, Any]:
    quality = _quality_score(proposed_entries)
    base: dict[str, Any] = {
        "allowed": True,
        "target": target,
        "operation": operation,
        "quality_score": quality,
        "conflicts": [],
        "protected": [],
        "recommendations": _recommendations(governance_dir, " ".join(proposed_entries)),
    }
    protected = protected_entries(current_entries)
    changed = {index for index, (old, new) in enumerate(zip(current_entries, proposed_entries)) if old != new}
    changed.update(range(len(proposed_entries), len(current_entries)))
    impacted = [item for item in protected if item["entry_index"] in changed]
    if impacted and operation in {"replace", "remove", "batch", "journey_edit", "journey_delete"}:
        return {**base, "allowed": False, "gate": "protected_region", "protected": impacted}

    if any("\n§\n" in entry or entry.strip() == "§" for entry in proposed_entries):
        return {**base, "allowed": False, "gate": "quality"}

    conflicts: list[dict[str, Any]] = []
    for index, proposed in enumerate(proposed_entries):
        if index < len(current_entries) and proposed == current_entries[index]:
            continue
        for old_index, existing in enumerate(current_entries):
            similarity = _similarity(existing, proposed)
            if len(_tokens(existing)) >= 2 and len(_tokens(proposed)) >= 2 and similarity >= 0.82:
                conflicts.append({"entry_index": old_index, "similarity": round(similarity, 2), "preview": existing[:120]})
            old_sig, new_sig = _preference_signature(existing), _preference_signature(proposed)
            if old_sig and new_sig and old_sig[:2] == new_sig[:2] and old_sig[2] != new_sig[2]:
                conflicts.append({"entry_index": old_index, "similarity": 0.0, "preview": existing[:120], "type": "contradiction"})
    if conflicts:
        return {**base, "allowed": False, "gate": "conflict_detected", "conflicts": conflicts}

    match = _step_match(governance_dir, " ".join(proposed_entries))
    if match:
        return {**base, "allowed": False, "gate": "step_buffer", "step_buffer": match}
    return base


def record_step(governance_dir: Path, pattern: str, note: str | None = None) -> dict[str, Any]:
    normalized = _normalize(pattern)
    records = _read_jsonl(governance_dir / "step_buffer.jsonl")
    for record in records:
        if record.get("normalized_pattern") == normalized:
            record["count"] = int(record.get("count", 0)) + 1
            record["last_seen_at"] = _now()
            if note:
                record["note"] = note
            path = governance_dir / "step_buffer.jsonl"
            path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records), encoding="utf-8")
            return record
    record = {"pattern": pattern, "normalized_pattern": normalized, "count": 1, "first_seen_at": _now(), "last_seen_at": _now(), "note": note}
    _append_jsonl(governance_dir / "step_buffer.jsonl", record)
    return record


def list_steps(governance_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    return sorted(_read_jsonl(governance_dir / "step_buffer.jsonl"), key=lambda item: item.get("last_seen_at", ""), reverse=True)[: max(0, limit)]


def record_meta(governance_dir: Path, strategy: str, result: str, note: str | None = None) -> dict[str, Any]:
    if result not in {"success", "failure"}:
        raise ValueError("result must be success or failure")
    record = {"strategy": strategy, "result": result, "created_at": _now(), "note": note}
    _append_jsonl(governance_dir / "meta_skill.jsonl", record)
    return record


def list_meta(governance_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    return _read_jsonl(governance_dir / "meta_skill.jsonl")[-max(0, limit):][::-1]
