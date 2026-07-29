from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider

from .contracts import MnemosyneConfig
from .injector import (
    collect_l1_items,
    collect_l2_items,
    collect_l3_items,
    collect_l4_items,
    render_context,
)
from .l4_store import L4CandidateRecord, L4Store, SecurityInvariantError


_OBSERVED_MEMORY_ACTIONS = {"add", "replace"}
_OBSERVED_EXECUTION_KINDS = {"interactive", "primary", "subagent", "cron", "flush"}


def _skillwiki_db_path(hermes_home: Path) -> Path | None:
    root = Path(hermes_home).expanduser().resolve(strict=False)
    db_path = root / "skills" / ".hub" / "provenance.db"
    try:
        db_path.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return None
    return db_path


class MnemosyneProvider(MemoryProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._raw_config = dict(config or {})
        self._config = MnemosyneConfig.from_mapping(self._raw_config)
        self._session_id = ""
        self._parent_session_id: str | None = None
        self._profile_id = "default"
        self._agent_id = "agent"
        self._execution_kind = "interactive"
        self._hermes_home = Path(".")
        self._candidate_buffer: list[dict[str, object]] = []
        self._l4_store: L4Store | None = None
        self._retriever: object | None = None
        self._skillwiki: object | None = None
        self._disabled = False

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._parent_session_id = kwargs.get("parent_session_id") or None
        self._profile_id = str(kwargs.get("agent_identity") or "default")
        self._agent_id = str(
            kwargs.get("agent_id") or kwargs.get("agent_identity") or "agent"
        )
        execution_kind = str(
            kwargs.get("execution_kind") or kwargs.get("agent_context") or "interactive"
        )
        self._execution_kind = (
            "interactive" if execution_kind == "primary" else execution_kind
        )
        self._hermes_home = Path(str(kwargs.get("hermes_home") or "."))
        self._config = MnemosyneConfig.from_mapping(
            self._raw_config,
            execution_kind=self._execution_kind,
        )
        self._candidate_buffer = []
        self._disabled = False
        self._l4_store = L4Store(
            self._hermes_home,
            profile_id=self._profile_id,
            config=self._config,
        )
        self._retriever = None
        self._skillwiki = None
        try:
            from plugins.memory.holographic.retrieval import FactRetriever
            from plugins.memory.holographic.store import MemoryStore as HoloStore

            holo_db = self._hermes_home / "memory_store.db"
            if holo_db.exists():
                self._retriever = FactRetriever(store=HoloStore(db_path=holo_db))
        except Exception:
            self._retriever = None
        try:
            from tools.skillwiki import SkillWiki

            skillwiki_db = _skillwiki_db_path(self._hermes_home)
            if skillwiki_db is not None:
                self._skillwiki = SkillWiki(skillwiki_db)
        except Exception:
            self._skillwiki = None

    def system_prompt_block(self) -> str:
        return (
            "# Mnemosyne Memory Tower\n"
            "Active local memory orchestration. L4 reflections are candidates, "
            "not approved facts."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._disabled:
            return ""
        l1_items = []
        l2_items = []
        l3_items = []
        l4_items = []
        try:
            include_sensitive = self._include_sensitive_l1()
            l1_items, l1_diagnostics = collect_l1_items(
                self._hermes_home,
                include_sensitive=include_sensitive,
                query=query,
            )
            if _has_security_diagnostic(l1_diagnostics):
                self._disabled = True
                return ""
        except SecurityInvariantError:
            self._disabled = True
            return ""
        except Exception:
            l1_items = []
        try:
            retriever = getattr(self, "_retriever", None)
            if retriever is not None:
                l2_items, _l2_diagnostics = collect_l2_items(
                    retriever,
                    query,
                    limit=self._config.max_items_per_layer,
                )
        except Exception:
            l2_items = []
        try:
            skillwiki = getattr(self, "_skillwiki", None)
            if skillwiki is not None:
                l3_items, _l3_diagnostics = collect_l3_items(skillwiki, query)
        except Exception:
            l3_items = []
        try:
            if self._l4_store is not None:
                records, l4_diagnostics = self._l4_store.read_records()
                if _has_security_diagnostic(l4_diagnostics):
                    self._disabled = True
                    return ""
                l4_items = collect_l4_items(records)
        except SecurityInvariantError:
            self._disabled = True
            return ""
        except Exception:
            l4_items = []
        items = l1_items + l2_items + l3_items + l4_items
        if not items:
            return ""
        try:
            return render_context(items, self._config)
        except Exception:
            return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages=None,
    ) -> None:
        if self._disabled or not self._raw_config.get("l4_candidate_observation"):
            return
        if self._execution_kind not in _OBSERVED_EXECUTION_KINDS:
            return
        content = " ".join((user_content or "").split())
        if not content:
            return
        self._append_candidate(
            kind="reflection",
            content=content[:1000],
            session_id=session_id or self._session_id,
            confidence=0.5,
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs: Any
    ) -> str:
        raise NotImplementedError(f"Mnemosyne exposes no tools in v1: {tool_name}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._disabled or self._l4_store is None:
            return
        try:
            for raw in list(self._candidate_buffer):
                self._l4_store.write_candidate(L4CandidateRecord.from_mapping(raw))
        except SecurityInvariantError:
            self._disabled = True
            return
        except Exception:
            return
        self._candidate_buffer.clear()

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        if self._disabled or not self._raw_config.get("l4_candidate_observation"):
            return
        if action not in _OBSERVED_MEMORY_ACTIONS:
            return
        normalized = " ".join((content or "").split())
        if not normalized:
            return
        metadata = dict(metadata or {})
        self._append_candidate(
            kind="reflection",
            content=normalized[:1000],
            session_id=str(metadata.get("session_id") or self._session_id),
            parent_session_id=metadata.get(
                "parent_session_id",
                self._parent_session_id,
            ),
            confidence=0.7,
            source_refs=[
                {
                    "layer": "L1",
                    "item_id": str(target or "memory"),
                    "source": "memory_write",
                    "reason_code": f"memory_write_{action}",
                }
            ],
        )

    def _append_candidate(
        self,
        *,
        kind: str,
        content: str,
        session_id: str,
        confidence: float,
        parent_session_id: object | None = None,
        source_refs: list[dict[str, object]] | None = None,
    ) -> None:
        candidate = {
            "kind": kind,
            "content": content,
            "profile_id": self._profile_id,
            "session_id": session_id,
            "parent_session_id": (
                self._parent_session_id
                if parent_session_id is None
                else parent_session_id
            ),
            "agent_id": self._agent_id,
            "execution_kind": self._execution_kind,
            "confidence": confidence,
            "governance_state": "candidate",
            "source_refs": source_refs or [],
        }
        try:
            record_id = L4CandidateRecord.from_mapping(candidate).record_id
        except (TypeError, ValueError):
            return
        existing_ids = {
            L4CandidateRecord.from_mapping(raw).record_id
            for raw in self._candidate_buffer
        }
        if record_id not in existing_ids:
            self._candidate_buffer.append(candidate)

    def _include_sensitive_l1(self) -> bool:
        return self._execution_kind == "interactive" and self._parent_session_id is None


def _has_security_diagnostic(diagnostics: list[dict[str, object]]) -> bool:
    return any(
        diagnostic.get("reason") in {
            "security_invariant_failure",
            "profile_mismatch",
            "provider_disabled",
        }
        for diagnostic in diagnostics
    )
