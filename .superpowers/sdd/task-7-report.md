# Task 7 Report

## Red tests

Command:

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_bridge_protocol_exists_but_provider_uses_no_network tests/plugins/memory/test_mnemosyne.py::test_provider_prefetch_merges_l1_and_l4_context tests/plugins/memory/test_mnemosyne.py::test_provider_prefetch_includes_injected_l2_and_l3_without_writing tests/plugins/memory/test_mnemosyne.py::test_provider_prefetch_fails_open_when_injected_l2_and_l3_throw -q
```

Result: expected failure, exit 1.

- `test_bridge_protocol_exists_but_provider_uses_no_network`: `ModuleNotFoundError: No module named 'plugins.memory.mnemosyne.bridge'`.
- `test_provider_prefetch_merges_l1_and_l4_context`: provider merged L1/L4, but rendered lines did not expose `reason:`.
- `test_provider_prefetch_includes_injected_l2_and_l3_without_writing`: provider prefetch returned empty because `_retriever` and `_skillwiki` are not collected.
- `test_provider_prefetch_fails_open_when_injected_l2_and_l3_throw`: passed.

## Green tests

Same focused command after implementation: `4 passed in 0.18s`.

Required verification:

- `./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q`: `53 passed in 1.31s`.
- `./.venv/bin/python -m pytest tests/agent/test_memory_provider.py -q`: `101 passed in 0.24s`.
- `./.venv/bin/python -m pytest tests/plugins/memory/test_holographic_retrieval.py tests/plugins/memory/test_holographic_store.py tests/tools/test_memory_governance.py tests/tools/test_skillwiki.py -q`: `143 passed in 1.27s`.
- `./.venv/bin/python -m compileall -q plugins/memory/mnemosyne agent tools`: exit 0.
- `git diff --check`: exit 0.

## Implementation notes

- Added `MnemosyneBridge` as a protocol only; provider does not instantiate it.
- Provider initializes L2 Holographic retriever and L3 SkillWiki best-effort and fail-open.
- Prefetch merges L1, optional L2, optional L3, and L4 before the existing budgeted render.
- L2 remains read-only via `mark_retrieved=False`; L3 uses `list_skills()` only.
- `docs/mnemosyne.md` documents opt-in, local-only, L4-candidate, non-goal, and disable boundaries.

## Important review fix

Red test:

- `./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_provider_initialize_empty_home_does_not_bootstrap_l2_db -q`: expected failure, exit 1. `MnemosyneProvider.initialize()` attached a `FactRetriever` in an empty `hermes_home`, proving it constructed Holographic storage on startup.

Fix:

- `MnemosyneProvider.initialize()` now checks for the canonical Holographic DB path, `$HERMES_HOME/memory_store.db`, before constructing `HoloStore` and `FactRetriever`.
- If the DB is absent, `_retriever` remains `None`; L2 is unavailable/fail-open and `prefetch()` does not create `memory_store.db`, `memory_store.db-wal`, or `memory_store.db-shm`.
- Optional dependency and attach failures still stay inside the existing best-effort `try`/`except` path and leave `_retriever=None`.

Green test:

- `./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_provider_initialize_empty_home_does_not_bootstrap_l2_db -q`: `1 passed in 0.26s`.
- `./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q`: `54 passed in 1.32s`.
- `./.venv/bin/python -m pytest tests/agent/test_memory_provider.py -q`: `101 passed in 0.43s`.
- `./.venv/bin/python -m compileall -q plugins/memory/mnemosyne agent tools`: exit 0.
- `git diff --check`: exit 0.

Residual concern:

- Existing L2 DB attach remains best-effort. When `memory_store.db` already exists, constructing Holographic `MemoryStore` may still run its own schema or migration path; Mnemosyne preserves fail-open behavior if that attach fails, but this fix intentionally avoids changing the Holographic store implementation.
