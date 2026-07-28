from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Optional

from .contracts import MnemosyneConfig

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
L4_RELATIVE_PATH = Path("memories") / "mnemosyne" / "l4.jsonl"
_READ_TIME_FIELDS = ("decay_score", "age_days", "archive_recommended", "read_state")


class SecurityInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class L4ReadState:
    age_days: int
    decay_score: float
    archive_recommended: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _canonical_source_refs(source_refs: object) -> str:
    if not isinstance(source_refs, list):
        return "[]"
    normalized = []
    for ref in source_refs:
        if isinstance(ref, Mapping):
            normalized.append({str(k): str(v) for k, v in sorted(ref.items())})
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _canonical_parent_session_id(value: object) -> str:
    if value is None:
        return "null"
    if not isinstance(value, str):
        raise ValueError("parent_session_id must be a string or None")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def candidate_record_id(record: Mapping[str, object]) -> str:
    key = "\0".join([
        str(record.get("profile_id") or ""),
        str(record.get("session_id") or ""),
        _canonical_parent_session_id(record.get("parent_session_id")),
        str(record.get("agent_id") or ""),
        str(record.get("execution_kind") or ""),
        str(record.get("kind") or ""),
        " ".join(str(record.get("content") or "").casefold().split()),
        _canonical_source_refs(record.get("source_refs")),
    ])
    return "l4_" + sha256(key.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class L4CandidateRecord:
    payload: dict[str, object]
    record_id: str
    _canonical_payload: dict[str, object] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "L4CandidateRecord":
        payload = dict(value)
        payload.setdefault("parent_session_id", None)
        payload.setdefault("governance_state", "candidate")
        payload.setdefault("created_at", _now_iso())
        payload.setdefault("last_validated_at", None)
        payload.setdefault("archived_at", None)
        for field in _READ_TIME_FIELDS:
            payload.pop(field, None)
        if (
            payload["parent_session_id"] is not None
            and not isinstance(payload["parent_session_id"], str)
        ):
            raise ValueError("parent_session_id must be a string or None")
        record_id = candidate_record_id(payload)
        payload["record_id"] = record_id
        if "\n" in json.dumps(payload, sort_keys=True, ensure_ascii=False):
            raise ValueError("L4 record must serialize to one JSONL line")
        return cls(
            payload=payload,
            record_id=record_id,
            _canonical_payload=deepcopy(payload),
        )


def _persistence_payload(record: L4CandidateRecord) -> dict[str, object]:
    payload = deepcopy(record._canonical_payload or record.payload)
    for field in _READ_TIME_FIELDS:
        payload.pop(field, None)
    payload.setdefault("parent_session_id", None)
    if payload["parent_session_id"] is not None and not isinstance(payload["parent_session_id"], str):
        raise ValueError("parent_session_id must be a string or None")
    payload["record_id"] = record.record_id
    return payload


def assert_trusted_path(path: Path, root: Path) -> Path:
    lexical_root = root.absolute()
    lexical_path = path.absolute()
    try:
        relative_path = lexical_path.relative_to(lexical_root)
    except ValueError as exc:
        raise SecurityInvariantError(f"path escapes trusted root: {path}") from exc
    current = lexical_root
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            raise SecurityInvariantError(f"path traverses symlink: {current}")
    resolved_root = lexical_root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise SecurityInvariantError(f"path escapes trusted root: {path}") from exc
    return resolved_path


def _derive_read_state(record: Mapping[str, object], *, now: datetime) -> Optional[L4ReadState]:
    reference = _parse_utc(record.get("last_validated_at")) or _parse_utc(record.get("created_at"))
    if reference is None:
        return None
    age_days = max(0, int((now - reference).total_seconds() // 86400))
    decay_score = max(0.0, 1.0 - age_days / 180.0)
    return L4ReadState(
        age_days=age_days,
        decay_score=decay_score,
        archive_recommended=age_days >= 90,
    )


class L4Store:
    def __init__(self, hermes_home: str | Path, *, profile_id: str, config: MnemosyneConfig) -> None:
        self.hermes_home = Path(hermes_home)
        self.profile_id = profile_id
        self.config = config
        self.path = self.hermes_home / L4_RELATIVE_PATH
        self._disabled = False

    def ensure_available(self) -> None:
        assert_trusted_path(self.path.parent, self.hermes_home)
        assert_trusted_path(self.path, self.hermes_home)

    def _lock(self) -> threading.RLock:
        key = str(self.path)
        with _LOCKS_GUARD:
            lock = _LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _LOCKS[key] = lock
            return lock

    def _read_raw_lines(self) -> list[bytes]:
        self.ensure_available()
        if not self.path.exists():
            return []
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags)
        except OSError as exc:
            if self.path.is_symlink() or exc.errno == errno.ELOOP:
                raise SecurityInvariantError(f"path traverses symlink: {self.path}") from exc
            raise
        with os.fdopen(fd, "rb") as handle:
            return handle.read().splitlines(keepends=True)

    def write_candidate(self, record: L4CandidateRecord) -> dict[str, object]:
        if self._disabled:
            return {"written": False, "reason": "provider_disabled"}
        try:
            self.ensure_available()
        except SecurityInvariantError:
            self._disabled = True
            raise
        if record.payload.get("profile_id") != self.profile_id:
            raise ValueError("record profile_id does not match L4 store profile_id")
        line_text = json.dumps(_persistence_payload(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if "\n" in line_text or len(line_text) > self.config.max_l4_record_chars:
            return {"written": False, "reason": "record_too_large"}
        line = (line_text + "\n").encode("utf-8")
        with self._lock():
            self.ensure_available()
            raw_lines = self._read_raw_lines()
            valid_ids = set()
            for raw in raw_lines:
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("record_id"), str):
                    valid_ids.add(parsed["record_id"])
            if record.record_id in valid_ids:
                return {"written": False, "reason": "duplicate"}
            existing = b"".join(raw_lines)
            separator = b"" if not existing or existing.endswith((b"\n", b"\r")) else b"\n"
            final = existing + separator + line
            if len(final.decode("utf-8", errors="ignore")) > self.config.max_l4_file_chars:
                return {"written": False, "reason": "file_too_large"}
            self.ensure_available()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.ensure_available()
            fd, tmp_name = tempfile.mkstemp(prefix=".l4-", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(final)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.ensure_available()
                os.replace(tmp_name, self.path)
                try:
                    dir_fd = os.open(str(self.path.parent), os.O_DIRECTORY)
                except (AttributeError, OSError):
                    dir_fd = None
                if dir_fd is not None:
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        return {"written": True, "record_id": record.record_id}

    def read_records(self, now: Optional[datetime] = None) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        if self._disabled:
            return [], [{"reason": "provider_disabled"}]
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        records: list[dict[str, object]] = []
        diagnostics: list[dict[str, object]] = []
        try:
            self.ensure_available()
        except SecurityInvariantError as exc:
            self._disabled = True
            return [], [{"reason": "security_invariant_failure", "message": str(exc)}]
        try:
            raw_lines = self._read_raw_lines()
        except SecurityInvariantError as exc:
            self._disabled = True
            return [], [{"reason": "security_invariant_failure", "message": str(exc)}]
        for index, raw in enumerate(raw_lines):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                diagnostics.append({"reason": "malformed_l4_line", "line": index})
                continue
            if not isinstance(parsed, dict):
                diagnostics.append({"reason": "invalid_l4_record", "line": index})
                continue
            if parsed.get("profile_id") != self.profile_id:
                self._disabled = True
                return [], [{"reason": "profile_mismatch", "line": index}]
            if parsed.get("governance_state") in {"rejected", "archived"}:
                continue
            state = _derive_read_state(parsed, now=now)
            if state is None:
                diagnostics.append({"reason": "invalid_timestamp", "record_id": parsed.get("record_id")})
                continue
            item = dict(parsed)
            for field in _READ_TIME_FIELDS:
                item.pop(field, None)
            item["read_state"] = {
                "age_days": state.age_days,
                "decay_score": state.decay_score,
                "archive_recommended": state.archive_recommended,
            }
            records.append(item)
        return records, diagnostics
