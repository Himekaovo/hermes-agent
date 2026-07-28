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

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages=None,
    ) -> None:
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs: Any
    ) -> str:
        raise NotImplementedError(f"Mnemosyne exposes no tools in v1: {tool_name}")
