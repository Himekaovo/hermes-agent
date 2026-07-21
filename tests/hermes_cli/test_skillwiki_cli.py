"""CLI coverage for ``hermes skills wiki`` provenance commands."""

import argparse
import json
from io import StringIO

import pytest
from rich.console import Console

import hermes_cli.skills_hub as cli
from hermes_cli.subcommands.skills import build_skills_parser


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_skills_parser(subparsers, cmd_skills=lambda _args: None)
    return parser.parse_args(argv)


def _console():
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def test_wiki_parser_exposes_nested_actions():
    args = _parse_args(["skills", "wiki", "status", "github:acme/a:", "candidate"])

    assert args.skills_action == "wiki"
    assert args.wiki_action == "status"
    assert args.new_status == "candidate"


def test_wiki_parser_rejects_invalid_relation_and_status_values():
    with pytest.raises(SystemExit):
        _parse_args(["skills", "wiki", "relation", "add", "from", "to", "invalid"])
    with pytest.raises(SystemExit):
        _parse_args(["skills", "wiki", "status", "github:acme/a:", "invalid"])


def test_wiki_import_delegates_to_existing_install(monkeypatch):
    called = {}

    monkeypatch.setattr(
        cli,
        "do_install",
        lambda identifier, **kwargs: called.update(identifier=identifier, **kwargs),
    )

    cli.do_wiki_import("acme/a", category="catalog", force=True, skip_confirm=True)

    assert called == {
        "identifier": "acme/a",
        "category": "catalog",
        "force": True,
        "skip_confirm": True,
    }


def test_wiki_status_rejects_invalid_transition(tmp_path):
    from tools.skillwiki import SkillWiki
    from tools.skills_hub import SkillBundle

    db_path = tmp_path / "provenance.db"
    wiki = SkillWiki(db_path)
    wiki.record_import(
        SkillBundle(
            name="a",
            source="github",
            trust_level="community",
            identifier="acme/a",
            files={"SKILL.md": "# a"},
            metadata={},
        ),
        tmp_path / "installed-a",
    )
    console = _console()

    exit_code = cli.do_wiki_status(
        "github:acme/a:", "release", db_path=db_path, console=console
    )

    assert exit_code != 0
    assert "invalid_transition" in console.file.getvalue()


def test_wiki_metadata_commands_do_not_create_skill_files(tmp_path):
    db_path = tmp_path / "missing" / "provenance.db"
    console = _console()

    assert cli.do_wiki_list(db_path=db_path, console=console) != 0
    assert cli.do_wiki_show("github:acme/a:", db_path=db_path, console=console) != 0
    assert cli.do_wiki_check(db_path=db_path, console=console, as_json=True) == 0

    assert not db_path.exists()
    assert not (tmp_path / "skills").exists()
    payload = json.loads(console.file.getvalue().splitlines()[-1])
    assert payload["available"] is False
