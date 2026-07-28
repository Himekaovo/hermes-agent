from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json

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
