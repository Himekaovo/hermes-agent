from __future__ import annotations

from typing import Protocol

from .contracts import MnemosyneItem


class MnemosyneBridge(Protocol):
    def fetch(self, query: str, *, layer: str) -> list[MnemosyneItem]:
        ...

    def publish(self, items: list[MnemosyneItem]) -> None:
        ...
