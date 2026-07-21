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
