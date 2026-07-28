## 2026-07-28

- Prevent blocked, incomplete, and subagent turns from submitting `session-archiver` memory-governance candidates; keep sanitized audit writes fail-open.
- Skip external-memory sync/prefetch for failed, incomplete, and safety-blocked turns while preserving normal successful sync.
- Ensure `on_session_end` always runs best-effort safety-hook cleanup and restores session-local ContextVar state without suppressing the audit result.
