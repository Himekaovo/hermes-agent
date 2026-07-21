from __future__ import annotations

import json
from pathlib import Path

from tools import memory_governance as mg


def test_snapshot_listing_and_rollback_preserve_previous_bytes(tmp_path: Path):
    target = tmp_path / "MEMORY.md"
    original = "first\n§\nsecond".encode()
    target.write_bytes(original)
    versions = tmp_path / "versions"

    created = mg.snapshot(
        target,
        versions,
        target="memory",
        reason="before edit",
        operation="replace",
    )

    assert created["target"] == "memory"
    assert created["sha256"]
    assert (versions / f"{created['id']}.md").read_bytes() == original
    sidecar = json.loads((versions / f"{created['id']}.json").read_text())
    assert sidecar["operation"] == "replace"
    assert sidecar["byte_count"] == len(original)

    target.write_text("changed", encoding="utf-8")
    listed = mg.list_snapshots(versions, target="memory")
    assert listed[0]["id"] == created["id"]

    restored = mg.rollback(
        created["id"],
        target_path=target,
        versions_dir=versions,
        target="memory",
    )
    assert restored["success"]
    assert target.read_bytes() == original
    assert len(list(versions.glob("*.md"))) == 2


def test_rollback_rejects_path_traversal_and_target_mismatch(tmp_path: Path):
    target = tmp_path / "MEMORY.md"
    target.write_text("fact", encoding="utf-8")
    versions = tmp_path / "versions"
    created = mg.snapshot(target, versions, target="memory", reason="test", operation="add")

    mismatch = mg.rollback(created["id"], target_path=tmp_path / "USER.md", versions_dir=versions, target="user")
    assert not mismatch["success"]
    assert "target" in mismatch["error"]
    traversal = mg.rollback("../../MEMORY.md", target_path=target, versions_dir=versions, target="memory")
    assert not traversal["success"]


def test_protected_entries_detect_slow_update_and_named_regions():
    entries = [
        "stable preference\n<!-- SLOW_UPDATE -->",
        "<!-- PROTECTED_START: identity -->\nname: Himeka\n<!-- PROTECTED_END: identity -->",
        "ordinary note",
    ]

    protected = mg.protected_entries(entries)

    assert {item["entry_index"] for item in protected} == {0, 1}
    assert protected[0]["name"] == "SLOW_UPDATE"
    assert protected[1]["name"] == "identity"


def test_protected_spans_cross_entries_and_fail_closed_on_malformed_markers():
    entries = [
        "<!-- PROTECTED_START: account -->\nname: Himeka",
        "timezone: Asia/Shanghai",
        "<!-- PROTECTED_END: account -->\nordinary tail",
    ]
    protected = mg.protected_entries(entries)
    assert {(item["name"], item["entry_index"]) for item in protected} >= {
        ("account", 0), ("account", 1), ("account", 2)
    }

    malformed = mg.protected_entries(["<!-- PROTECTED_START: never closed -->", "ordinary"])
    assert all(item["entry_index"] in {0, 1} for item in malformed)
    assert any(item.get("malformed") for item in malformed)


def test_preflight_blocks_duplicate_and_conflicting_additions(tmp_path: Path):
    current = ["The user prefers dark mode in desktop applications."]
    duplicate = mg.preflight(
        target="memory",
        current_entries=current,
        proposed_entries=current + ["The user prefers dark mode in desktop applications."],
        operation="add",
        governance_dir=tmp_path / "governance",
    )
    assert not duplicate["allowed"]
    assert duplicate["gate"] == "conflict_detected"
    assert duplicate["conflicts"][0]["similarity"] >= 0.82

    conflict = mg.preflight(
        target="memory",
        current_entries=["The user prefers dark mode."],
        proposed_entries=["The user prefers dark mode.", "The user does not prefer dark mode."],
        operation="add",
        governance_dir=tmp_path / "governance",
    )
    assert not conflict["allowed"]
    assert conflict["gate"] == "conflict_detected"


def test_replace_does_not_conflict_with_its_previous_version(tmp_path: Path):
    current = ["The user prefers detailed technical explanations with concrete examples."]
    proposed = ["The user prefers detailed technical explanations with concise concrete examples."]
    result = mg.preflight(
        target="memory",
        current_entries=current,
        proposed_entries=proposed,
        operation="replace",
        governance_dir=tmp_path / "governance",
    )
    assert result["allowed"]


def test_replace_still_conflicts_with_another_entry(tmp_path: Path):
    current = [
        "The user prefers detailed technical explanations with concrete examples.",
        "The user prefers detailed technical explanations with concise concrete examples for documents.",
    ]
    proposed = [
        "The user prefers detailed technical explanations with concise concrete examples.",
        current[1],
    ]
    result = mg.preflight(
        target="memory",
        current_entries=current,
        proposed_entries=proposed,
        operation="replace",
        governance_dir=tmp_path / "governance",
    )
    assert not result["allowed"]
    assert result["gate"] == "conflict_detected"


def test_remove_before_protected_entry_is_allowed(tmp_path: Path):
    current = ["ordinary one", "ordinary two", "protected rule\n<!-- SLOW_UPDATE -->"]
    result = mg.preflight(
        target="memory",
        current_entries=current,
        proposed_entries=current[1:],
        operation="remove",
        governance_dir=tmp_path / "governance",
    )
    assert result["allowed"]


def test_batch_remove_before_protected_span_is_allowed(tmp_path: Path):
    current = ["ordinary one", "ordinary two", "protected rule\n<!-- SLOW_UPDATE -->"]
    result = mg.preflight(
        target="memory",
        current_entries=current,
        proposed_entries=current[1:],
        operation="batch",
        governance_dir=tmp_path / "governance",
    )
    assert result["allowed"]


def test_preflight_blocks_protected_mutation_and_bad_content(tmp_path: Path):
    current = ["critical rule\n<!-- SLOW_UPDATE -->"]
    protected = mg.preflight(
        target="memory",
        current_entries=current,
        proposed_entries=["changed"],
        operation="replace",
        governance_dir=tmp_path / "governance",
    )
    assert not protected["allowed"]
    assert protected["gate"] == "protected_region"

    bad = mg.preflight(
        target="memory",
        current_entries=[],
        proposed_entries=["§"],
        operation="add",
        governance_dir=tmp_path / "governance",
    )
    assert not bad["allowed"]
    assert bad["gate"] == "quality"


def test_preflight_uses_distinct_delimiter_reason_code(tmp_path: Path):
    result = mg.preflight(
        target="memory",
        current_entries=[],
        proposed_entries=["valid fact\n§\nsecond fact"],
        operation="add",
        governance_dir=tmp_path / "governance",
    )
    assert not result["allowed"]
    assert result["gate"] == "delimiter_abuse"


def test_step_buffer_blocks_repeated_pattern(tmp_path: Path):
    governance = tmp_path / "governance"
    first = mg.record_step(governance, "replace preference with a contradictory rule")
    second = mg.record_step(governance, "replace preference with a contradictory rule")

    assert first["count"] == 1
    assert second["count"] == 2
    result = mg.preflight(
        target="memory",
        current_entries=[],
        proposed_entries=["replace preference with a contradictory rule"],
        operation="add",
        governance_dir=governance,
    )
    assert not result["allowed"]
    assert result["gate"] == "step_buffer"


def test_step_buffer_rejects_empty_and_preserves_malformed_lines(tmp_path: Path):
    governance = tmp_path / "governance"
    governance.mkdir(parents=True)
    path = governance / "step_buffer.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    try:
        mg.record_step(governance, "")
    except ValueError:
        pass
    else:
        raise AssertionError("empty step pattern must be rejected")
    mg.record_step(governance, "real failed pattern")
    assert "not json" in path.read_text(encoding="utf-8")


def test_meta_skill_aggregates_normalized_strategy_outcomes(tmp_path: Path):
    governance = tmp_path / "governance"
    mg.record_meta(governance, "Merge overlapping entries", "success")
    mg.record_meta(governance, "merge overlapping entries", "failure")
    result = mg.preflight(
        target="memory",
        current_entries=[],
        proposed_entries=["merge overlapping entries before adding a new fact"],
        operation="add",
        governance_dir=governance,
    )
    assert result["recommendations"] == []


def test_meta_skill_returns_advisory_recommendation(tmp_path: Path):
    governance = tmp_path / "governance"
    mg.record_meta(governance, "merge overlapping entries", "success")
    mg.record_meta(governance, "merge overlapping entries", "success")
    mg.record_meta(governance, "merge overlapping entries", "failure")

    result = mg.preflight(
        target="memory",
        current_entries=[],
        proposed_entries=["merge overlapping entries before adding a new fact"],
        operation="add",
        governance_dir=governance,
    )

    assert result["allowed"]
    assert result["recommendations"][0]["strategy"] == "merge overlapping entries"
