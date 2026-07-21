"""SQLite-backed provenance and lifecycle data for imported skills."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    from tools.skills_hub import SkillBundle, bundle_content_hash, source_url_for_bundle
except ImportError:  # pragma: no cover - supports a minimal standalone import
    SkillBundle = Any
    bundle_content_hash = None
    source_url_for_bundle = None


LIFECYCLE_STATUSES = (
    "raw",
    "candidate",
    "draft",
    "verified",
    "release",
    "degraded",
    "deprecated",
    "archived",
)
RELATIONS = ("inspired_by", "depends_on", "references")

ALLOWED_TRANSITIONS = {
    "raw": {"candidate", "archived"},
    "candidate": {"draft", "deprecated", "archived"},
    "draft": {"verified", "degraded", "deprecated", "archived"},
    "verified": {"release", "degraded", "deprecated", "archived"},
    "release": {"degraded", "deprecated", "archived"},
    "degraded": {"draft", "deprecated", "archived"},
    "deprecated": {"archived"},
    "archived": set(),
}


@dataclass
class SkillWikiResult:
    """Stable result envelope shared by every database operation."""

    available: bool
    reason: Optional[str] = None
    items: list[dict] = field(default_factory=list)
    value: Optional[dict] = None


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    repo TEXT,
    path TEXT,
    ref TEXT,
    commit_sha TEXT,
    source_url TEXT,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    local_path TEXT NOT NULL,
    local_modified_count INTEGER NOT NULL DEFAULT 0,
    version TEXT,
    status TEXT NOT NULL CHECK (status IN ({', '.join(repr(s) for s in LIFECYCLE_STATUSES)})),
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    from_skill_id TEXT NOT NULL,
    to_skill_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('inspired_by', 'depends_on', 'references')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_skill_id, to_skill_id, relation),
    FOREIGN KEY (from_skill_id) REFERENCES skills(skill_id)
);
CREATE TABLE IF NOT EXISTS lifecycle_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(files: dict[str, str | bytes]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(files):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\x00")
        content = files[relative_path]
        digest.update(content if isinstance(content, bytes) else content.encode("utf-8"))
    return f"sha256:{digest.hexdigest()[:16]}"


def _bundle_hash(bundle: SkillBundle) -> str:
    if bundle_content_hash is not None:
        return bundle_content_hash(bundle)
    return _stable_hash(bundle.files)


def _local_hash(path: Path) -> Optional[str]:
    if not path.is_dir():
        return None
    files: dict[str, bytes] = {}
    for child in path.rglob("*"):
        if child.is_file():
            files[child.relative_to(path).as_posix()] = child.read_bytes()
    return _stable_hash(files)


def _row(row: sqlite3.Row) -> dict:
    result = dict(row)
    try:
        metadata = json.loads(result["metadata_json"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("malformed metadata") from exc
    if not isinstance(metadata, dict):
        raise ValueError("malformed metadata")
    result["metadata"] = metadata
    return result


def _skill_id(bundle: SkillBundle) -> tuple[str, Optional[str], Optional[str]]:
    identifier = str(bundle.identifier)
    if bundle.source == "github":
        parts = identifier.split("/", 2)
        repo = "/".join(parts[:2]) if len(parts) >= 2 else identifier
        path = parts[2] if len(parts) == 3 else ""
        return f"github:{repo}:{path}", repo, path
    return f"{bundle.source}:{identifier}", None, None


class SkillWiki:
    """Best-effort access to the SkillWiki SQLite database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path), timeout=2)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA)
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _unavailable() -> SkillWikiResult:
        return SkillWikiResult(False, "database_unavailable")

    def record_import(
        self,
        bundle: SkillBundle,
        local_path: Path,
        *,
        ref: Optional[str] = None,
        commit_sha: Optional[str] = None,
    ) -> SkillWikiResult:
        try:
            now = _now()
            skill_id, repo, path = _skill_id(bundle)
            content_hash = _bundle_hash(bundle)
            metadata = dict(bundle.metadata or {})
            metadata.setdefault("trust_level", bundle.trust_level)
            source_url = source_url_for_bundle(bundle) if source_url_for_bundle else bundle.identifier
            with self._connection() as db:
                existing = db.execute(
                    "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
                ).fetchone()
                modified_count = 0 if existing is None else int(existing["local_modified_count"])
                if existing is not None:
                    local_hash = _local_hash(Path(local_path))
                    prior_metadata = _row(existing)["metadata"]
                    prior_local_hash = prior_metadata.get("_last_local_hash")
                    if local_hash is not None and local_hash != content_hash and local_hash != prior_local_hash:
                        modified_count += 1
                    if local_hash is not None:
                        metadata["_last_local_hash"] = local_hash
                    status = existing["status"]
                    created_at = existing["created_at"]
                    db.execute(
                        """UPDATE skills SET name=?, source=?, repo=?, path=?, ref=?,
                        commit_sha=?, source_url=?, content_hash=?, imported_at=?,
                        local_path=?, local_modified_count=?, version=?, metadata_json=?,
                        updated_at=? WHERE skill_id=?""",
                        (
                            bundle.name, bundle.source, repo, path, ref, commit_sha,
                            source_url, content_hash, now, str(local_path), modified_count,
                            metadata.get("version"), json.dumps(metadata, sort_keys=True),
                            now, skill_id,
                        ),
                    )
                else:
                    status = "raw"
                    created_at = now
                    local_hash = _local_hash(Path(local_path))
                    if local_hash is not None:
                        metadata["_last_local_hash"] = local_hash
                    db.execute(
                        """INSERT INTO skills (skill_id, name, source, repo, path, ref,
                        commit_sha, source_url, content_hash, imported_at, local_path,
                        local_modified_count, version, status, metadata_json, created_at,
                        updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            skill_id, bundle.name, bundle.source, repo, path, ref, commit_sha,
                            source_url, content_hash, now, str(local_path), 0,
                            metadata.get("version"), status, json.dumps(metadata, sort_keys=True),
                            created_at, now,
                        ),
                    )
                record = db.execute(
                    "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
                ).fetchone()
                return SkillWikiResult(True, items=[_row(record)], value=_row(record))
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def get_skill(self, skill_id: str) -> SkillWikiResult:
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
                value = _row(row) if row else None
                return SkillWikiResult(True, items=[value] if value else [], value=value)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def list_skills(self) -> SkillWikiResult:
        try:
            with self._connection() as db:
                rows = db.execute("SELECT * FROM skills ORDER BY skill_id").fetchall()
                return SkillWikiResult(True, items=[_row(row) for row in rows])
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def add_relation(self, from_skill_id: str, to_skill_id: str, relation: str) -> SkillWikiResult:
        try:
            if relation not in RELATIONS:
                return SkillWikiResult(False, "invalid_relation")
            with self._connection() as db:
                db.execute(
                    "INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?)",
                    (from_skill_id, to_skill_id, relation, _now()),
                )
                item = {"from_skill_id": from_skill_id, "to_skill_id": to_skill_id, "relation": relation}
                return SkillWikiResult(True, items=[item], value=item)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def remove_relation(self, from_skill_id: str, to_skill_id: str, relation: str) -> SkillWikiResult:
        try:
            with self._connection() as db:
                cursor = db.execute(
                    "DELETE FROM relations WHERE from_skill_id=? AND to_skill_id=? AND relation=?",
                    (from_skill_id, to_skill_id, relation),
                )
                return SkillWikiResult(True, items=[{"removed": cursor.rowcount > 0}])
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def list_relations(self, skill_id: Optional[str] = None) -> SkillWikiResult:
        try:
            with self._connection() as db:
                if skill_id is None:
                    rows = db.execute("SELECT * FROM relations ORDER BY from_skill_id, to_skill_id, relation").fetchall()
                else:
                    rows = db.execute(
                        "SELECT * FROM relations WHERE from_skill_id=? OR to_skill_id=? ORDER BY from_skill_id, to_skill_id, relation",
                        (skill_id, skill_id),
                    ).fetchall()
                return SkillWikiResult(True, items=[dict(row) for row in rows])
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def transition(
        self,
        skill_id: str,
        to_status: str,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> SkillWikiResult:
        try:
            if to_status not in LIFECYCLE_STATUSES:
                return SkillWikiResult(False, "invalid_status")
            if not actor.strip():
                return SkillWikiResult(False, "invalid_actor")
            with self._connection() as db:
                current = db.execute("SELECT * FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
                if current is None:
                    return SkillWikiResult(False, "skill_not_found")
                if to_status not in ALLOWED_TRANSITIONS[current["status"]]:
                    return SkillWikiResult(False, "invalid_transition")
                now = _now()
                db.execute("UPDATE skills SET status=?, updated_at=? WHERE skill_id=?", (to_status, now, skill_id))
                db.execute(
                    "INSERT INTO lifecycle_events (skill_id, from_status, to_status, actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (skill_id, current["status"], to_status, actor, reason, now),
                )
                value = _row(db.execute("SELECT * FROM skills WHERE skill_id=?", (skill_id,)).fetchone())
                return SkillWikiResult(True, items=[value], value=value)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return self._unavailable()

    def check(self, skill_id: Optional[str] = None) -> dict:
        result = self.list_skills() if skill_id is None else self.get_skill(skill_id)
        if not result.available:
            return {"available": False, "reason": result.reason, "skills": []}
        skills = result.items
        checked = []
        for skill in skills:
            local_path = Path(skill["local_path"])
            exists = local_path.is_dir()
            local_hash = None
            hash_available = False
            diagnostic = None
            if exists:
                try:
                    local_hash = _local_hash(local_path)
                    hash_available = True
                except OSError:
                    diagnostic = "local_hash_unavailable"
            checked.append({
                **skill,
                "local_path_exists": exists,
                "local_hash_available": hash_available,
                "content_drift": bool(hash_available and exists and local_hash != skill["content_hash"]),
                **({"diagnostic": diagnostic} if diagnostic else {}),
            })
        return {"available": True, "reason": None, "skills": checked}

    def advisory_evaluation(self, skill_id: Optional[str] = None) -> dict:
        report = self.check(skill_id)
        if not report["available"]:
            return report
        for skill in report["skills"]:
            skill["advisory"] = "local_content_drift" if skill["content_drift"] else "ok"
        return report


__all__ = ["LIFECYCLE_STATUSES", "RELATIONS", "SkillWiki", "SkillWikiResult"]
