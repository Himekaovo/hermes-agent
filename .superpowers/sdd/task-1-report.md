# Task 1 Report: Mnemosyne Contracts and Discovery Shell

## Status

DONE

## Delivered

- Added the bundled local `MnemosyneProvider` at `plugins/memory/mnemosyne/`.
- Added immutable `MnemosyneItem` and `MnemosyneSourceRef` contracts, reason-detail deep freezing, reason-text sanitization, score clamping, and config defaults.
- Added `MnemosyneConfig.from_mapping()` with cron and subagent budget scaling using `floor(base * multiplier)` for the total budget, initial layer budgets, and hard layer caps.
- Added provider metadata and bundled-provider discovery coverage.
- Kept the provider inactive unless existing `memory.provider: mnemosyne` configuration selects it; no main-loop or lifecycle wiring changed.

## Scope Boundaries Preserved

- No L1, L2, or L4 persistence or mutation paths were added.
- No Memory Governance, holographic fact-store, SkillWiki, skill lifecycle, network, credential, model, remote backend, or background-thread behavior was added.
- The provider exposes no tools and its v1 prefetch and turn-sync methods are no-ops.

## TDD Evidence

1. Added failing contract, sanitization, config-scaling, and provider-discovery tests before production modules existed.
2. Ran the prescribed red command:

   ```bash
   ./.venv/bin/python -m pytest \
     tests/plugins/memory/test_mnemosyne.py::test_provider_discovery_loads_mnemosyne \
     tests/plugins/memory/test_mnemosyne.py::test_mnemosyne_items_deep_freeze_provenance_and_reason_detail \
     -q
   ```

   Result: `2 failed`, as expected, because `plugins.memory.mnemosyne` did not exist and provider discovery returned `None`.

3. Implemented the minimal local contracts, provider shell, and plugin metadata.
4. Ran the focused green command:

   ```bash
   ./.venv/bin/python -m pytest \
     tests/plugins/memory/test_mnemosyne.py \
     tests/agent/test_memory_provider.py::TestPluginMemoryDiscovery::test_discover_finds_providers \
     -q
   ```

   Result: `5 passed`.

## Final Verification

```bash
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py tests/agent/test_memory_provider.py -q
```

Result: `105 passed in 0.31s`.

## Files Changed

- `plugins/memory/mnemosyne/contracts.py`
- `plugins/memory/mnemosyne/__init__.py`
- `plugins/memory/mnemosyne/plugin.yaml`
- `tests/plugins/memory/test_mnemosyne.py`
- `tests/agent/test_memory_provider.py`
- `.superpowers/sdd/task-1-report.md`

## Concerns

None.
