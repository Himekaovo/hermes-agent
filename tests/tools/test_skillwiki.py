"""Focused tests for the SkillWiki provenance data layer."""

import sqlite3

import pytest

from tools.skills_hub import SkillBundle


def _bundle(name, identifier, files, *, metadata=None):
    return SkillBundle(
        name=name,
        files=files,
        source="github",
        identifier=identifier,
        trust_level="community",
        metadata=metadata or {},
    )


def test_record_import_persists_github_provenance(tmp_path):
    from tools.skillwiki import SkillWiki

    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# demo"})
    wiki = SkillWiki(tmp_path / "provenance.db")

    result = wiki.record_import(
        bundle,
        tmp_path / "skills" / "demo",
        ref="main",
        commit_sha="abc123",
    )

    assert result.available is True
    row = wiki.get_skill("github:acme/demo-skill:").value
    assert row["repo"] == "acme/demo-skill"
    assert row["ref"] == "main"
    assert row["commit_sha"] == "abc123"
    assert row["status"] == "raw"
    assert row["content_hash"].startswith("sha256:")


def test_local_drift_increments_count_without_rewriting_skill(tmp_path):
    from tools.skillwiki import SkillWiki

    local = tmp_path / "demo" / "SKILL.md"
    local.parent.mkdir()
    local.write_text("# local edit", encoding="utf-8")
    wiki = SkillWiki(tmp_path / "provenance.db")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# upstream"})
    wiki.record_import(bundle, local.parent)

    report = wiki.check("github:acme/demo-skill:")

    assert report["available"] is True
    assert report["skills"][0]["content_drift"] is True
    assert local.read_text(encoding="utf-8") == "# local edit"


def test_corrupt_database_is_unavailable_and_non_fatal(tmp_path):
    from tools.skillwiki import SkillWiki

    db = tmp_path / "provenance.db"
    db.write_bytes(b"not sqlite")

    result = SkillWiki(db).list_skills()

    assert result.available is False
    assert result.reason == "database_unavailable"


def test_binary_bundle_files_have_stable_hashes(tmp_path):
    from tools.skillwiki import SkillWiki

    bundle = _bundle(
        "demo",
        "acme/demo-skill",
        {"assets/data.bin": b"\x00\xff", "SKILL.md": "# demo"},
    )
    reordered = _bundle(
        "demo",
        "acme/demo-skill",
        {"SKILL.md": "# demo", "assets/data.bin": b"\x00\xff"},
    )
    wiki = SkillWiki(tmp_path / "provenance.db")

    first = wiki.record_import(bundle, tmp_path / "demo")
    second = wiki.record_import(reordered, tmp_path / "demo")

    assert first.value["content_hash"] == second.value["content_hash"]
    assert second.value["local_modified_count"] == 0


def test_repeated_identical_import_does_not_increment_drift_count(tmp_path):
    from tools.skillwiki import SkillWiki

    local = tmp_path / "demo"
    local.mkdir()
    (local / "SKILL.md").write_text("# upstream", encoding="utf-8")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# upstream"})
    wiki = SkillWiki(tmp_path / "provenance.db")

    wiki.record_import(bundle, local)
    result = wiki.record_import(bundle, local)

    assert result.value["local_modified_count"] == 0


def test_later_same_upstream_import_counts_detected_local_drift(tmp_path):
    from tools.skillwiki import SkillWiki

    local = tmp_path / "demo"
    local.mkdir()
    (local / "SKILL.md").write_text("# upstream", encoding="utf-8")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# upstream"})
    wiki = SkillWiki(tmp_path / "provenance.db")

    wiki.record_import(bundle, local)
    (local / "SKILL.md").write_text("# local edit", encoding="utf-8")
    result = wiki.record_import(bundle, local)
    repeated = wiki.record_import(bundle, local)

    assert result.available is True
    assert result.value["local_modified_count"] == 1
    assert repeated.value["local_modified_count"] == 1


def test_check_reports_missing_local_paths_without_raising(tmp_path):
    from tools.skillwiki import SkillWiki

    wiki = SkillWiki(tmp_path / "provenance.db")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# upstream"})
    wiki.record_import(bundle, tmp_path / "missing")

    report = wiki.check("github:acme/demo-skill:")

    assert report["available"] is True
    assert report["skills"][0]["local_path_exists"] is False
    assert report["skills"][0]["content_drift"] is False


def test_relations_and_transitions_keep_the_result_contract(tmp_path):
    from tools.skillwiki import RELATIONS, SkillWiki

    wiki = SkillWiki(tmp_path / "provenance.db")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# demo"})
    wiki.record_import(bundle, tmp_path / "demo")

    relation = wiki.add_relation(
        "github:acme/demo-skill:", "github:acme/other-skill:", "references"
    )
    skill_id = "github:acme/demo-skill:"
    wiki.transition(skill_id, "candidate", actor="scanner", reason="triaged")
    wiki.transition(skill_id, "draft", actor="scanner", reason="prepared")
    transition = wiki.transition(skill_id, "verified", actor="scanner", reason="clean")

    assert relation.available is True
    assert RELATIONS == ("inspired_by", "depends_on", "references")
    assert relation.reason is None
    assert isinstance(relation.items, list)
    assert relation.value["relation"] == "references"
    assert transition.available is True
    assert transition.value["status"] == "verified"
    assert wiki.list_relations().items[0]["relation"] == "references"


_EXPECTED_TRANSITIONS = {
    "raw": {"candidate", "archived"},
    "candidate": {"draft", "deprecated", "archived"},
    "draft": {"verified", "degraded", "deprecated", "archived"},
    "verified": {"release", "degraded", "deprecated", "archived"},
    "release": {"degraded", "deprecated", "archived"},
    "degraded": {"draft", "deprecated", "archived"},
    "deprecated": {"archived"},
    "archived": set(),
}


@pytest.mark.parametrize("from_status", _EXPECTED_TRANSITIONS)
@pytest.mark.parametrize("to_status", _EXPECTED_TRANSITIONS)
def test_transition_enforces_the_approved_lifecycle_table(
    tmp_path, from_status, to_status
):
    from tools.skillwiki import LIFECYCLE_STATUSES, SkillWiki

    assert LIFECYCLE_STATUSES == tuple(_EXPECTED_TRANSITIONS)
    skill_id = "github:acme/demo-skill:"
    wiki = SkillWiki(tmp_path / f"{from_status}-{to_status}.db")
    wiki.record_import(
        _bundle("demo", "acme/demo-skill", {"SKILL.md": "# demo"}),
        tmp_path / "demo",
    )
    with sqlite3.connect(wiki.db_path) as db:
        db.execute("UPDATE skills SET status = ? WHERE skill_id = ?", (from_status, skill_id))

    result = wiki.transition(skill_id, to_status, actor="reviewer", reason="test")

    assert result.available is (to_status in _EXPECTED_TRANSITIONS[from_status])
    if result.available:
        assert result.value["status"] == to_status
    else:
        assert result.reason == "invalid_transition"
        assert wiki.get_skill(skill_id).value["status"] == from_status


def test_check_returns_a_diagnostic_when_local_hashing_is_unavailable(
    tmp_path, monkeypatch
):
    from tools import skillwiki

    wiki = skillwiki.SkillWiki(tmp_path / "provenance.db")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# upstream"})
    local = tmp_path / "demo"
    local.mkdir()
    (local / "SKILL.md").write_text("# upstream", encoding="utf-8")
    wiki.record_import(bundle, local)

    def unavailable_hash(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(skillwiki, "_local_hash", unavailable_hash)

    report = wiki.check("github:acme/demo-skill:")

    assert report["available"] is True
    assert report["skills"][0]["content_drift"] is False
    assert report["skills"][0]["local_hash_available"] is False
    assert report["skills"][0]["diagnostic"] == "local_hash_unavailable"


def test_check_does_not_create_missing_database(tmp_path):
    from tools.skillwiki import SkillWiki

    db = tmp_path / "missing" / "provenance.db"
    report = SkillWiki(db).check()

    assert report == {
        "available": False,
        "reason": "database_unavailable",
        "skills": [],
    }
    assert not db.exists()


def test_legacy_lifecycle_database_is_migrated(tmp_path):
    from tools.skillwiki import SkillWiki

    db = tmp_path / "provenance.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE skills (
                skill_id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                repo TEXT, path TEXT, ref TEXT, commit_sha TEXT, source_url TEXT,
                content_hash TEXT NOT NULL, imported_at TEXT NOT NULL, local_path TEXT NOT NULL,
                local_modified_count INTEGER NOT NULL DEFAULT 0, version TEXT,
                status TEXT NOT NULL CHECK (status IN ('raw', 'quarantined', 'verified', 'active', 'stale', 'archived', 'deprecated', 'rejected')),
                metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE relations (from_skill_id TEXT NOT NULL, to_skill_id TEXT NOT NULL,
                relation TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (from_skill_id, to_skill_id, relation));
            CREATE TABLE lifecycle_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id TEXT NOT NULL, from_status TEXT NOT NULL, to_status TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO skills VALUES
                ('github:acme/demo:', 'demo', 'github', 'acme/demo', '', 'main', NULL, NULL,
                 'sha256:abc', 'now', '/tmp/demo', 0, NULL, 'active', '{}', 'now', 'now');
            INSERT INTO lifecycle_events
                (skill_id, from_status, to_status, actor, reason, created_at)
                VALUES ('github:acme/demo:', 'quarantined', 'active', 'legacy', 'imported', 'now');
            """
        )

    result = SkillWiki(db).get_skill("github:acme/demo:")

    assert result.available is True
    assert result.value["status"] == "verified"
    assert SkillWiki(db).transition("github:acme/demo:", "release", actor="test").available
    with sqlite3.connect(db) as connection:
        event = connection.execute(
            "SELECT from_status, to_status FROM lifecycle_events"
        ).fetchone()
    assert event == ("raw", "verified")


def test_remove_relation_returns_value_record(tmp_path):
    from tools.skillwiki import SkillWiki

    wiki = SkillWiki(tmp_path / "provenance.db")
    wiki.record_import(_bundle("demo", "acme/demo", {"SKILL.md": "# demo"}), tmp_path / "demo")
    wiki.add_relation("github:acme/demo:", "github:acme/other:", "references")

    result = wiki.remove_relation("github:acme/demo:", "github:acme/other:", "references")

    assert result.available is True
    assert result.value["removed"] is True


def test_list_relations_does_not_create_missing_database(tmp_path):
    from tools.skillwiki import SkillWiki

    db = tmp_path / "missing" / "provenance.db"
    result = SkillWiki(db).list_relations()

    assert result.available is False
    assert not db.exists()


def test_check_reports_malformed_local_path_without_raising(tmp_path, monkeypatch):
    from tools import skillwiki

    wiki = skillwiki.SkillWiki(tmp_path / "provenance.db")
    result = wiki.record_import(
        _bundle("demo", "acme/demo", {"SKILL.md": "# demo"}), tmp_path / "demo"
    )
    def broken_path(_value):
        raise ValueError("malformed path")

    monkeypatch.setattr(skillwiki, "Path", broken_path)

    report = wiki.check(result.value["skill_id"])

    assert report["available"] is True
    assert report["skills"][0]["diagnostic"] == "local_path_unavailable"
