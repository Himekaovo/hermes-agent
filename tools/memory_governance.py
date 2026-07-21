"""Deterministic governance for profile-scoped built-in memory files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
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


@contextmanager
def _jsonl_lock(path: Path):
    """Serialize JSONL read-modify-write operations across local processes."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    windows_lock = None
    try:
        try:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
        except (ImportError, OSError):
            try:
                import msvcrt

                windows_lock = msvcrt
                handle.seek(0)
                if handle.tell() == 0:
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            except (ImportError, OSError):
                windows_lock = None
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_UN)
        except (ImportError, OSError):
            if windows_lock is not None:
                try:
                    handle.seek(0)
                    windows_lock.locking(handle.fileno(), windows_lock.LK_UNLCK, 1)
                except OSError:
                    pass
        handle.close()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".governance-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".governance-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


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
    _atomic_write_bytes(snapshot_path, raw)
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
    _atomic_write_text(
        versions_dir / f"{snapshot_id}.json",
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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
    if not snapshot_id or Path(snapshot_id).name != snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
        return {"success": False, "error": "Invalid snapshot id"}
    sidecar_path = versions_dir / f"{snapshot_id}.json"
    try:
        metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"success": False, "error": f"Snapshot metadata not found: {snapshot_id}"}
    if not isinstance(metadata, dict) or metadata.get("id") != snapshot_id:
        return {"success": False, "error": "Snapshot metadata does not match id"}
    if metadata.get("target") != target:
        return {"success": False, "error": "Snapshot target does not match rollback target"}
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
    _atomic_write_bytes(target_path, source.read_bytes())
    return {"success": True, "snapshot_id": snapshot_id, "target": target}


def protected_entries(entries: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    spans: list[tuple[str, int]] = []
    marked: set[tuple[str, int]] = set()
    for index, entry in enumerate(entries):
        if "<!-- SLOW_UPDATE -->" in entry:
            found.append({"name": "SLOW_UPDATE", "entry_index": index})
        markers = []
        markers.extend((match.start(), "start", match.group(1).strip()) for match in _START_RE.finditer(entry))
        markers.extend((match.start(), "end", match.group(1).strip()) for match in _END_RE.finditer(entry))
        for _, kind, name in sorted(markers):
            if kind == "start":
                spans.append((name, index))
                continue
            matching = next((position for position in range(len(spans) - 1, -1, -1) if spans[position][0] == name), None)
            if matching is None:
                found.append({"name": name, "entry_index": index, "malformed": True})
                continue
            _, start_index = spans.pop(matching)
            for protected_index in range(start_index, index + 1):
                key = (name, protected_index)
                if key not in marked:
                    found.append({"name": name, "entry_index": protected_index})
                    marked.add(key)
    for name, start_index in spans:
        for protected_index in range(start_index, len(entries)):
            key = (name, protected_index)
            if key not in marked:
                found.append({"name": name, "entry_index": protected_index, "malformed": True})
                marked.add(key)
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
    for record in _aggregate_step_records(_read_jsonl(governance_dir / "step_buffer.jsonl")):
        if record.get("count", 0) >= 2 and _similarity(normalized, str(record.get("normalized_pattern", ""))) >= 0.65:
            return {"pattern": record.get("pattern", ""), "count": record.get("count", 0)}
    return None


def _aggregate_step_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        normalized = str(record.get("normalized_pattern", "")).strip()
        if not normalized:
            continue
        if normalized not in grouped:
            grouped[normalized] = dict(record)
            grouped[normalized]["count"] = int(record.get("count", 1))
            continue
        item = grouped[normalized]
        item["count"] = int(item.get("count", 0)) + int(record.get("count", 1))
        item["first_seen_at"] = min(item.get("first_seen_at", ""), record.get("first_seen_at", ""))
        item["last_seen_at"] = max(item.get("last_seen_at", ""), record.get("last_seen_at", ""))
        if record.get("note"):
            item["note"] = record["note"]
    return list(grouped.values())


def _recommendations(governance_dir: Path, summary: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in _read_jsonl(governance_dir / "meta_skill.jsonl"):
        strategy = str(record.get("strategy", "")).strip()
        if not strategy:
            continue
        key = _normalize(strategy)
        item = grouped.setdefault(key, {"strategy": strategy, "successes": 0, "failures": 0})
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
    matching_old_indexes: set[int] = set()
    for old_start, new_start, size in SequenceMatcher(
        a=current_entries, b=proposed_entries, autojunk=False
    ).get_matching_blocks():
        matching_old_indexes.update(range(old_start, old_start + size))
    impacted = [
        item for item in protected
        if item["entry_index"] not in matching_old_indexes
    ]
    if impacted and operation in {"replace", "remove", "batch", "journey_edit", "journey_delete"}:
        return {**base, "allowed": False, "gate": "protected_region", "protected": impacted}

    if any("\n§\n" in entry for entry in proposed_entries):
        return {**base, "allowed": False, "gate": "delimiter_abuse"}
    if any(
        not entry.strip()
        or entry.strip() == "§"
        or (len(entry) > 12000 and any(char.isspace() for char in entry))
        for entry in proposed_entries
    ):
        return {**base, "allowed": False, "gate": "quality"}

    conflicts: list[dict[str, Any]] = []
    if operation in {"remove", "journey_delete"}:
        candidates: list[tuple[int, str]] = []
    elif operation in {"replace", "journey_edit"}:
        candidates = [
            (index, proposed)
            for index, proposed in enumerate(proposed_entries)
            if index >= len(current_entries) or proposed != current_entries[index]
        ]
    else:
        if operation == "add":
            candidates = [
                (index, proposed)
                for index, proposed in enumerate(proposed_entries)
                if index >= len(current_entries)
            ]
        else:
            candidates = [
                (index, proposed)
                for index, proposed in enumerate(proposed_entries)
                if proposed not in current_entries
            ]
    for index, proposed in candidates:
        for old_index, existing in enumerate(current_entries):
            if operation in {"replace", "journey_edit"} and old_index == index:
                continue
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
    if not pattern or not pattern.strip():
        raise ValueError("step pattern cannot be empty")
    normalized = _normalize(pattern)
    path = governance_dir / "step_buffer.jsonl"
    with _jsonl_lock(path):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
        for index, line in enumerate(lines):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict) or record.get("normalized_pattern") != normalized:
                continue
            record["count"] = int(record.get("count", 0)) + 1
            record["last_seen_at"] = _now()
            if note:
                record["note"] = note
            lines[index] = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            _atomic_write_text(path, "".join(lines))
            return record
        record = {"pattern": pattern, "normalized_pattern": normalized, "count": 1, "first_seen_at": _now(), "last_seen_at": _now(), "note": note}
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        _atomic_write_text(path, "".join(lines))
        return record


def list_steps(governance_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    return sorted(
        _aggregate_step_records(_read_jsonl(governance_dir / "step_buffer.jsonl")),
        key=lambda item: item.get("last_seen_at", ""),
        reverse=True,
    )[: max(0, limit)]


def record_meta(governance_dir: Path, strategy: str, result: str, note: str | None = None) -> dict[str, Any]:
    if not strategy or not strategy.strip():
        raise ValueError("strategy cannot be empty")
    if result not in {"success", "failure"}:
        raise ValueError("result must be success or failure")
    record = {"strategy": strategy, "result": result, "created_at": _now(), "note": note}
    path = governance_dir / "meta_skill.jsonl"
    with _jsonl_lock(path):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        _atomic_write_text(path, existing + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def list_meta(governance_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    return _read_jsonl(governance_dir / "meta_skill.jsonl")[-max(0, limit):][::-1]
