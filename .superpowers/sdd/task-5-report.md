# Task 5 Report

## RED

Command:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_l2_adapter_uses_mark_retrieved_false_and_reason tests/plugins/memory/test_mnemosyne.py::test_l3_intent_routing_is_deterministic_and_read_only -q
```

Result: failed as expected.

Key failure:

```text
ImportError: cannot import name 'collect_l2_items' from 'plugins.memory.mnemosyne.injector'
ImportError: cannot import name 'collect_l3_items' from 'plugins.memory.mnemosyne.injector'
```

Review fix command:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_l3_intent_routing_is_deterministic_and_read_only tests/plugins/memory/test_mnemosyne.py::test_l2_adapter_skips_malformed_rows_with_diagnostic -q
```

Result: failed as expected.

Key failures:

```text
AssertionError: assert False is True
TypeError: float() argument must be a string or a real number, not 'NoneType'
```

## GREEN

Focused adapter command:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_l2_adapter_uses_mark_retrieved_false_and_reason tests/plugins/memory/test_mnemosyne.py::test_l2_adapter_fails_open_when_retriever_unavailable tests/plugins/memory/test_mnemosyne.py::test_l3_unavailable_skillwiki_fails_open tests/plugins/memory/test_mnemosyne.py::test_l3_intent_routing_is_deterministic_and_read_only tests/plugins/memory/test_mnemosyne.py::test_l3_filters_inactive_skills_and_lowers_incomplete_provenance_trust -q
```

Result:

```text
5 passed in 0.07s
```

Review fix command:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_l3_intent_routing_is_deterministic_and_read_only tests/plugins/memory/test_mnemosyne.py::test_l2_adapter_skips_malformed_rows_with_diagnostic -q
```

Result:

```text
2 passed in 0.06s
```

Requested focused file command:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
```

Result:

```text
41 passed in 1.20s
```

Diff hygiene:

```bash
git diff --check
```

Result: exit 0 with no output.

## Notes

- L2 calls `retriever.search(..., mark_retrieved=False)` and does not call any write or mutation path.
- L2 malformed row normalization now skips invalid rows with `l2_row_invalid` diagnostics and preserves valid rows.
- L3 calls only `SkillWiki.list_skills()` in adapter routing, filters inactive skills, and lowers trust when source provenance is incomplete.
- L3 skill/tool intent now handles simple Chinese no-space keyword queries with deterministic substring checks.
- Unrelated dirty gateway files were not edited or staged.
