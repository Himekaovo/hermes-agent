# Mnemosyne Memory Tower Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in local `mnemosyne` memory provider that orchestrates existing MyHermes L1/L2/L3 memory capabilities plus a new L4 candidate store into a bounded, explainable Memory Tower.

**Architecture:** `plugins/memory/mnemosyne/` owns only orchestration, immutable recall contracts, deterministic injection budgeting, L4 candidate JSONL storage, and a local-only bridge protocol. L1 writes remain under Memory Governance, L2 writes are out of scope, L3 remains SkillWiki-owned, and L4 writes create candidates only. The provider plugs into the existing `MemoryProvider` lifecycle and does not modify the Agent main loop.

**Tech Stack:** Python 3 standard library (`dataclasses`, `datetime`, `hashlib`, `json`, `os`, `pathlib`, `tempfile`, `threading`, `typing`), existing `agent.memory_provider.MemoryProvider`, existing `tools.memory_tool.MemoryStore`, existing `plugins.memory.holographic` APIs, existing `tools.skillwiki.SkillWiki`, pytest.

## Global Constraints

- Implement A1: bundled local `MnemosyneProvider` under `plugins/memory/mnemosyne/`.
- Activate only through existing `memory.provider: mnemosyne`; do not enable by default.
- Do not rewrite the Agent main loop.
- Do not bypass Memory Governance to mutate `MEMORY.md` or `USER.md`.
- Do not bypass the holographic fact-store owner to mutate L2 facts; Mnemosyne v1 performs no L2 writes.
- Do not automatically modify L1 Core memory, promote memories, delete memory, archive memory, install skills, upgrade skills, repair skills, or change SkillWiki lifecycle states.
- Do not add network calls, dynamic URLs, credential reads, background sync threads, remote bridges, external memory backends, or model calls.
- L4 is the only new persistence path and stores candidate records only at `memories/mnemosyne/l4.jsonl`.
- L4 records persist one state field only: `governance_state`.
- `decay_score`, `age_days`, and `archive_recommended` are read-time derived values and are never persisted by v1.
- `parent_session_id` is stored as `str | None` on L4 records; it participates in isolation checks and candidate idempotency.
- `cron_multiplier` and `subagent_multiplier` scale `total_char_budget`, every `initial_layer_budgets` value, and every `hard_layer_caps` value with `floor(base * multiplier)`.
- Every task starts with failing tests and verifies the expected failure before production code.
- Keep existing unrelated working-tree changes out of every Mnemosyne commit.

---

## File Map

- Create: `plugins/memory/mnemosyne/__init__.py` — `MnemosyneProvider` lifecycle implementation and provider registration surface.
- Create: `plugins/memory/mnemosyne/plugin.yaml` — bundled provider metadata for discovery.
- Create: `plugins/memory/mnemosyne/contracts.py` — frozen dataclasses, deep-freeze helpers, reason sanitization, score clamp, and provider configuration.
- Create: `plugins/memory/mnemosyne/l4_store.py` — profile-scoped L4 JSONL candidate storage, idempotency, read-time decay, locks, atomic rewrite, and security invariant checks.
- Create: `plugins/memory/mnemosyne/injector.py` — layer adapters, deterministic intent routing, cross-layer merge, budgeting, sorting, and context rendering.
- Create: `plugins/memory/mnemosyne/bridge.py` — local-only `MnemosyneBridge` protocol with no registered default implementation.
- Create: `tests/plugins/memory/test_mnemosyne.py` — focused provider, contracts, L4, injector, and lifecycle tests.
- Modify: `tests/agent/test_memory_provider.py` — provider discovery smoke coverage if the existing discovery test needs explicit `mnemosyne` assertion.
- Create: `docs/mnemosyne.md` — user-facing opt-in and boundary reference, only after provider behavior is green.

## Task 1: Add immutable contracts, config defaults, and discovery shell

**Files:**
- Create: `plugins/memory/mnemosyne/contracts.py`
- Create: `plugins/memory/mnemosyne/__init__.py`
- Create: `plugins/memory/mnemosyne/plugin.yaml`
- Create: `tests/plugins/memory/test_mnemosyne.py`
- Modify: `tests/agent/test_memory_provider.py`

**Interfaces:**
- Produces:
  - `MnemosyneSourceRef(layer: str, item_id: str, source: str, reason_code: str, reason_detail: tuple[tuple[str, object], ...] = ())`
  - `MnemosyneItem(item_id: str, layer: str, content: str, reason: str, reason_code: str, reason_detail: tuple[tuple[str, object], ...], score: float, source: str, provenance: tuple[MnemosyneSourceRef, ...], trust: float | None = None, created_at: datetime | None = None, expires_at: datetime | None = None)`
  - `MnemosyneConfig.from_mapping(mapping: Mapping[str, object] | None, execution_kind: str = "interactive") -> MnemosyneConfig`
  - `sanitize_reason_text(text: str) -> str`
  - `freeze_reason_detail(value: object) -> tuple[tuple[str, object], ...]`
  - `MnemosyneProvider`, a concrete `MemoryProvider`
- Consumes: `agent.memory_provider.MemoryProvider`

- [ ] **Step 1: Write failing contract and provider discovery tests.**

Add these tests to `tests/plugins/memory/test_mnemosyne.py`:

```python
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest


def test_mnemosyne_items_deep_freeze_provenance_and_reason_detail():
    from plugins.memory.mnemosyne.contracts import (
        MnemosyneItem,
        MnemosyneSourceRef,
        freeze_reason_detail,
    )

    detail = freeze_reason_detail({
        "matched_tags": ["migration", "deploy"],
        "score": 0.91,
    })
    ref = MnemosyneSourceRef(
        layer="L2",
        item_id="fact:7",
        source="holographic",
        reason_code="l2_query_tag_match",
        reason_detail=detail,
    )
    item = MnemosyneItem(
        item_id="L2:fact:7",
        layer="L2",
        content="Deployment failed during migration.",
        reason="matched tags: migration, deploy",
        reason_code="l2_query_tag_match",
        reason_detail=detail,
        score=1.4,
        source="holographic",
        provenance=(ref,),
        trust=0.8,
        created_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )

    assert item.score == 1.0
    assert item.reason_detail == (
        ("matched_tags", ("migration", "deploy")),
        ("score", 0.91),
    )
    assert item.provenance[0].reason_detail == item.reason_detail
    with pytest.raises(FrozenInstanceError):
        item.content = "changed"
    with pytest.raises(TypeError):
        item.reason_detail[0][1][0] = "changed"


def test_reason_text_is_sanitized():
    from plugins.memory.mnemosyne.contracts import sanitize_reason_text

    text = (
        "SELECT * FROM facts WHERE token='secret' "
        "/Users/himeka/.hermes/.env OPENAI_API_KEY=sk-test"
    )

    sanitized = sanitize_reason_text(text)

    assert "SELECT *" not in sanitized
    assert "/Users/himeka" not in sanitized
    assert "sk-test" not in sanitized
    assert "OPENAI_API_KEY" in sanitized


def test_provider_discovery_loads_mnemosyne():
    from plugins.memory import load_memory_provider

    provider = load_memory_provider("mnemosyne")

    assert provider is not None
    assert provider.name == "mnemosyne"
    assert provider.is_available() is True
    assert provider.get_tool_schemas() == []
    assert "Memory Tower" in provider.system_prompt_block()
```

Add this assertion to the existing bundled-provider discovery test in `tests/agent/test_memory_provider.py` if the test already lists provider names:

```python
assert "mnemosyne" in names
```

- [ ] **Step 2: Run the tests and confirm they fail for missing provider code.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_provider_discovery_loads_mnemosyne \
  tests/plugins/memory/test_mnemosyne.py::test_mnemosyne_items_deep_freeze_provenance_and_reason_detail \
  -q
```

Expected: FAIL because `plugins.memory.mnemosyne` does not exist or lacks contracts.

- [ ] **Step 3: Implement the minimal contract module.**

Create `plugins/memory/mnemosyne/contracts.py` with these definitions:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import Any, Mapping, Optional

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
_SECRET_VALUE_RE = re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=([^\s]+)")


JsonPrimitive = str | int | float | bool | None


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
    clean = _SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[redacted]", clean)
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
        object.__setattr__(self, "reason_detail", freeze_reason_detail(dict(self.reason_detail)))


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
        object.__setattr__(self, "reason_detail", freeze_reason_detail(dict(self.reason_detail)))
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
        total = min(int(data.get("total_char_budget", DEFAULT_TOTAL_CHAR_BUDGET)), MAX_TOTAL_CHAR_BUDGET)
        initial = dict(DEFAULT_INITIAL_LAYER_BUDGETS)
        initial.update({k: int(v) for k, v in dict(data.get("initial_layer_budgets", {})).items() if k in LAYERS})
        hard = dict(DEFAULT_HARD_LAYER_CAPS)
        hard.update({k: int(v) for k, v in dict(data.get("hard_layer_caps", {})).items() if k in LAYERS})
        multiplier = 1.0
        if execution_kind == "cron":
            multiplier = float(data.get("cron_multiplier", DEFAULT_CRON_MULTIPLIER))
        elif execution_kind == "subagent":
            multiplier = float(data.get("subagent_multiplier", DEFAULT_SUBAGENT_MULTIPLIER))
        if multiplier != 1.0:
            total = floor(total * multiplier)
            initial = {layer: floor(value * multiplier) for layer, value in initial.items()}
            hard = {layer: floor(value * multiplier) for layer, value in hard.items()}
        hard = {layer: max(hard[layer], initial[layer]) for layer in LAYERS}
        return cls(
            total_char_budget=max(0, total),
            max_total_char_budget=MAX_TOTAL_CHAR_BUDGET,
            max_items_per_layer=int(data.get("max_items_per_layer", DEFAULT_MAX_ITEMS_PER_LAYER)),
            max_item_chars=int(data.get("max_item_chars", DEFAULT_MAX_ITEM_CHARS)),
            initial_layer_budgets=initial,
            hard_layer_caps=hard,
            max_l4_file_chars=int(data.get("max_l4_file_chars", 1048576)),
            max_l4_record_chars=int(data.get("max_l4_record_chars", 4096)),
        )
```

- [ ] **Step 4: Implement the provider shell and metadata.**

Create `plugins/memory/mnemosyne/__init__.py`:

```python
from __future__ import annotations

from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider


class MnemosyneProvider(MemoryProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._session_id = ""
        self._hermes_home = ""

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._hermes_home = str(kwargs.get("hermes_home", ""))

    def system_prompt_block(self) -> str:
        return (
            "# Mnemosyne Memory Tower\n"
            "Active local memory orchestration. L4 reflections are candidates, "
            "not approved facts."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", messages=None) -> None:
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        raise NotImplementedError(f"Mnemosyne exposes no tools in v1: {tool_name}")
```

Create `plugins/memory/mnemosyne/plugin.yaml`:

```yaml
name: mnemosyne
description: Local Mnemosyne Memory Tower orchestration provider
```

- [ ] **Step 5: Run the focused tests and provider discovery regression.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py \
  tests/agent/test_memory_provider.py::TestMemoryProviderDiscovery::test_discover_finds_providers \
  -q
```

Expected: PASS for contract/discovery tests.

- [ ] **Step 6: Commit the provider shell.**

```bash
git add plugins/memory/mnemosyne tests/plugins/memory/test_mnemosyne.py tests/agent/test_memory_provider.py
git commit -m "feat: add mnemosyne provider contracts"
```

## Task 2: Add secure L4 candidate store with idempotent atomic JSONL rewrite

**Files:**
- Create: `plugins/memory/mnemosyne/l4_store.py`
- Modify: `plugins/memory/mnemosyne/contracts.py`
- Modify: `tests/plugins/memory/test_mnemosyne.py`

**Interfaces:**
- Consumes: `MnemosyneConfig`, active `hermes_home`, profile/session/agent metadata.
- Produces:
  - `L4ReadState(age_days: int, decay_score: float, archive_recommended: bool)`
  - `L4CandidateRecord`
  - `L4Store(hermes_home: Path, profile_id: str, config: MnemosyneConfig)`
  - `L4Store.write_candidate(record: L4CandidateRecord) -> dict[str, object]`
  - `L4Store.read_records(now: datetime | None = None) -> tuple[list[dict[str, object]], list[dict[str, object]]]`
  - `candidate_record_id(record: Mapping[str, object]) -> str`
  - `assert_trusted_path(path: Path, root: Path) -> Path`

- [ ] **Step 1: Write failing L4 persistence tests.**

Append tests:

```python
from datetime import timedelta
import json


def _l4_record(**overrides):
    base = {
        "kind": "lesson",
        "content": "Prefer rollback-first recovery after failed migrations.",
        "profile_id": "coder",
        "session_id": "s1",
        "parent_session_id": None,
        "agent_id": "agent-main",
        "execution_kind": "interactive",
        "confidence": 0.8,
        "governance_state": "candidate",
        "source_refs": [{"layer": "L2", "item_id": "fact:7"}],
        "created_at": "2026-07-28T00:00:00Z",
        "last_validated_at": None,
        "archived_at": None,
    }
    base.update(overrides)
    return base


def test_l4_write_preserves_malformed_lines_and_is_idempotent(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    l4_path = tmp_path / "memories" / "mnemosyne" / "l4.jsonl"
    l4_path.parent.mkdir(parents=True)
    l4_path.write_bytes(b"{bad json}\n")
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record())

    first = store.write_candidate(record)
    second = store.write_candidate(record)

    assert first["written"] is True
    assert second["written"] is False
    raw = l4_path.read_bytes().splitlines()
    assert raw[0] == b"{bad json}"
    assert len(raw) == 2
    payload = json.loads(raw[1])
    assert payload["record_id"].startswith("l4_")
    assert "decay_score" not in payload
    assert payload["parent_session_id"] is None


def test_l4_parent_session_id_participates_in_candidate_key(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    parent_a = L4CandidateRecord.from_mapping(_l4_record(parent_session_id="parent-a"))
    parent_b = L4CandidateRecord.from_mapping(_l4_record(parent_session_id="parent-b"))

    assert parent_a.record_id != parent_b.record_id
    assert store.write_candidate(parent_a)["written"] is True
    assert store.write_candidate(parent_b)["written"] is True
    records, diagnostics = store.read_records()
    assert len(records) == 2
    assert diagnostics == []


def test_l4_decay_is_read_time_only_and_legacy_decay_score_is_ignored(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4Store

    l4_path = tmp_path / "memories" / "mnemosyne" / "l4.jsonl"
    l4_path.parent.mkdir(parents=True)
    l4_path.write_text(
        json.dumps({
            **_l4_record(),
            "record_id": "l4_legacy",
            "decay_score": 1.0,
            "created_at": "2026-01-01T00:00:00Z",
        }) + "\n",
        encoding="utf-8",
    )
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))

    records, diagnostics = store.read_records(now=datetime(2026, 7, 28, tzinfo=timezone.utc))

    assert diagnostics == []
    assert records[0]["record_id"] == "l4_legacy"
    assert records[0]["read_state"]["archive_recommended"] is True
    assert records[0]["read_state"]["decay_score"] == 0.0
    assert "decay_score" in l4_path.read_text(encoding="utf-8")


def test_l4_security_invariant_disables_store_on_symlink_escape(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4Store, SecurityInvariantError

    outside = tmp_path / "outside"
    outside.mkdir()
    target_dir = tmp_path / "memories"
    target_dir.mkdir()
    (target_dir / "mnemosyne").symlink_to(outside)

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))

    with pytest.raises(SecurityInvariantError):
        store.ensure_available()
```

- [ ] **Step 2: Run tests and confirm missing L4 APIs fail.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_l4_write_preserves_malformed_lines_and_is_idempotent \
  tests/plugins/memory/test_mnemosyne.py::test_l4_decay_is_read_time_only_and_legacy_decay_score_is_ignored \
  -q
```

Expected: FAIL because `plugins.memory.mnemosyne.l4_store` does not exist.

- [ ] **Step 3: Implement record, read state, path security, and idempotency.**

Create `plugins/memory/mnemosyne/l4_store.py` with:

```python
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Optional

from .contracts import MnemosyneConfig

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
L4_RELATIVE_PATH = Path("memories") / "mnemosyne" / "l4.jsonl"


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


def candidate_record_id(record: Mapping[str, object]) -> str:
    key = "\0".join([
        str(record.get("profile_id") or ""),
        str(record.get("session_id") or ""),
        str(record.get("parent_session_id") or ""),
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

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "L4CandidateRecord":
        payload = dict(value)
        payload.setdefault("parent_session_id", None)
        payload.setdefault("governance_state", "candidate")
        payload.setdefault("created_at", _now_iso())
        payload.setdefault("last_validated_at", None)
        payload.setdefault("archived_at", None)
        payload.pop("decay_score", None)
        payload.pop("age_days", None)
        payload.pop("archive_recommended", None)
        record_id = candidate_record_id(payload)
        payload["record_id"] = record_id
        if "\n" in json.dumps(payload, sort_keys=True, ensure_ascii=False):
            raise ValueError("L4 record must serialize to one JSONL line")
        return cls(payload=payload, record_id=record_id)


def assert_trusted_path(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise SecurityInvariantError(f"path escapes trusted root: {path}") from exc
    return resolved_path


class L4Store:
    def __init__(self, hermes_home: str | Path, *, profile_id: str, config: MnemosyneConfig) -> None:
        self.hermes_home = Path(hermes_home)
        self.profile_id = profile_id
        self.config = config
        self.path = self.hermes_home / L4_RELATIVE_PATH
        self._disabled = False

    def ensure_available(self) -> None:
        assert_trusted_path(self.path.parent, self.hermes_home)

    def _lock(self) -> threading.RLock:
        key = str(self.path)
        with _LOCKS_GUARD:
            lock = _LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _LOCKS[key] = lock
            return lock

    def _read_raw_lines(self) -> list[bytes]:
        if not self.path.exists():
            return []
        return self.path.read_bytes().splitlines(keepends=True)

    def write_candidate(self, record: L4CandidateRecord) -> dict[str, object]:
        if self._disabled:
            return {"written": False, "reason": "provider_disabled"}
        try:
            self.ensure_available()
        except SecurityInvariantError:
            self._disabled = True
            raise
        line_text = json.dumps(record.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if "\n" in line_text or len(line_text) > self.config.max_l4_record_chars:
            return {"written": False, "reason": "record_too_large"}
        line = (line_text + "\n").encode("utf-8")
        with self._lock():
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
            final = b"".join(raw_lines) + line
            if len(final.decode("utf-8", errors="ignore")) > self.config.max_l4_file_chars:
                return {"written": False, "reason": "file_too_large"}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".l4-", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(final)
                    handle.flush()
                    os.fsync(handle.fileno())
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
```

- [ ] **Step 4: Implement `read_records()` and dynamic decay.**

Append:

```python
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
```

Inside `L4Store`:

```python
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
        for index, raw in enumerate(self._read_raw_lines()):
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
            item.pop("decay_score", None)
            item["read_state"] = {
                "age_days": state.age_days,
                "decay_score": state.decay_score,
                "archive_recommended": state.archive_recommended,
            }
            records.append(item)
        return records, diagnostics
```

- [ ] **Step 5: Run the L4 tests.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
```

Expected: PASS for Task 1 and Task 2 tests.

- [ ] **Step 6: Commit L4 storage.**

```bash
git add plugins/memory/mnemosyne/l4_store.py plugins/memory/mnemosyne/contracts.py tests/plugins/memory/test_mnemosyne.py
git commit -m "feat: add mnemosyne l4 candidate store"
```

## Task 3: Add deterministic budgeting, sorting, and context rendering

**Files:**
- Create: `plugins/memory/mnemosyne/injector.py`
- Modify: `tests/plugins/memory/test_mnemosyne.py`

**Interfaces:**
- Consumes: `MnemosyneItem`, `MnemosyneConfig`, authority ranking.
- Produces:
  - `sort_items(items: list[MnemosyneItem]) -> list[MnemosyneItem]`
  - `merge_duplicate_items(items: list[MnemosyneItem]) -> list[MnemosyneItem]`
  - `render_context(items: list[MnemosyneItem], config: MnemosyneConfig) -> str`
  - `scale_config_for_execution(config: MnemosyneConfig, execution_kind: str) -> MnemosyneConfig` from Task 1 config behavior.

- [ ] **Step 1: Write failing budget and sort tests.**

Add:

```python
def _item(item_id, layer, content, score=0.5, trust=None):
    from plugins.memory.mnemosyne.contracts import MnemosyneItem, MnemosyneSourceRef

    ref = MnemosyneSourceRef(
        layer=layer,
        item_id=item_id,
        source=f"source-{layer}",
        reason_code=f"{layer.lower()}_test",
        reason_detail=(("item_id", item_id),),
    )
    return MnemosyneItem(
        item_id=item_id,
        layer=layer,
        content=content,
        reason=f"reason for {item_id}",
        reason_code=f"{layer.lower()}_test",
        reason_detail=(("item_id", item_id),),
        score=score,
        source=f"source-{layer}",
        provenance=(ref,),
        trust=trust,
    )


def test_sort_items_uses_authority_then_score_then_item_id():
    from plugins.memory.mnemosyne.injector import sort_items

    items = [
        _item("z-l2", "L2", "l2 high", score=1.0, trust=1.0),
        _item("a-l1", "L1", "l1 low", score=0.1, trust=0.1),
        _item("b-l1", "L1", "l1 low second", score=0.1, trust=0.1),
    ]

    assert [item.item_id for item in sort_items(items)] == ["a-l1", "b-l1", "z-l2"]


def test_budget_borrowing_allows_l2_to_use_empty_l3_without_exceeding_caps():
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.injector import render_context

    config = MnemosyneConfig.from_mapping({
        "total_char_budget": 200,
        "max_item_chars": 80,
        "max_items_per_layer": 8,
        "initial_layer_budgets": {"L1": 30, "L2": 50, "L3": 50, "L4": 50},
        "hard_layer_caps": {"L1": 60, "L2": 120, "L3": 60, "L4": 60},
    })
    items = [
        _item("l2-a", "L2", "a" * 60),
        _item("l2-b", "L2", "b" * 40),
        _item("l1-a", "L1", "core"),
    ]

    block = render_context(items, config)

    assert "l2-a" in block
    assert "l2-b" in block
    assert len(block) <= 200


def test_subagent_multiplier_scales_total_initial_and_hard_caps():
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig

    config = MnemosyneConfig.from_mapping({}, execution_kind="subagent")

    assert config.total_char_budget == 3000
    assert config.initial_layer_budgets["L2"] == 1600
    assert config.hard_layer_caps["L2"] == 3000
    assert config.initial_layer_budgets["L1"] <= config.hard_layer_caps["L1"]
```

- [ ] **Step 2: Run the new tests and confirm missing injector fails.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_sort_items_uses_authority_then_score_then_item_id \
  tests/plugins/memory/test_mnemosyne.py::test_budget_borrowing_allows_l2_to_use_empty_l3_without_exceeding_caps \
  -q
```

Expected: FAIL because `plugins.memory.mnemosyne.injector` does not exist.

- [ ] **Step 3: Implement sorting, duplicate merge, and rendering.**

Create `plugins/memory/mnemosyne/injector.py`:

```python
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .contracts import AUTHORITY_RANK, LAYERS, MnemosyneConfig, MnemosyneItem


def freshness_score(created_at) -> float:
    if created_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    age_days = max(0, int((now - created_at.astimezone(timezone.utc)).total_seconds() // 86400))
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
        if provenance == primary.provenance:
            merged.append(primary)
        else:
            merged.append(MnemosyneItem(
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
            ))
    return sort_items(merged)


def _item_line(item: MnemosyneItem, max_item_chars: int) -> str:
    content = item.content[:max_item_chars].strip()
    reason = item.reason.strip()
    return f"- [{item.layer} id={item.item_id} score={item.score:.2f}] {content}\n  reason: {reason}\n"


def render_context(items: list[MnemosyneItem], config: MnemosyneConfig) -> str:
    sorted_items = sort_items(merge_duplicate_items(items))
    used_by_layer = {layer: 0 for layer in LAYERS}
    emitted_by_layer = {layer: 0 for layer in LAYERS}
    lines = ["## Mnemosyne Memory Tower\n"]

    def remaining_total() -> int:
        return config.total_char_budget - sum(len(line) for line in lines)

    deferred: list[tuple[MnemosyneItem, str]] = []
    for item in sorted_items:
        if emitted_by_layer[item.layer] >= config.max_items_per_layer:
            continue
        line = _item_line(item, config.max_item_chars)
        cost = len(line)
        if cost <= config.initial_layer_budgets[item.layer] - used_by_layer[item.layer] and cost <= remaining_total():
            lines.append(line)
            used_by_layer[item.layer] += cost
            emitted_by_layer[item.layer] += 1
        else:
            deferred.append((item, line))

    shared_pool = sum(
        max(0, config.initial_layer_budgets[layer] - used_by_layer[layer])
        for layer in LAYERS
    )
    for layer in ("L2", "L1", "L4", "L3"):
        for item, line in list(deferred):
            if item.layer != layer:
                continue
            if emitted_by_layer[layer] >= config.max_items_per_layer:
                continue
            cost = len(line)
            can_borrow = min(shared_pool, config.hard_layer_caps[layer] - used_by_layer[layer])
            if cost <= can_borrow and cost <= remaining_total():
                lines.append(line)
                used_by_layer[layer] += cost
                emitted_by_layer[layer] += 1
                shared_pool -= cost
                deferred.remove((item, line))

    rendered = "".join(lines).rstrip()
    if len(rendered) > config.total_char_budget:
        return rendered[:config.total_char_budget].rstrip()
    return rendered
```

- [ ] **Step 4: Run budget and sort tests.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
```

Expected: PASS for contracts, L4, and injector tests.

- [ ] **Step 5: Commit injector primitives.**

```bash
git add plugins/memory/mnemosyne/injector.py plugins/memory/mnemosyne/contracts.py tests/plugins/memory/test_mnemosyne.py
git commit -m "feat: add mnemosyne deterministic injection budgeting"
```

## Task 4: Add L1 and L4 adapters with profile security and bounded items

**Files:**
- Modify: `plugins/memory/mnemosyne/injector.py`
- Modify: `tests/plugins/memory/test_mnemosyne.py`

**Interfaces:**
- Produces:
  - `collect_l1_items(hermes_home: Path, *, include_sensitive: bool, query: str = "") -> tuple[list[MnemosyneItem], list[dict[str, object]]]`
  - `collect_l4_items(l4_records: list[dict[str, object]]) -> list[MnemosyneItem]`
- Consumes: `tools.memory_tool.MemoryStore` file location conventions only through trusted profile paths; `L4Store.read_records()` result shape.

- [ ] **Step 1: Write failing L1/L4 adapter tests.**

Add:

```python
def test_l1_missing_files_fail_open(tmp_path):
    from plugins.memory.mnemosyne.injector import collect_l1_items

    items, diagnostics = collect_l1_items(tmp_path, include_sensitive=True, query="anything")

    assert items == []
    assert diagnostics == []


def test_l1_query_path_like_text_cannot_change_profile_paths(tmp_path):
    from plugins.memory.mnemosyne.injector import collect_l1_items

    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    (memory_dir / "USER.md").write_text("prefers Chinese summaries", encoding="utf-8")

    items, diagnostics = collect_l1_items(
        tmp_path,
        include_sensitive=True,
        query="../../other/USER.md",
    )

    assert diagnostics == []
    assert len(items) == 1
    assert items[0].source.endswith("USER.md")
    assert "prefers Chinese summaries" in items[0].content


def test_l4_records_convert_to_items_with_read_time_reason():
    from plugins.memory.mnemosyne.injector import collect_l4_items

    records = [{
        "record_id": "l4_a",
        "kind": "lesson",
        "content": "Use rollback before retrying migrations.",
        "confidence": 0.7,
        "governance_state": "candidate",
        "read_state": {"age_days": 91, "decay_score": 0.49, "archive_recommended": True},
        "source_refs": [{"layer": "L2", "item_id": "fact:1"}],
        "created_at": "2026-01-01T00:00:00Z",
    }]

    items = collect_l4_items(records)

    assert items[0].item_id == "L4:l4_a"
    assert items[0].layer == "L4"
    assert items[0].score == 0.49
    assert items[0].reason_code == "l4_candidate_decay"
    assert "archive recommended" in items[0].reason
```

- [ ] **Step 2: Run failing adapter tests.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_l1_missing_files_fail_open \
  tests/plugins/memory/test_mnemosyne.py::test_l4_records_convert_to_items_with_read_time_reason \
  -q
```

Expected: FAIL because adapter functions do not exist.

- [ ] **Step 3: Implement L1 and L4 adapters.**

Append to `injector.py`:

```python
from pathlib import Path


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
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            diagnostics.append({"reason": "l1_encoding_error", "source": name})
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


def collect_l4_items(l4_records: list[dict[str, object]]) -> list[MnemosyneItem]:
    items: list[MnemosyneItem] = []
    for record in l4_records:
        read_state = record.get("read_state")
        if not isinstance(read_state, dict):
            continue
        score = float(read_state.get("decay_score", 0.0)) * float(record.get("confidence", 0.5))
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
            provenance=(),
            trust=float(record.get("confidence", 0.5)),
        ))
    return items
```

- [ ] **Step 4: Run adapter tests.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
```

Expected: PASS for Task 1-4 tests.

- [ ] **Step 5: Commit adapters.**

```bash
git add plugins/memory/mnemosyne/injector.py tests/plugins/memory/test_mnemosyne.py
git commit -m "feat: add mnemosyne l1 and l4 adapters"
```

## Task 5: Add L2 and L3 read-only adapters plus deterministic intent routing

**Files:**
- Modify: `plugins/memory/mnemosyne/injector.py`
- Modify: `tests/plugins/memory/test_mnemosyne.py`

**Interfaces:**
- Produces:
  - `is_skill_intent(query: str, known_skill_names: set[str] | None = None) -> bool`
  - `collect_l2_items(retriever: object, query: str, *, limit: int = 8) -> tuple[list[MnemosyneItem], list[dict[str, object]]]`
  - `collect_l3_items(skillwiki: object, query: str, *, known_skill_names: set[str] | None = None) -> tuple[list[MnemosyneItem], list[dict[str, object]]]`
- Consumes: `FactRetriever.search(..., mark_retrieved=False)`, `FactRetriever.reconstruct(..., mark_retrieved=False)`, `SkillWiki.list_skills()`, `SkillWiki.check()`.

- [ ] **Step 1: Write failing L2/L3 tests with fakes.**

Add:

```python
class _FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query, *, min_trust=0.3, limit=8, mark_retrieved=True, **kwargs):
        self.calls.append(("search", mark_retrieved))
        return [{
            "fact_id": 7,
            "content": "Deployment failed during migration.",
            "trust_score": 0.8,
            "tags": "deployment,migration",
            "reason": {
                "summary": "matched migration tag",
                "strategy": "fts+jaccard+hrr+trust",
                "matched_terms": ["migration"],
            },
        }]


def test_l2_adapter_uses_mark_retrieved_false_and_reason():
    from plugins.memory.mnemosyne.injector import collect_l2_items

    retriever = _FakeRetriever()
    items, diagnostics = collect_l2_items(retriever, "migration", limit=3)

    assert diagnostics == []
    assert retriever.calls == [("search", False)]
    assert items[0].item_id == "L2:fact:7"
    assert items[0].reason_code == "l2_retrieval_reason"
    assert "migration" in items[0].reason


class _UnavailableWiki:
    def list_skills(self):
        return type("Result", (), {"available": False, "reason": "database_unavailable", "items": []})()


def test_l3_unavailable_skillwiki_fails_open():
    from plugins.memory.mnemosyne.injector import collect_l3_items

    items, diagnostics = collect_l3_items(_UnavailableWiki(), "skill deploy")

    assert items == []
    assert diagnostics == [{"reason": "database_unavailable", "layer": "L3"}]


class _FakeWiki:
    def list_skills(self):
        return type("Result", (), {
            "available": True,
            "items": [{
                "skill_id": "github:acme/deploy:",
                "name": "deploy",
                "status": "verified",
                "source_url": "https://github.com/acme/deploy/tree/abc",
                "content_hash": "sha256:abc",
                "metadata_json": "{}",
            }],
        })()


def test_l3_intent_routing_is_deterministic_and_read_only():
    from plugins.memory.mnemosyne.injector import collect_l3_items, is_skill_intent

    assert is_skill_intent("which skill handles deploy?", {"deploy"}) is True
    assert is_skill_intent("remember my lunch", {"deploy"}) is False

    items, diagnostics = collect_l3_items(_FakeWiki(), "which skill handles deploy?", known_skill_names={"deploy"})

    assert diagnostics == []
    assert items[0].layer == "L3"
    assert items[0].reason_code == "l3_skill_intent_match"
    assert items[0].trust == 1.0
```

- [ ] **Step 2: Run failing L2/L3 tests.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_l2_adapter_uses_mark_retrieved_false_and_reason \
  tests/plugins/memory/test_mnemosyne.py::test_l3_intent_routing_is_deterministic_and_read_only \
  -q
```

Expected: FAIL because L2/L3 adapter functions do not exist.

- [ ] **Step 3: Implement L2 and L3 adapters.**

Append:

```python
_SKILL_INTENT_KEYWORDS = {
    "skill", "skills", "tool", "tools", "api", "workflow", "install",
    "provenance", "lifecycle", "能力", "技能", "工具", "安装", "来源",
}


def is_skill_intent(query: str, known_skill_names: set[str] | None = None) -> bool:
    tokens = {token.strip(".,:;!?()[]{}").casefold() for token in (query or "").split()}
    if tokens & _SKILL_INTENT_KEYWORDS:
        return True
    return bool(known_skill_names and tokens & {name.casefold() for name in known_skill_names})


def collect_l2_items(retriever: object, query: str, *, limit: int = 8) -> tuple[list[MnemosyneItem], list[dict[str, object]]]:
    if not query:
        return [], []
    try:
        rows = retriever.search(query, min_trust=0.3, limit=limit, mark_retrieved=False)
    except Exception as exc:
        return [], [{"reason": "l2_unavailable", "message": str(exc)}]
    items: list[MnemosyneItem] = []
    for row in rows or []:
        fact_id = row.get("fact_id")
        reason_obj = row.get("reason") if isinstance(row, dict) else None
        reason = ""
        if isinstance(reason_obj, dict):
            reason = str(reason_obj.get("summary") or "")
            detail = {
                "strategy": reason_obj.get("strategy"),
                "matched_terms": reason_obj.get("matched_terms", []),
            }
        else:
            detail = {}
        items.append(MnemosyneItem(
            item_id=f"L2:fact:{fact_id}",
            layer="L2",
            content=str(row.get("content") or ""),
            reason=reason or "holographic recall",
            reason_code="l2_retrieval_reason",
            reason_detail=tuple((str(k), v) for k, v in detail.items()),
            score=float(row.get("score", row.get("trust_score", 0.5))),
            source="holographic",
            provenance=(),
            trust=float(row.get("trust_score", 0.5)),
        ))
    return items, []


def collect_l3_items(
    skillwiki: object,
    query: str,
    *,
    known_skill_names: set[str] | None = None,
) -> tuple[list[MnemosyneItem], list[dict[str, object]]]:
    try:
        result = skillwiki.list_skills()
    except Exception as exc:
        return [], [{"reason": "l3_unavailable", "message": str(exc), "layer": "L3"}]
    if not getattr(result, "available", False):
        return [], [{"reason": getattr(result, "reason", "database_unavailable"), "layer": "L3"}]
    rows = [dict(row) for row in getattr(result, "items", [])]
    names = {str(row.get("name") or "") for row in rows}
    if known_skill_names:
        names.update(known_skill_names)
    if not is_skill_intent(query, names):
        return [], []
    items: list[MnemosyneItem] = []
    for row in rows:
        if row.get("status") in {"archived", "deprecated", "degraded"}:
            continue
        skill_id = str(row.get("skill_id") or "")
        name = str(row.get("name") or skill_id)
        status = str(row.get("status") or "raw")
        confidence = 1.0 if row.get("content_hash") and row.get("source_url") else 0.5
        items.append(MnemosyneItem(
            item_id=f"L3:{skill_id}",
            layer="L3",
            content=f"skill {name} is {status}",
            reason=f"deterministic skill intent matched {name}; SkillWiki status {status}",
            reason_code="l3_skill_intent_match",
            reason_detail=(("skill", name), ("status", status)),
            score=confidence,
            source="skillwiki",
            provenance=(),
            trust=confidence,
        ))
    return items, []
```

- [ ] **Step 4: Run adapter tests.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
```

Expected: PASS for Task 1-5 focused tests.

- [ ] **Step 5: Commit L2/L3 adapters.**

```bash
git add plugins/memory/mnemosyne/injector.py tests/plugins/memory/test_mnemosyne.py
git commit -m "feat: add mnemosyne l2 and l3 adapters"
```

## Task 6: Wire provider lifecycle, buffers, budgets, and fail-open behavior

**Files:**
- Modify: `plugins/memory/mnemosyne/__init__.py`
- Modify: `plugins/memory/mnemosyne/injector.py`
- Modify: `tests/plugins/memory/test_mnemosyne.py`

**Interfaces:**
- Consumes: contracts, L4 store, adapters, existing `MemoryProvider`.
- Produces:
  - `MnemosyneProvider.initialize()` stores profile/session/agent/execution metadata.
  - `MnemosyneProvider.prefetch()` returns bounded context or `""`.
  - `MnemosyneProvider.sync_turn()` writes only to instance-local buffer.
  - `MnemosyneProvider.on_session_end()` writes idempotent L4 candidates.
  - `MnemosyneProvider.on_memory_write()` observes successful writes idempotently.

- [ ] **Step 1: Write failing lifecycle tests.**

Add:

```python
def test_provider_sync_turn_buffers_without_touching_l4_file(tmp_path):
    from plugins.memory.mnemosyne import MnemosyneProvider

    provider = MnemosyneProvider({"l4_candidate_observation": True})
    provider.initialize(
        "s1",
        hermes_home=str(tmp_path),
        agent_identity="coder",
        agent_context="primary",
        agent_id="agent-main",
        execution_kind="interactive",
    )

    provider.sync_turn("User prefers rollback first.", "I will remember candidate.")

    assert not (tmp_path / "memories" / "mnemosyne" / "l4.jsonl").exists()
    assert len(provider._candidate_buffer) == 1


def test_provider_session_end_writes_l4_once_with_parent_session_id(tmp_path):
    from plugins.memory.mnemosyne import MnemosyneProvider

    provider = MnemosyneProvider({"l4_candidate_observation": True})
    provider.initialize(
        "child",
        hermes_home=str(tmp_path),
        agent_identity="coder",
        agent_context="subagent",
        agent_id="agent-child",
        execution_kind="subagent",
        parent_session_id="parent",
    )
    provider.sync_turn("Migration failed.", "Use rollback first.")

    messages = [{"role": "user", "content": "Migration failed."}]
    provider.on_session_end(messages)
    provider.on_session_end(messages)

    raw = (tmp_path / "memories" / "mnemosyne" / "l4.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 1
    payload = json.loads(raw[0])
    assert payload["parent_session_id"] == "parent"
    assert payload["execution_kind"] == "subagent"


def test_provider_prefetch_returns_empty_on_security_invariant_failure(tmp_path):
    from plugins.memory.mnemosyne import MnemosyneProvider

    outside = tmp_path / "outside"
    outside.mkdir()
    memories = tmp_path / "memories"
    memories.mkdir()
    (memories / "mnemosyne").symlink_to(outside)
    provider = MnemosyneProvider()
    provider.initialize("s1", hermes_home=str(tmp_path), agent_identity="coder")

    assert provider.prefetch("anything") == ""
    assert provider._disabled is True
```

- [ ] **Step 2: Run failing lifecycle tests.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_provider_sync_turn_buffers_without_touching_l4_file \
  tests/plugins/memory/test_mnemosyne.py::test_provider_session_end_writes_l4_once_with_parent_session_id \
  -q
```

Expected: FAIL because provider lifecycle is still a shell.

- [ ] **Step 3: Implement provider metadata and local buffer.**

Update `MnemosyneProvider.__init__()` and `initialize()`:

```python
from pathlib import Path

from .contracts import MnemosyneConfig
from .l4_store import L4CandidateRecord, L4Store, SecurityInvariantError
from .injector import collect_l1_items, collect_l4_items, render_context


    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._raw_config = dict(config or {})
        self._config = MnemosyneConfig.from_mapping(self._raw_config)
        self._session_id = ""
        self._parent_session_id = None
        self._profile_id = "default"
        self._agent_id = "agent"
        self._execution_kind = "interactive"
        self._hermes_home = Path(".")
        self._candidate_buffer: list[dict[str, object]] = []
        self._l4_store: L4Store | None = None
        self._disabled = False

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._parent_session_id = kwargs.get("parent_session_id")
        self._profile_id = str(kwargs.get("agent_identity") or "default")
        self._agent_id = str(kwargs.get("agent_id") or kwargs.get("agent_identity") or "agent")
        self._execution_kind = str(kwargs.get("execution_kind") or kwargs.get("agent_context") or "interactive")
        if self._execution_kind == "primary":
            self._execution_kind = "interactive"
        self._hermes_home = Path(str(kwargs.get("hermes_home") or "."))
        self._config = MnemosyneConfig.from_mapping(self._raw_config, execution_kind=self._execution_kind)
        self._l4_store = L4Store(self._hermes_home, profile_id=self._profile_id, config=self._config)
```

- [ ] **Step 4: Implement `sync_turn()` buffer and `on_session_end()` L4 candidate write.**

```python
    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", messages=None) -> None:
        if self._disabled or not self._raw_config.get("l4_candidate_observation"):
            return
        if self._execution_kind not in {"interactive", "primary", "subagent", "cron"}:
            return
        content = " ".join((user_content or "").split())
        if not content:
            return
        self._candidate_buffer.append({
            "kind": "reflection",
            "content": content[:1000],
            "profile_id": self._profile_id,
            "session_id": session_id or self._session_id,
            "parent_session_id": self._parent_session_id,
            "agent_id": self._agent_id,
            "execution_kind": self._execution_kind,
            "confidence": 0.5,
            "governance_state": "candidate",
            "source_refs": [],
        })

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._disabled or not self._l4_store:
            return
        for raw in list(self._candidate_buffer):
            try:
                self._l4_store.write_candidate(L4CandidateRecord.from_mapping(raw))
            except SecurityInvariantError:
                self._disabled = True
                return
            except Exception:
                continue
        self._candidate_buffer.clear()
```

- [ ] **Step 5: Implement bounded fail-open `prefetch()`.**

```python
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._disabled:
            return ""
        try:
            include_sensitive = self._execution_kind == "interactive"
            l1_items, _ = collect_l1_items(
                self._hermes_home,
                include_sensitive=include_sensitive,
                query=query,
            )
            l4_items = []
            if self._l4_store is not None:
                records, _ = self._l4_store.read_records()
                l4_items = collect_l4_items(records)
            return render_context(l1_items + l4_items, self._config)
        except SecurityInvariantError:
            self._disabled = True
            return ""
        except Exception:
            return ""
```

Task 7 will extend prefetch with real L2/L3 adapters; this step proves lifecycle and fail-open behavior first.

- [ ] **Step 6: Run lifecycle tests.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
```

Expected: PASS for all Mnemosyne focused tests so far.

- [ ] **Step 7: Commit provider lifecycle.**

```bash
git add plugins/memory/mnemosyne/__init__.py tests/plugins/memory/test_mnemosyne.py
git commit -m "feat: wire mnemosyne provider lifecycle"
```

## Task 7: Integrate real L2/L3 sources, bridge protocol, docs, and final verification

**Files:**
- Create: `plugins/memory/mnemosyne/bridge.py`
- Modify: `plugins/memory/mnemosyne/__init__.py`
- Modify: `plugins/memory/mnemosyne/injector.py`
- Create: `docs/mnemosyne.md`
- Modify: `tests/plugins/memory/test_mnemosyne.py`

**Interfaces:**
- Consumes: `HolographicMemoryProvider` internals when available, `SkillWiki`, L1/L4 adapters.
- Produces:
  - `MnemosyneBridge` protocol only.
  - Provider prefetch includes L1, L2, L3, and L4 when each layer is available.
  - Documentation of opt-in and non-goals.

- [ ] **Step 1: Write failing integration and zero-network tests.**

Add:

```python
def test_bridge_protocol_exists_but_provider_uses_no_network(monkeypatch, tmp_path):
    import socket
    from plugins.memory.mnemosyne import MnemosyneProvider
    from plugins.memory.mnemosyne.bridge import MnemosyneBridge

    calls = []

    def fail_socket(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    provider = MnemosyneProvider()
    provider.initialize("s1", hermes_home=str(tmp_path), agent_identity="coder")

    provider.prefetch("deployment")
    provider.sync_turn("hello", "world")
    provider.on_session_end([])

    assert calls == []
    assert hasattr(MnemosyneBridge, "fetch")
    assert hasattr(MnemosyneBridge, "publish")


def test_provider_prefetch_merges_l1_and_l4_context(tmp_path):
    from plugins.memory.mnemosyne import MnemosyneProvider
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord

    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    (memory_dir / "USER.md").write_text("prefers Chinese summaries", encoding="utf-8")

    provider = MnemosyneProvider({"l4_candidate_observation": True})
    provider.initialize("s1", hermes_home=str(tmp_path), agent_identity="coder")
    provider._l4_store.write_candidate(L4CandidateRecord.from_mapping(_l4_record(profile_id="coder")))

    block = provider.prefetch("rollback migration")

    assert "## Mnemosyne Memory Tower" in block
    assert "prefers Chinese summaries" in block
    assert "rollback" in block
    assert "reason:" in block
```

- [ ] **Step 2: Run failing final tests.**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_bridge_protocol_exists_but_provider_uses_no_network \
  tests/plugins/memory/test_mnemosyne.py::test_provider_prefetch_merges_l1_and_l4_context \
  -q
```

Expected: FAIL because `bridge.py` does not exist and prefetch does not yet include all adapters reliably.

- [ ] **Step 3: Add local-only bridge protocol.**

Create `plugins/memory/mnemosyne/bridge.py`:

```python
from __future__ import annotations

from typing import Protocol

from .contracts import MnemosyneItem


class MnemosyneBridge(Protocol):
    def fetch(self, query: str, *, layer: str) -> list[MnemosyneItem]:
        ...

    def publish(self, items: list[MnemosyneItem]) -> None:
        ...
```

Do not instantiate any bridge inside `MnemosyneProvider`.

- [ ] **Step 4: Extend provider prefetch to include L2/L3 when available.**

Inside `MnemosyneProvider.prefetch()`, after L1 collection:

```python
            items = list(l1_items)
            retriever = getattr(self, "_retriever", None)
            if retriever is not None:
                l2_items, _ = collect_l2_items(retriever, query, limit=self._config.max_items_per_layer)
                items.extend(l2_items)
            skillwiki = getattr(self, "_skillwiki", None)
            if skillwiki is not None:
                l3_items, _ = collect_l3_items(skillwiki, query)
                items.extend(l3_items)
            if self._l4_store is not None:
                records, _ = self._l4_store.read_records()
                items.extend(collect_l4_items(records))
            return render_context(items, self._config)
```

In `initialize()`, best-effort initialize optional L2/L3 dependencies without failing provider startup:

```python
        self._retriever = None
        self._skillwiki = None
        try:
            from plugins.memory.holographic.store import MemoryStore as HoloStore
            from plugins.memory.holographic.retrieval import FactRetriever

            holo_db = self._hermes_home / "memory_store.db"
            self._retriever = FactRetriever(store=HoloStore(db_path=holo_db))
        except Exception:
            self._retriever = None
        try:
            from tools.skills_hub import _hub_dir
            from tools.skillwiki import SkillWiki

            self._skillwiki = SkillWiki(_hub_dir() / "provenance.db")
        except Exception:
            self._skillwiki = None
```

Use `tools.skills_hub._hub_dir()` deliberately here because the current Skills Hub install path records SkillWiki provenance with `SkillWiki(_hub_dir() / "provenance.db")`. Do not create a second SkillWiki path convention.

- [ ] **Step 5: Add user-facing docs.**

Create `docs/mnemosyne.md`:

```markdown
# Mnemosyne Memory Tower

Mnemosyne is an opt-in local memory provider:

```yaml
memory:
  provider: mnemosyne
```

It orchestrates existing MyHermes memory layers:

- L1 Core from `MEMORY.md` and `USER.md`;
- L2 Episodic facts from Holographic Memory;
- L3 Skill/API state from SkillWiki;
- L4 Reflection/Experience candidates in `memories/mnemosyne/l4.jsonl`.

Mnemosyne does not automatically rewrite Core memory, promote memories, install
skills, modify SkillWiki lifecycle, call remote services, or change the Agent
main loop. L4 records are candidates only. Disable Mnemosyne by changing or
removing `memory.provider`.
```

- [ ] **Step 6: Run focused and regression tests.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
./.venv/bin/python -m pytest tests/agent/test_memory_provider.py -q
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_holographic_retrieval.py \
  tests/plugins/memory/test_holographic_store.py \
  tests/tools/test_memory_governance.py \
  tests/tools/test_skillwiki.py \
  -q
./.venv/bin/python -m compileall -q plugins/memory/mnemosyne agent tools
git diff --check
```

Expected:

- `tests/plugins/memory/test_mnemosyne.py` passes.
- `tests/agent/test_memory_provider.py` passes.
- Listed holographic/governance/SkillWiki regressions pass.
- `compileall` exits 0.
- `git diff --check` exits 0.

- [ ] **Step 7: Commit final integration and docs.**

```bash
git add plugins/memory/mnemosyne tests/plugins/memory/test_mnemosyne.py tests/agent/test_memory_provider.py docs/mnemosyne.md
git commit -m "feat: add mnemosyne memory tower provider"
```

## Final verification before review

- [ ] **Step 1: Confirm branch state and unrelated files.**

Run:

```bash
git status --short
```

Expected: no uncommitted Mnemosyne files. If unrelated gateway files remain, do not stage or revert them.

- [ ] **Step 2: Run final focused verification.**

Run:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
./.venv/bin/python -m pytest tests/agent/test_memory_provider.py -q
./.venv/bin/python -m compileall -q plugins/memory/mnemosyne
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Prepare review summary.**

Summarize:

- commit list for Mnemosyne work only;
- tests run and exact pass/fail status;
- confirmation that no network bridge is implemented;
- confirmation that L1/L2/L3 ownership boundaries remain unchanged;
- any unrelated dirty files that were intentionally left untouched.
