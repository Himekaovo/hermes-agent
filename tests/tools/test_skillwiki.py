"""Focused tests for the SkillWiki provenance data layer."""

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
    from tools.skillwiki import SkillWiki

    wiki = SkillWiki(tmp_path / "provenance.db")
    bundle = _bundle("demo", "acme/demo-skill", {"SKILL.md": "# demo"})
    wiki.record_import(bundle, tmp_path / "demo")

    relation = wiki.add_relation(
        "github:acme/demo-skill:", "github:acme/other-skill:", "references"
    )
    transition = wiki.transition(
        "github:acme/demo-skill:", "verified", actor="scanner", reason="clean"
    )

    assert relation.available is True
    assert relation.reason is None
    assert isinstance(relation.items, list)
    assert relation.value["relation"] == "references"
    assert transition.available is True
    assert transition.value["status"] == "verified"
    assert wiki.list_relations().items[0]["relation"] == "references"
