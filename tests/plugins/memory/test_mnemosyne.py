from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
import multiprocessing
import os

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


def _write_l4_candidate_after_read_barrier(
    hermes_home: str,
    content: str,
    read_entered,
    allow_write,
    result_queue,
) -> None:
    """Run a deliberately stale read in a separate process for lock coverage."""
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(hermes_home, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    original_read = L4Store._read_raw_lines

    def read_after_barrier(self, *args, **kwargs):
        raw_lines = original_read(self, *args, **kwargs)
        read_entered.set()
        if not allow_write.wait(timeout=10):
            raise RuntimeError("timed out waiting to finish stale L4 write")
        return raw_lines

    L4Store._read_raw_lines = read_after_barrier
    try:
        result_queue.put(("ok", store.write_candidate(L4CandidateRecord.from_mapping(
            _l4_record(content=content)
        ))))
    except Exception as exc:
        result_queue.put(("error", repr(exc)))


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


def test_l4_file_symlink_escape_is_rejected_without_touching_target(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store, SecurityInvariantError

    outside = tmp_path.parent / "outside-l4.jsonl"
    outside.write_bytes(b"outside sentinel\n")
    l4_path = tmp_path / "memories" / "mnemosyne" / "l4.jsonl"
    l4_path.parent.mkdir(parents=True)
    l4_path.symlink_to(outside)
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))

    with pytest.raises(SecurityInvariantError):
        store.ensure_available()
    with pytest.raises(SecurityInvariantError):
        store.write_candidate(L4CandidateRecord.from_mapping(_l4_record()))

    assert outside.read_bytes() == b"outside sentinel\n"


def test_l4_write_separates_unterminated_malformed_line_and_remains_idempotent(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    l4_path = tmp_path / "memories" / "mnemosyne" / "l4.jsonl"
    l4_path.parent.mkdir(parents=True)
    l4_path.write_bytes(b"{bad json}")
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record())

    assert store.write_candidate(record)["written"] is True
    assert store.write_candidate(record) == {"written": False, "reason": "duplicate"}
    raw = l4_path.read_bytes().splitlines()
    assert raw[0] == b"{bad json}"
    assert json.loads(raw[1])["record_id"] == record.record_id
    records, diagnostics = store.read_records()
    assert [item["record_id"] for item in records] == [record.record_id]
    assert diagnostics == [{"reason": "malformed_l4_line", "line": 0}]


def test_l4_read_state_is_not_persisted_when_rewriting_read_output(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    initial = L4CandidateRecord.from_mapping(_l4_record())
    assert store.write_candidate(initial)["written"] is True
    records, diagnostics = store.read_records()

    assert diagnostics == []
    rewritten = L4CandidateRecord.from_mapping({**records[0], "content": "A revised lesson."})
    assert store.write_candidate(rewritten)["written"] is True
    payloads = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    assert all("read_state" not in payload for payload in payloads)


def test_l4_mapping_read_state_is_not_persisted(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(
        _l4_record(read_state={"age_days": 7, "decay_score": 0.8, "archive_recommended": False})
    )

    assert store.write_candidate(record)["written"] is True
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert "read_state" not in persisted


def test_l4_write_resanitizes_mutated_candidate_payload(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record())
    record.payload.update({
        "read_state": {"age_days": 7, "decay_score": 0.8, "archive_recommended": False},
        "decay_score": 0.8,
        "age_days": 7,
        "archive_recommended": False,
    })

    assert store.write_candidate(record)["written"] is True
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert "read_state" not in persisted
    assert "decay_score" not in persisted
    assert "age_days" not in persisted
    assert "archive_recommended" not in persisted


def test_l4_write_preserves_constructed_identity_after_payload_mutation(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record())
    record.payload["parent_session_id"] = ""
    record.payload["content"] = "A caller-mutated lesson."

    assert store.write_candidate(record)["written"] is True
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted["record_id"] == record.record_id
    assert persisted["parent_session_id"] is None
    assert persisted["content"] == _l4_record()["content"]


def test_l4_write_rejects_mutated_profile_id(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record())
    record.payload["profile_id"] = "other-profile"

    with pytest.raises(ValueError, match="profile_id"):
        store.write_candidate(record)


def test_l4_write_rejects_canonical_profile_mismatch_after_public_profile_mutation(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record(profile_id="other-profile"))
    record.payload["profile_id"] = "coder"

    with pytest.raises(ValueError, match="profile_id"):
        store.write_candidate(record)

    assert not store.path.exists()


def test_l4_canonical_payload_cannot_be_mutated_to_create_stale_id_persistence(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    record = L4CandidateRecord.from_mapping(_l4_record())

    with pytest.raises(TypeError):
        record._canonical_payload["content"] = "A stale-ID mutation."

    assert store.write_candidate(record)["written"] is True
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted["record_id"] == record.record_id
    assert persisted["content"] == _l4_record()["content"]


def test_l4_read_strips_all_legacy_derived_fields(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4Store

    l4_path = tmp_path / "memories" / "mnemosyne" / "l4.jsonl"
    l4_path.parent.mkdir(parents=True)
    l4_path.write_text(
        json.dumps({
            **_l4_record(created_at="2026-01-01T00:00:00Z"),
            "record_id": "l4_legacy",
            "decay_score": 1.0,
            "age_days": 1,
            "archive_recommended": False,
        }) + "\n",
        encoding="utf-8",
    )
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))

    records, diagnostics = store.read_records(now=datetime(2026, 7, 28, tzinfo=timezone.utc))

    assert diagnostics == []
    assert not {"decay_score", "age_days", "archive_recommended"} & records[0].keys()
    assert records[0]["read_state"] == {
        "age_days": 208,
        "decay_score": 0.0,
        "archive_recommended": True,
    }


def test_l4_parent_session_id_type_and_none_id_are_distinct_from_empty():
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord

    none_parent = L4CandidateRecord.from_mapping(_l4_record(parent_session_id=None))
    empty_parent = L4CandidateRecord.from_mapping(_l4_record(parent_session_id=""))

    assert none_parent.record_id != empty_parent.record_id
    with pytest.raises((TypeError, ValueError)):
        L4CandidateRecord.from_mapping(_l4_record(parent_session_id=42))


def test_l4_write_rejects_existing_valid_record_from_another_profile(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store, SecurityInvariantError

    l4_path = tmp_path / "memories" / "mnemosyne" / "l4.jsonl"
    l4_path.parent.mkdir(parents=True)
    existing = L4CandidateRecord.from_mapping(_l4_record(profile_id="other-profile"))
    l4_path.write_text(json.dumps(existing.payload) + "\n", encoding="utf-8")
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))

    with pytest.raises(SecurityInvariantError, match="profile_id"):
        store.write_candidate(L4CandidateRecord.from_mapping(_l4_record(content="coder lesson")))

    persisted = [json.loads(line) for line in l4_path.read_text(encoding="utf-8").splitlines()]
    assert persisted == [existing.payload]


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX multiprocessing locks")
def test_l4_interprocess_writes_preserve_both_candidates(tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4Store

    context = multiprocessing.get_context("fork")
    first_read = context.Event()
    second_read = context.Event()
    allow_first_write = context.Event()
    allow_second_write = context.Event()
    results = context.Queue()
    first = context.Process(
        target=_write_l4_candidate_after_read_barrier,
        args=(
            str(tmp_path),
            "first process lesson",
            first_read,
            allow_first_write,
            results,
        ),
    )
    second = context.Process(
        target=_write_l4_candidate_after_read_barrier,
        args=(
            str(tmp_path),
            "second process lesson",
            second_read,
            allow_second_write,
            results,
        ),
    )

    first.start()
    assert first_read.wait(timeout=5)
    second.start()
    second_read.wait(timeout=1)
    allow_first_write.set()
    first.join(timeout=10)
    assert first.exitcode == 0
    allow_second_write.set()
    second.join(timeout=10)
    assert second.exitcode == 0

    outcomes = [results.get(timeout=2), results.get(timeout=2)]
    assert all(status == "ok" and result["written"] is True for status, result in outcomes)
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))
    records, diagnostics = store.read_records()
    assert diagnostics == []
    assert {record["content"] for record in records} == {
        "first process lesson",
        "second process lesson",
    }


def test_l4_rewrite_uses_trusted_directory_fds(monkeypatch, tmp_path):
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.l4_store import L4CandidateRecord, L4Store

    if os.name != "posix" or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("platform lacks trusted directory descriptor primitives")
    if os.open not in os.supports_dir_fd or os.rename not in os.supports_dir_fd:
        pytest.skip("platform lacks dir_fd rewrite support")

    open_calls = []
    replace_calls = []
    original_open = os.open
    original_replace = os.replace

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        open_calls.append((path, flags, dir_fd))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def tracking_replace(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        replace_calls.append((source, destination, src_dir_fd, dst_dir_fd))
        return original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "replace", tracking_replace)
    store = L4Store(tmp_path, profile_id="coder", config=MnemosyneConfig.from_mapping({}))

    assert store.write_candidate(L4CandidateRecord.from_mapping(_l4_record()))["written"] is True
    assert any(
        isinstance(path, str) and path.startswith(".l4-") and dir_fd is not None
        for path, _flags, dir_fd in open_calls
    )
    assert any(
        isinstance(source, str)
        and source.startswith(".l4-")
        and destination == "l4.jsonl"
        and src_dir_fd is not None
        and dst_dir_fd is not None
        for source, destination, src_dir_fd, dst_dir_fd in replace_calls
    )


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


def test_merge_duplicate_items_keeps_authoritative_item_and_combines_provenance():
    from plugins.memory.mnemosyne.injector import merge_duplicate_items

    merged = merge_duplicate_items([
        _item("l3-copy", "L3", "Same memory", score=1.0),
        _item("l1-original", "L1", " same   memory ", score=0.1),
    ])

    assert [item.item_id for item in merged] == ["l1-original"]
    assert [ref.item_id for ref in merged[0].provenance] == ["l1-original", "l3-copy"]


def test_scale_config_for_execution_applies_subagent_multiplier_to_existing_config():
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.injector import scale_config_for_execution

    config = scale_config_for_execution(MnemosyneConfig.from_mapping({}), "subagent")

    assert config.total_char_budget == 3000
    assert config.initial_layer_budgets["L2"] == 1600
    assert config.hard_layer_caps["L2"] == 3000


def test_rendered_line_length_respects_layer_hard_cap_with_long_reason():
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.injector import render_context

    config = MnemosyneConfig.from_mapping({
        "total_char_budget": 1000,
        "max_item_chars": 40,
        "initial_layer_budgets": {"L1": 0, "L2": 70, "L3": 0, "L4": 0},
        "hard_layer_caps": {"L1": 0, "L2": 70, "L3": 0, "L4": 0},
    })
    item = replace(_item("l2-long", "L2", "content" * 20), reason="r" * 600)

    block = render_context([item], config)
    l2_lines = [line for line in block.splitlines() if line.startswith("- L2:")]

    assert l2_lines
    assert sum(map(len, l2_lines)) <= config.hard_layer_caps["L2"]
    assert len(block) <= config.total_char_budget


def test_scale_config_for_execution_honors_custom_subagent_multiplier():
    from plugins.memory.mnemosyne.contracts import MnemosyneConfig
    from plugins.memory.mnemosyne.injector import scale_config_for_execution

    config = MnemosyneConfig.from_mapping({
        "total_char_budget": 1001,
        "initial_layer_budgets": {"L2": 401},
        "hard_layer_caps": {"L2": 801},
        "subagent_multiplier": 0.25,
    })

    scaled = scale_config_for_execution(config, "subagent")

    assert scaled.total_char_budget == 250
    assert scaled.initial_layer_budgets["L2"] == 100
    assert scaled.hard_layer_caps["L2"] == 200


def test_merge_duplicate_items_with_empty_provenance_returns_new_item():
    from plugins.memory.mnemosyne.injector import merge_duplicate_items

    first = replace(_item("l2-copy", "L2", "Same memory"), provenance=())
    second = replace(_item("l1-original", "L1", "same memory"), provenance=())

    merged = merge_duplicate_items([first, second])

    assert merged[0] is not first
    assert merged[0] is not second
    assert merged[0].provenance == ()


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


def test_l1_filesystem_error_fails_open_and_collects_other_files(tmp_path):
    from plugins.memory.mnemosyne.injector import collect_l1_items

    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").mkdir()
    (memory_dir / "USER.md").write_text("prefers Chinese summaries", encoding="utf-8")

    items, diagnostics = collect_l1_items(tmp_path, include_sensitive=True)

    assert [item.item_id for item in items] == ["L1:USER.md"]
    assert diagnostics == [{"reason": "l1_filesystem_error", "source": "MEMORY.md"}]


def test_l4_records_convert_to_items_with_read_time_reason():
    from plugins.memory.mnemosyne.contracts import MnemosyneSourceRef
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
    assert items[0].provenance == (
        MnemosyneSourceRef(
            layer="L2",
            item_id="fact:1",
            source="mnemosyne:l4",
            reason_code="l4_source_ref",
            reason_detail=(),
        ),
    )


class _FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query, *, min_trust=0.3, limit=8, mark_retrieved=True, **kwargs):
        self.calls.append(("search", query, min_trust, limit, mark_retrieved, kwargs))
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
    assert retriever.calls == [("search", "migration", 0.3, 3, False, {})]
    assert items[0].item_id == "L2:fact:7"
    assert items[0].layer == "L2"
    assert items[0].reason_code == "l2_retrieval_reason"
    assert "migration" in items[0].reason
    assert items[0].reason_detail == (
        ("matched_terms", ("migration",)),
        ("strategy", "fts+jaccard+hrr+trust"),
    )
    assert items[0].score == 0.8
    assert items[0].trust == 0.8


class _UnavailableRetriever:
    def search(self, *args, **kwargs):
        raise RuntimeError("database locked")


def test_l2_adapter_fails_open_when_retriever_unavailable():
    from plugins.memory.mnemosyne.injector import collect_l2_items

    items, diagnostics = collect_l2_items(_UnavailableRetriever(), "migration")

    assert items == []
    assert diagnostics == [{
        "reason": "l2_unavailable",
        "layer": "L2",
        "message": "database locked",
    }]


class _UnavailableWiki:
    def list_skills(self):
        return type("Result", (), {
            "available": False,
            "reason": "database_unavailable",
            "items": [],
        })()


def test_l3_unavailable_skillwiki_fails_open():
    from plugins.memory.mnemosyne.injector import collect_l3_items

    items, diagnostics = collect_l3_items(_UnavailableWiki(), "skill deploy")

    assert items == []
    assert diagnostics == [{"reason": "database_unavailable", "layer": "L3"}]


class _FakeWiki:
    def __init__(self):
        self.calls = []

    def list_skills(self):
        self.calls.append("list_skills")
        return type("Result", (), {
            "available": True,
            "items": [
                {
                    "skill_id": "github:acme/deploy:",
                    "name": "deploy",
                    "status": "verified",
                    "source_url": "https://github.com/acme/deploy/tree/abc",
                    "content_hash": "sha256:abc",
                    "metadata_json": "{}",
                },
                {
                    "skill_id": "github:acme/old-deploy:",
                    "name": "old-deploy",
                    "status": "archived",
                    "source_url": "https://github.com/acme/old-deploy/tree/abc",
                    "content_hash": "sha256:def",
                    "metadata_json": "{}",
                },
                {
                    "skill_id": "github:acme/partial:",
                    "name": "partial",
                    "status": "verified",
                    "source_url": "",
                    "content_hash": "",
                    "metadata_json": "{}",
                },
            ],
        })()


def test_l3_intent_routing_is_deterministic_and_read_only():
    from plugins.memory.mnemosyne.injector import collect_l3_items, is_skill_intent

    assert is_skill_intent("which skill handles deploy?", {"deploy"}) is True
    assert is_skill_intent("如何安装技能", set()) is True
    assert is_skill_intent("这个工具怎么用", set()) is True
    assert is_skill_intent("remember my lunch", {"deploy"}) is False
    assert is_skill_intent("记住我的午餐", set()) is False

    wiki = _FakeWiki()
    items, diagnostics = collect_l3_items(
        wiki,
        "which skill handles deploy?",
        known_skill_names={"deploy"},
    )

    assert diagnostics == []
    assert wiki.calls == ["list_skills"]
    assert [item.item_id for item in items] == ["L3:github:acme/deploy:"]
    assert items[0].layer == "L3"
    assert items[0].reason_code == "l3_skill_intent_match"
    assert items[0].trust == 1.0


def test_l3_filters_inactive_skills_and_lowers_incomplete_provenance_trust():
    from plugins.memory.mnemosyne.injector import collect_l3_items

    items, diagnostics = collect_l3_items(
        _FakeWiki(),
        "which skill handles partial?",
        known_skill_names={"partial"},
    )

    assert diagnostics == []
    assert [item.item_id for item in items] == ["L3:github:acme/partial:"]
    assert items[0].trust < 1.0


class _MalformedRowRetriever:
    def search(self, query, *, min_trust=0.3, limit=8, mark_retrieved=True, **kwargs):
        return [
            {
                "fact_id": 8,
                "content": "Valid deployment memory.",
                "score": 0.7,
                "trust_score": 0.9,
                "reason": {"summary": "matched deployment"},
            },
            {
                "fact_id": 9,
                "content": "Malformed deployment memory.",
                "score": None,
                "trust_score": None,
                "reason": {"summary": "matched malformed row"},
            },
        ]


def test_l2_adapter_skips_malformed_rows_with_diagnostic():
    from plugins.memory.mnemosyne.injector import collect_l2_items

    items, diagnostics = collect_l2_items(_MalformedRowRetriever(), "deployment")

    assert [item.item_id for item in items] == ["L2:fact:8"]
    assert diagnostics == [{
        "reason": "l2_row_invalid",
        "layer": "L2",
        "fact_id": 9,
    }]


def test_l4_records_skip_malformed_source_refs_without_dropping_valid_refs():
    from plugins.memory.mnemosyne.contracts import MnemosyneSourceRef
    from plugins.memory.mnemosyne.injector import collect_l4_items

    records = [{
        "record_id": "l4_a",
        "content": "Use rollback before retrying migrations.",
        "confidence": 0.7,
        "governance_state": "candidate",
        "read_state": {"decay_score": 0.49, "archive_recommended": False},
        "source_refs": [
            {"layer": "L9", "item_id": "bad-layer"},
            {"layer": "L2"},
            "not a mapping",
            {
                "layer": "L2",
                "item_id": "fact:1",
                "source": "holographic",
                "reason_code": "l2_query_tag_match",
                "reason_detail": {"matched_tags": ["migration"]},
            },
        ],
    }]

    items = collect_l4_items(records)

    assert items[0].provenance == (
        MnemosyneSourceRef(
            layer="L2",
            item_id="fact:1",
            source="holographic",
            reason_code="l2_query_tag_match",
            reason_detail=(("matched_tags", ("migration",)),),
        ),
    )
