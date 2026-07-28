# Task 4 Report: Mnemosyne L1 and L4 Adapters

## Status

DONE_WITH_CONCERNS

## Scope Delivered

- Added `collect_l1_items()` in `plugins/memory/mnemosyne/injector.py`.
  - Reads only fixed `memories/MEMORY.md` and, when `include_sensitive=True`, `memories/USER.md` paths beneath the trusted Hermes home.
  - Ignores `query` for path selection, fails open for missing files, reports encoding failures, and returns a security diagnostic for an escaped resolved path.
- Added `collect_l4_items()` in `plugins/memory/mnemosyne/injector.py`.
  - Converts L4 read records into `MnemosyneItem` instances only; it performs no persistence or writes.
  - Uses derived `read_state` and includes the archive recommendation in the item reason.
- Added the three Task 4 adapter tests in `tests/plugins/memory/test_mnemosyne.py`.

## TDD Evidence

1. Added the L1 and L4 adapter tests before production code.
2. Ran:

   ```bash
   ./.venv/bin/python -m pytest \
     tests/plugins/memory/test_mnemosyne.py::test_l1_missing_files_fail_open \
     tests/plugins/memory/test_mnemosyne.py::test_l4_records_convert_to_items_with_read_time_reason \
     -q
   ```

   Result: expected red state, 2 failures caused by missing `collect_l1_items` and `collect_l4_items` imports.
3. Implemented the two adapters.
4. Ran:

   ```bash
   ./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
   ```

   Result: 33 passed in 1.16s.
5. Ran `git diff --check`; result: no whitespace errors.

## Constraint Review

- No Agent main-loop changes.
- No Memory Governance, L2 fact-store, SkillWiki, network, credential, model, background-thread, remote-bridge, or external-backend changes.
- No L4 persistence changes; candidate storage remains owned by the existing L4 store.
- The only intended staged files are the Mnemosyne injector and its test module.

## Concern

The prescribed L4 implementation snippet multiplies `read_state.decay_score` by `confidence`, but the prescribed test asserts `0.49` from `decay_score=0.49` and `confidence=0.7`. The adapter uses the asserted score (`0.49`) so the specified test contract passes; confidence remains represented by `MnemosyneItem.trust`.

## Review Fix: L1 Filesystem Fail-Open

- `collect_l1_items()` now catches `OSError` from both `path.exists()` and
  `path.read_text()` on a per-file basis.
- Filesystem failures emit `{"reason": "l1_filesystem_error", "source": name}`
  and do not prevent subsequent trusted L1 files from being read.
- L4 behavior is unchanged: `score` remains the read-time `decay_score` and
  `trust` remains `confidence`.

## Review Fix TDD Evidence

1. RED: added `test_l1_filesystem_error_fails_open_and_collects_other_files`,
   creating `memories/MEMORY.md` as a directory and a readable `USER.md`.
2. RED command:

   ```bash
   ./.venv/bin/python -m pytest \
     tests/plugins/memory/test_mnemosyne.py::test_l1_filesystem_error_fails_open_and_collects_other_files \
     -q
   ```

   Result: expected failure with `IsADirectoryError` from
   `MEMORY.md.read_text()`.
3. GREEN: caught `OSError` around the L1 existence/read operations and
   continued the per-file loop.
4. GREEN command: same focused command; result: `1 passed in 0.07s`.
5. Regression suite command:

   ```bash
   ./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
   ```

   Result: `34 passed in 1.16s`.

## Review Fix: L4 Source Ref Provenance

- `collect_l4_items()` now converts valid L4 `source_refs` entries into
  `MnemosyneSourceRef` provenance.
- Minimal refs preserve required `layer` and `item_id` with safe defaults:
  `source="mnemosyne:l4"` and `reason_code="l4_source_ref"`.
- Refs with available `source`, `reason_code`, and `reason_detail` preserve
  those fields.
- Malformed refs are skipped defensively so `collect_l4_items()` does not crash.

## Review Fix TDD Evidence: L4 Source Ref Provenance

1. RED: strengthened `test_l4_records_convert_to_items_with_read_time_reason`
   to assert the existing L4 `source_refs` fixture is preserved as
   `MnemosyneSourceRef` provenance, and added
   `test_l4_records_skip_malformed_source_refs_without_dropping_valid_refs`.
2. RED command:

   ```bash
   ./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_l4_records_convert_to_items_with_read_time_reason tests/plugins/memory/test_mnemosyne.py::test_l4_records_skip_malformed_source_refs_without_dropping_valid_refs -q
   ```

   Result: expected failure, `2 failed`, both because `items[0].provenance`
   was `()`.
3. GREEN: added L4 source-ref parsing in
   `plugins/memory/mnemosyne/injector.py` and wired it into
   `collect_l4_items()` only.
4. GREEN focused command:

   ```bash
   ./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py::test_l4_records_convert_to_items_with_read_time_reason tests/plugins/memory/test_mnemosyne.py::test_l4_records_skip_malformed_source_refs_without_dropping_valid_refs -q
   ```

   Result: `2 passed in 0.06s`.
5. Regression suite command:

   ```bash
   ./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
   ```

   Result: `35 passed in 1.17s`.
