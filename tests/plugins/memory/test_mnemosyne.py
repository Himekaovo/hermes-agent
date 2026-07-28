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


def test_config_uses_defaults_and_scales_cron_layer_budgets():
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig

    config = MnemosyneConfig.from_mapping(
        {"total_char_budget": 7001}, execution_kind="cron"
    )

    assert config.total_char_budget == 3500
    assert config.initial_layer_budgets == {
        "L1": 600,
        "L2": 1600,
        "L3": 400,
        "L4": 400,
    }
    assert config.hard_layer_caps == {
        "L1": 1200,
        "L2": 3000,
        "L3": 800,
        "L4": 800,
    }


def test_provider_discovery_loads_mnemosyne():
    from plugins.memory import load_memory_provider

    provider = load_memory_provider("mnemosyne")

    assert provider is not None
    assert provider.name == "mnemosyne"
    assert provider.is_available() is True
    assert provider.get_tool_schemas() == []
    assert "Memory Tower" in provider.system_prompt_block()
