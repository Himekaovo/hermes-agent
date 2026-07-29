from __future__ import annotations

import errno
import json
import os
import secrets
import tempfile
import threading
from contextlib import contextmanager
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
_LOCK_FILENAME = "l4.jsonl.lock"
_DIR_FD_REWRITE_SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
)


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
    _canonical_payload: str = field(repr=False)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "L4CandidateRecord":
        payload = dict(value)
        payload.setdefault("parent_session_id", None)
        payload["governance_state"] = "candidate"
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
        canonical_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if "\n" in canonical_payload:
            raise ValueError("L4 record must serialize to one JSONL line")
        return cls(
            payload=payload,
            record_id=record_id,
            _canonical_payload=canonical_payload,
        )


def _persistence_payload(record: L4CandidateRecord) -> dict[str, object]:
    payload = json.loads(record._canonical_payload)
    if not isinstance(payload, dict):
        raise ValueError("canonical L4 payload must be an object")
    for field in _READ_TIME_FIELDS:
        payload.pop(field, None)
    payload.setdefault("parent_session_id", None)
    if payload["parent_session_id"] is not None and not isinstance(payload["parent_session_id"], str):
        raise ValueError("parent_session_id must be a string or None")
    if candidate_record_id(payload) != record.record_id:
        raise ValueError("canonical L4 payload does not match record_id")
    payload["record_id"] = record.record_id
    payload["governance_state"] = "candidate"
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

    @contextmanager
    def _trusted_parent_dir_fd(self, *, create: bool):
        """Hold the real L4 parent directory open while a write is in flight."""
        if not _DIR_FD_REWRITE_SUPPORTED:
            yield None
            return

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fds: list[int] = []
        try:
            try:
                current_fd = os.open(str(self.hermes_home), flags)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise SecurityInvariantError(
                        f"trusted root is not a directory: {self.hermes_home}"
                    ) from exc
                raise
            fds.append(current_fd)
            for part in L4_RELATIVE_PATH.parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    if create:
                        raise
                    yield None
                    return
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise SecurityInvariantError(
                            f"path traverses symlink: {self.path.parent}"
                        ) from exc
                    raise
                fds.append(next_fd)
                current_fd = next_fd
            yield current_fd
        finally:
            for fd in reversed(fds):
                os.close(fd)

    @contextmanager
    def _interprocess_lock(self, parent_fd: Optional[int]):
        """Serialize L4 read-modify-replace cycles across local processes."""
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            if parent_fd is None:
                lock_path = self.path.with_name(_LOCK_FILENAME)
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                assert_trusted_path(lock_path, self.hermes_home)
                lock_fd = os.open(str(lock_path), flags, 0o600)
            else:
                lock_fd = os.open(_LOCK_FILENAME, flags, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise SecurityInvariantError(f"path traverses symlink: {self.path.parent}") from exc
            raise

        unlock = None
        try:
            try:
                import fcntl

                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                unlock = lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except (ImportError, OSError):
                try:
                    import msvcrt

                    if os.fstat(lock_fd).st_size == 0:
                        os.write(lock_fd, b"0")
                    os.lseek(lock_fd, 0, os.SEEK_SET)
                    msvcrt.locking(lock_fd, msvcrt.LK_LOCK, 1)
                    unlock = lambda: msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                except (ImportError, OSError):
                    unlock = None
            yield
        finally:
            if unlock is not None:
                try:
                    unlock()
                except OSError:
                    pass
            os.close(lock_fd)

    def _read_raw_lines(self, *, parent_fd: Optional[int] = None) -> list[bytes]:
        if parent_fd is None and _DIR_FD_REWRITE_SUPPORTED:
            with self._trusted_parent_dir_fd(create=False) as trusted_parent_fd:
                if trusted_parent_fd is None:
                    return []
                return self._read_raw_lines(parent_fd=trusted_parent_fd)

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            if parent_fd is None:
                self.ensure_available()
                if not self.path.exists():
                    return []
                fd = os.open(self.path, flags)
            else:
                fd = os.open("l4.jsonl", flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return []
        except OSError as exc:
            if exc.errno == errno.ELOOP or self.path.is_symlink():
                raise SecurityInvariantError(f"path traverses symlink: {self.path}") from exc
            raise
        with os.fdopen(fd, "rb") as handle:
            return handle.read().splitlines(keepends=True)

    def _rewrite_with_dir_fd(self, parent_fd: int, content: bytes) -> None:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
        for _ in range(32):
            temp_name = f".l4-{secrets.token_hex(12)}.tmp"
            try:
                fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("could not create unique L4 temporary file")

        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temp_name,
                "l4.jsonl",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    def _rewrite_fallback(self, content: bytes) -> None:
        self.ensure_available()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_available()
        fd, temp_name = tempfile.mkstemp(prefix=".l4-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self.ensure_available()
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def write_candidate(self, record: L4CandidateRecord) -> dict[str, object]:
        if self._disabled:
            return {"written": False, "reason": "provider_disabled"}
        try:
            self.ensure_available()
        except SecurityInvariantError:
            self._disabled = True
            raise
        persistence_payload = _persistence_payload(record)
        if (
            record.payload.get("profile_id") != self.profile_id
            or persistence_payload.get("profile_id") != self.profile_id
        ):
            raise ValueError("record profile_id does not match L4 store profile_id")
        line_text = json.dumps(persistence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        line_bytes = line_text.encode("utf-8")
        if (
            "\n" in line_text
            or len(line_text) > self.config.max_l4_record_chars
            or len(line_bytes) > self.config.max_l4_record_chars
        ):
            return {"written": False, "reason": "record_too_large"}
        line = line_bytes + b"\n"
        try:
            with self._lock():
                with self._trusted_parent_dir_fd(create=True) as parent_fd:
                    with self._interprocess_lock(parent_fd):
                        raw_lines = self._read_raw_lines(parent_fd=parent_fd)
                        valid_ids = set()
                        for raw in raw_lines:
                            try:
                                parsed = json.loads(raw.decode("utf-8"))
                            except Exception:
                                continue
                            if isinstance(parsed, dict) and isinstance(parsed.get("record_id"), str):
                                if parsed.get("profile_id") != self.profile_id:
                                    raise SecurityInvariantError(
                                        "existing L4 record profile_id does not match L4 store profile_id"
                                    )
                                valid_ids.add(parsed["record_id"])
                        if record.record_id in valid_ids:
                            return {"written": False, "reason": "duplicate"}
                        existing = b"".join(raw_lines)
                        separator = b"" if not existing or existing.endswith((b"\n", b"\r")) else b"\n"
                        final = existing + separator + line
                        if (
                            len(final.decode("utf-8", errors="ignore")) > self.config.max_l4_file_chars
                            or len(final) > self.config.max_l4_file_chars
                        ):
                            return {"written": False, "reason": "file_too_large"}
                        if parent_fd is None:
                            self._rewrite_fallback(final)
                        else:
                            self._rewrite_with_dir_fd(parent_fd, final)
        except SecurityInvariantError:
            self._disabled = True
            raise
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
