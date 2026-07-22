# Holographic Memory Provider

Local SQLite fact store with FTS5 search, trust scoring, entity resolution, and HRR-based compositional retrieval.

## Requirements

None — uses SQLite (always available). NumPy optional for HRR algebra.

## Setup

```bash
hermes memory setup    # select "holographic"
```

Or manually:
```bash
hermes config set memory.provider holographic
```

## Config

Config in `config.yaml` under `plugins.hermes-memory-store`:

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `$HERMES_HOME/memory_store.db` | SQLite database path |
| `auto_extract` | `false` | Auto-extract facts at session end |
| `default_trust` | `0.5` | Default trust score for new facts |
| `hrr_dim` | `1024` | HRR vector dimensions |

## Tools

| Tool | Description |
|------|-------------|
| `fact_store` | 11 actions: add, search, probe, related, reason, reconstruct, contradict, diagnose, update, remove, list |
| `fact_feedback` | Rate facts as helpful/unhelpful (trains trust scores) |

Search results include a `reason` object explaining why the fact was recalled:
matched query terms, scoring signals, and a short summary. Prefetched memory
context includes the same summary inline so recalled context is reviewable.

Facts receive deterministic local domain tags from their category and content
(for example `deployment`, `migration`, `bug`, and `memory`) while preserving
explicit tags. Search can route to facts containing all requested tags with
`filter_tags`. Mixed English/Chinese tokenization contributes to overlap
scoring without requiring a new package.

Use `action="reconstruct"` for a read-only multi-hop evidence pack. It runs a
base query plus optional entity hops and returns the hop fact IDs, deduplicated
evidence, and a prompt-shaped evidence block. It does not write or rewrite
memory.

`fact_store` with `action="diagnose"` returns a local memory health report:
near duplicates, stale facts, low-trust facts, simple consistency checks, and
cleanup recommendations. It is read-only: it reports findings and suggestions
but never removes, merges, or rewrites memory automatically.

Duplicate detection scans the most recently updated facts. By default it scans
`limit * 10` facts and returns up to `limit` findings; pass `scan_limit` to
increase or reduce the scan window. The report includes a `scan` object with
`total_facts`, `scanned_facts`, and `truncated` so partial scans are visible.

Stale facts are based on last use, not just total retrieval count:
`last_retrieved_at` is updated when a fact is recalled, and stale detection uses
`COALESCE(last_retrieved_at, updated_at, created_at)` against `stale_days`.
