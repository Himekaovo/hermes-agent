"""Deterministic safety hook primitives for pre/post-LLM guardrails."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable


ALLOWED_ACTIONS = frozenset({"allow", "warn", "block", "error", "skip"})
ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical", "unknown"})
ALLOWED_EXECUTION_KINDS = frozenset({"interactive", "cron", "subagent"})
ALLOWED_VERIFICATION_STATUSES = frozenset({"passed", "failed", "not_run", "unavailable"})
_MAX_TEXT_CHARS = 200
_MAX_LIST_ITEMS = 20
_MAX_DICT_ITEMS = 20
_MAX_DEPTH = 4
_MAX_AUDIT_MESSAGES = 8
_REDACTED = "[REDACTED]"
_SAFE_EGRESS_BLOCK_MESSAGE = (
    "I can't share that response because it may contain sensitive data."
)
_SECRET_KEY_NAMES = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "key",
    "password",
    "private_key",
    "secret",
    "token",
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?P<key>api[_-]?key|token|password|secret|private[_-]?key|authorization)\b(?P<sep>\s*[:=]\s*)(?P<value>\S+)"
)
_SECRET_TOKEN_RE = re.compile(
    r"(?i)\b(?:sk|ghp|github_pat|xox[baprs]?|AKIA|AIza|hf|pypi|npm)[-_A-Za-z0-9]+\b"
)
_PROMPT_INJECTION_PATTERNS = (
    "system prompt",
    "ignore previous instructions",
    "developer message",
    "hidden prompt",
    "reveal the prompt",
)


class ExecutionContextError(ValueError):
    """Raised when structured execution context is missing required fields."""

    def __init__(self, missing_fields: list[str]):
        self.missing_fields = missing_fields
        message = ", ".join(missing_fields)
        super().__init__(f"Missing execution context fields: {message}")


def bounded_context(value: Any, *, max_chars: int = _MAX_TEXT_CHARS) -> str:
    text = "" if value is None else str(value)
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    return text[:max_chars]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _contains_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SECRET_KEY_NAMES)


def _sanitize_string(value: str) -> str:
    redacted = _SECRET_VALUE_RE.sub(r"\g<key>\g<sep>[REDACTED]", value)
    redacted = _SECRET_TOKEN_RE.sub(_REDACTED, redacted)
    return bounded_context(redacted)


def _truncated_marker(value_type: str, size: int | None = None) -> dict[str, Any]:
    marker: dict[str, Any] = {"truncated": True, "type": value_type}
    if size is not None:
        marker["size"] = size
    return marker


def _sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        if isinstance(value, str):
            return _sanitize_string(value)
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            return _truncated_marker("dict", min(len(value), _MAX_DICT_ITEMS))
        if isinstance(value, (list, tuple)):
            return _truncated_marker("list", min(len(value), _MAX_LIST_ITEMS))
        return _truncated_marker(type(value).__name__)
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key in list(value)[:_MAX_DICT_ITEMS]:
            key_text = bounded_context(key, max_chars=80)
            item = value[key]
            if _contains_secret_key(key_text):
                sanitized[key_text] = _REDACTED
            else:
                sanitized[key_text] = _sanitize_value(item, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, depth=depth + 1) for item in list(value)[:_MAX_LIST_ITEMS]]
    return bounded_context(value)


def make_result(
    *,
    hook: str,
    event: str,
    action: str,
    reason_code: str,
    risk_level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported action: {action}")
    if risk_level not in ALLOWED_RISK_LEVELS:
        raise ValueError(f"Unsupported risk level: {risk_level}")
    return {
        "hook": bounded_context(hook, max_chars=80),
        "event": bounded_context(event, max_chars=80),
        "action": action,
        "reason_code": bounded_context(reason_code, max_chars=80),
        "risk_level": risk_level,
        "message": _sanitize_string(message),
        "metadata": _sanitize_value(metadata or {}),
    }


def normalize_execution_context(payload: dict[str, Any]) -> dict[str, Any]:
    missing_fields = [
        field
        for field in ("agent_id", "execution_kind", "session_id", "task_id", "turn_id")
        if not bounded_context(payload.get(field), max_chars=120).strip()
    ]
    execution_kind = bounded_context(payload.get("execution_kind"), max_chars=120).strip()
    if execution_kind and execution_kind not in ALLOWED_EXECUTION_KINDS:
        missing_fields.append(f"execution_kind:{execution_kind}")
    if missing_fields:
        raise ExecutionContextError(missing_fields)
    context: dict[str, Any] = {
        "agent_id": bounded_context(payload["agent_id"], max_chars=120),
        "execution_kind": execution_kind,
        "session_id": bounded_context(payload["session_id"], max_chars=120),
        "task_id": bounded_context(payload["task_id"], max_chars=120),
        "turn_id": bounded_context(payload["turn_id"], max_chars=120),
        "parent_session_id": None,
    }
    parent_session_id = payload.get("parent_session_id")
    if parent_session_id is not None:
        parent_text = bounded_context(parent_session_id, max_chars=120).strip()
        context["parent_session_id"] = None if not parent_text else _sanitize_value(parent_text)
    for optional in (
        "user_message",
        "planning_mode",
        "subagent_id",
        "delegation_target",
        "requested_path",
        "requested_paths",
        "context_sources",
        "memory_hits",
    ):
        if optional in payload:
            context[optional] = _sanitize_value(payload[optional])
    return context


def _execution_context_error_result(
    event: str,
    payload: Mapping[str, Any],
    missing_fields: list[str],
) -> dict[str, Any]:
    return make_result(
        hook="execution-context",
        event=event,
        action="error",
        reason_code="execution_context_invalid",
        risk_level="unknown",
        message="Structured execution context is incomplete.",
        metadata={
            "missing_fields": missing_fields,
            "session_id": payload.get("session_id", ""),
            "execution_kind": payload.get("execution_kind", ""),
        },
    )


def _malformed_payload_error_result(event: str, payload: Any) -> dict[str, Any]:
    return make_result(
        hook="safety-hooks",
        event=event,
        action="error",
        reason_code="malformed_payload",
        risk_level="unknown",
        message="Safety checks require a mapping payload.",
        metadata={
            "payload_type": type(payload).__name__,
            "payload_preview": _sanitize_value(payload),
        },
    )


def _check_identity(event: str, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    session_id = bounded_context(payload.get("session_id"), max_chars=120).strip()
    if not session_id:
        return make_result(
            hook="identity",
            event=event,
            action="block",
            reason_code="identity_missing",
            risk_level="high",
            message="Structured execution identity is required.",
            metadata={"missing_fields": ["session_id"]},
        )
    return make_result(
        hook="identity",
        event=event,
        action="allow",
        reason_code="identity_present",
        risk_level="low",
        message="Structured execution identity present.",
        metadata={"session_id": session_id},
    )


def _check_mode(event: str, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    planning_mode = bool(payload.get("planning_mode"))
    return make_result(
        hook="pm-mode",
        event=event,
        action="skip" if not planning_mode else "allow",
        reason_code="planning_mode_absent" if not planning_mode else "planning_mode_present",
        risk_level="unknown" if not planning_mode else "low",
        message="Planning mode not active." if not planning_mode else "Planning mode active.",
        metadata={"planning_mode": planning_mode},
    )


def _check_subagent(event: str, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    delegate = bounded_context(payload.get("delegation_target"), max_chars=120).strip()
    return make_result(
        hook="subagent-checklist",
        event=event,
        action="skip" if not delegate else "allow",
        reason_code="delegation_absent" if not delegate else "delegation_structured",
        risk_level="unknown" if not delegate else "low",
        message="No delegation target provided." if not delegate else "Delegation target present.",
        metadata={"delegation_target": delegate},
    )


def _check_local_recall(event: str, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    memory_hits = payload.get("memory_hits")
    has_hits = isinstance(memory_hits, list) and bool(memory_hits)
    return make_result(
        hook="local-recall",
        event=event,
        action="skip" if not has_hits else "allow",
        reason_code="local_recall_unavailable" if not has_hits else "local_recall_present",
        risk_level="unknown" if not has_hits else "low",
        message="No local recall attached." if not has_hits else "Local recall attached.",
        metadata={"memory_hits": memory_hits if has_hits else []},
    )


def _check_context_propagation(event: str, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if not context:
        return make_result(
            hook="context-propagation",
            event=event,
            action="skip",
            reason_code="context_unavailable",
            risk_level="unknown",
            message="Structured task context unavailable.",
            metadata={},
        )
    task_id = bounded_context(payload.get("task_id"), max_chars=120).strip()
    turn_id = bounded_context(payload.get("turn_id"), max_chars=120).strip()
    missing: list[str] = []
    if not task_id:
        missing.append("task_id")
    if not turn_id:
        missing.append("turn_id")
    if missing:
        return make_result(
            hook="context-propagation",
            event=event,
            action="warn",
            reason_code="context_fields_missing",
            risk_level="medium",
            message="Structured task context is incomplete.",
            metadata={"missing_fields": missing},
        )
    return make_result(
        hook="context-propagation",
        event=event,
        action="allow",
        reason_code="context_fields_present",
        risk_level="low",
        message="Structured task context present.",
        metadata={"task_id": task_id, "turn_id": turn_id},
    )


def _iter_candidate_paths(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("requested_path", "user_message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    requested_paths = payload.get("requested_paths")
    if isinstance(requested_paths, list):
        for item in requested_paths[:_MAX_LIST_ITEMS]:
            if isinstance(item, str) and item:
                paths.append(item)
    return paths


def _check_security(event: str, payload: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    message = bounded_context(payload.get("user_message"), max_chars=400).lower()
    if any(part == ".." for candidate in _iter_candidate_paths(payload) for part in PurePath(candidate).parts):
        results.append(
            make_result(
                hook="security-inspector",
                event=event,
                action="block",
                reason_code="dangerous_path",
                risk_level="high",
                message="Path traversal content detected.",
                metadata={"requested_paths": _iter_candidate_paths(payload)},
            )
        )
    if any(pattern in message for pattern in _PROMPT_INJECTION_PATTERNS):
        results.append(
            make_result(
                hook="security-inspector",
                event=event,
                action="block",
                reason_code="prompt_injection_detected",
                risk_level="high",
                message="Prompt exfiltration attempt detected.",
                metadata={"user_message": payload.get("user_message", "")},
            )
        )
    if _SECRET_VALUE_RE.search(message) or _SECRET_TOKEN_RE.search(message):
        results.append(
            make_result(
                hook="security-inspector",
                event=event,
                action="warn",
                reason_code="secret_detected",
                risk_level="medium",
                message="Secret-like material detected in user content.",
                metadata={"user_message": payload.get("user_message", "")},
            )
        )
    if not results:
        results.append(
            make_result(
                hook="security-inspector",
                event=event,
                action="allow",
                reason_code="security_clear",
                risk_level="low",
                message="Security inspection clear.",
                metadata={},
            )
        )
    return results


def _normalize_verification_status(value: Any, *, evidence_present: bool = False) -> str:
    status = bounded_context(value, max_chars=40).strip().lower()
    if status in ALLOWED_VERIFICATION_STATUSES:
        if status == "passed" and not evidence_present:
            return "not_run"
        return status
    if status in {"unverified", "stale"}:
        return "not_run"
    if status in {"not_applicable", "n/a", "na"}:
        return "unavailable"
    return "unavailable"


def _verification_status_from_payload(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    verification = payload.get("verification")
    if verification is None:
        return "unavailable", {
            "source": "payload",
            "verification_present": False,
            "verification_status": "unavailable",
        }
    if not isinstance(verification, Mapping):
        return "unavailable", {
            "source": "payload",
            "verification_present": True,
            "verification_type": type(verification).__name__,
            "verification_status": "unavailable",
        }
    evidence = verification.get("evidence")
    evidence_present = evidence not in (None, "", [], {})
    status = _normalize_verification_status(
        verification.get("status"),
        evidence_present=evidence_present,
    )
    metadata = {
        "source": "payload",
        "verification_present": True,
        "verification_status": status,
        "evidence_present": evidence_present,
    }
    for key in ("command", "summary", "reason", "tool"):
        if key in verification:
            metadata[key] = _sanitize_value(verification.get(key))
    return status, metadata


def _iter_text_candidates(*values: Any) -> list[str]:
    return [value for value in values if isinstance(value, str) and value]


def _contains_secret_like_text(*values: Any) -> bool:
    return any(
        _SECRET_VALUE_RE.search(candidate) or _SECRET_TOKEN_RE.search(candidate)
        for candidate in _iter_text_candidates(*values)
    )


def _check_egress_inspector(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    original = payload.get("original_assistant_response")
    visible = payload.get("assistant_response")
    secret_found = _contains_secret_like_text(original, visible)
    return make_result(
        hook="egress-inspector",
        event=event,
        action="block" if secret_found else "allow",
        reason_code="secret_detected" if secret_found else "egress_clear",
        risk_level="high" if secret_found else "low",
        message=_SAFE_EGRESS_BLOCK_MESSAGE if secret_found else "Egress inspection clear.",
        metadata={
            "inspected_original": isinstance(original, str) and bool(original),
            "inspected_visible": isinstance(visible, str) and bool(visible),
            "response_preview": _sanitize_value(original if isinstance(original, str) else visible),
        },
    )


def _check_verification_gate(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    status, metadata = _verification_status_from_payload(payload)
    return make_result(
        hook="verification-gate",
        event=event,
        action="allow" if status == "passed" else "skip",
        reason_code=f"verification_{status}",
        risk_level="low" if status == "passed" else "unknown",
        message="Verification evidence attached." if status == "passed" else "Verification evidence unavailable or incomplete.",
        metadata=metadata,
    )


def _check_a2a_metadata_processor(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = payload.get("a2a_metadata")
    present = isinstance(metadata, Mapping) and bool(metadata)
    return make_result(
        hook="a2a-metadata-processor",
        event=event,
        action="allow" if present else "skip",
        reason_code="a2a_metadata_present" if present else "a2a_metadata_absent",
        risk_level="low" if present else "unknown",
        message="A2A metadata processed." if present else "No A2A metadata supplied.",
        metadata={"a2a_metadata": _sanitize_value(metadata or {})},
    )


def _check_generic_postprocessor(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    response = payload.get("assistant_response")
    return make_result(
        hook="generic-postprocessor",
        event=event,
        action="allow",
        reason_code="postprocess_complete",
        risk_level="low",
        message="Generic postprocessing completed.",
        metadata={
            "response_chars": len(response) if isinstance(response, str) else 0,
            "response_transformed": bool(payload.get("response_transformed")),
        },
    )


def _run_post_llm_checks(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return run_builtin_post_llm_checks(payload)


def run_builtin_post_llm_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _check_egress_inspector("post_llm_call", payload)


def run_builtin_post_llm_checks(
    payload: Mapping[str, Any],
    *,
    egress_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        egress_result if isinstance(egress_result, dict) else run_builtin_post_llm_preflight(payload),
        _check_verification_gate("post_llm_call", payload),
        _check_a2a_metadata_processor("post_llm_call", payload),
        _check_generic_postprocessor("post_llm_call", payload),
    ]


def _bounded_message_preview(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    preview: list[dict[str, Any]] = []
    for message in messages[-_MAX_AUDIT_MESSAGES:]:
        if not isinstance(message, Mapping):
            preview.append({"type": type(message).__name__, "value": _sanitize_value(message)})
            continue
        preview.append(
            {
                "role": bounded_context(message.get("role"), max_chars=40),
                "content": _sanitize_value(message.get("content")),
            }
        )
    return preview


def _audit_log_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "logs" / "safety" / "session-archiver.jsonl"


def _emit_memory_governance_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    from hermes_constants import get_hermes_home
    from tools import memory_governance

    governance_dir = get_hermes_home() / "memories" / "governance"
    note = json.dumps(_sanitize_value(candidate), ensure_ascii=False, sort_keys=True)
    return memory_governance.record_meta(
        governance_dir,
        "session_archiver_candidate",
        "success",
        note=note,
    )


def _run_session_archiver(payload: Mapping[str, Any]) -> dict[str, Any]:
    verification_status, _ = _verification_status_from_payload(payload)
    incidents = payload.get("safety_results")
    incident_count = len(incidents) if isinstance(incidents, list) else 0
    record = {
        "created_at": _now_iso(),
        "session_id": bounded_context(payload.get("session_id"), max_chars=120),
        "task_id": bounded_context(payload.get("task_id"), max_chars=120),
        "turn_id": bounded_context(payload.get("turn_id"), max_chars=120),
        "agent_id": bounded_context(payload.get("agent_id"), max_chars=120),
        "execution_kind": bounded_context(payload.get("execution_kind"), max_chars=120),
        "completed": bool(payload.get("completed")),
        "interrupted": bool(payload.get("interrupted")),
        "safety_blocked": bool(payload.get("safety_blocked")),
        "verification_status": verification_status,
        "incident_count": incident_count,
        "safety_results": _sanitize_value(incidents or []),
        "conversation_preview": _bounded_message_preview(payload.get("conversation_history")),
    }
    audit_path = _audit_log_path()
    archive_status = "written"
    memory_candidate_status = "not_emitted"
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_sanitize_value(record), ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        archive_status = "write_failed"
    if record["conversation_preview"]:
        try:
            _emit_memory_governance_candidate(
                {
                    "session_id": record["session_id"],
                    "turn_id": record["turn_id"],
                    "verification_status": verification_status,
                    "incident_count": incident_count,
                    "conversation_preview": record["conversation_preview"],
                }
            )
            memory_candidate_status = "submitted"
        except Exception:
            memory_candidate_status = "governance_missing"
    return make_result(
        hook="session-archiver",
        event="on_session_end",
        action="allow" if archive_status == "written" else "warn",
        reason_code="session_archived" if archive_status == "written" else "session_archive_degraded",
        risk_level="low" if archive_status == "written" else "unknown",
        message="Session archive recorded." if archive_status == "written" else "Session archive completed with degraded protections.",
        metadata={
            "audit_path": str(audit_path),
            "archive_status": archive_status,
            "verification_status": verification_status,
            "incident_count": incident_count,
            "memory_candidate_status": memory_candidate_status,
            "session_id": payload.get("session_id"),
        },
    )


def run_safety_checks(
    event: str,
    payload: Any,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return [_malformed_payload_error_result(event, payload)]
    if event == "post_llm_call":
        return _run_post_llm_checks(payload)

    context: dict[str, Any] = {}
    checks: list[Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any] | list[dict[str, Any]]]] = [
        _check_identity,
        _check_mode,
        _check_subagent,
        _check_local_recall,
        _check_context_propagation,
    ]
    results: list[dict[str, Any]] = []
    execution_context_error: dict[str, Any] | None = None
    try:
        raw_context_payload = {
            "agent_id": payload.get("agent_id"),
            "execution_kind": payload.get("execution_kind"),
            "session_id": payload.get("session_id"),
            "parent_session_id": payload.get("parent_session_id"),
            "task_id": payload.get("task_id"),
            "turn_id": payload.get("turn_id"),
            "user_message": payload.get("user_message"),
            "planning_mode": payload.get("planning_mode"),
            "delegation_target": payload.get("delegation_target"),
            "memory_hits": payload.get("memory_hits"),
            "requested_path": payload.get("requested_path"),
            "requested_paths": payload.get("requested_paths"),
        }
        try:
            context = normalize_execution_context(raw_context_payload)
        except ExecutionContextError as exc:
            execution_context_error = _execution_context_error_result(event, payload, exc.missing_fields)
            context = {}
        for check in checks:
            outcome = check(event, payload, context)
            if isinstance(outcome, list):
                results.extend(outcome)
            else:
                results.append(outcome)
        results.extend(_check_security(event, payload, context))
        if execution_context_error is not None:
            results.append(execution_context_error)
    except Exception as exc:
        if execution_context_error is not None and not any(
            result.get("reason_code") == "execution_context_invalid" for result in results
        ):
            results.append(execution_context_error)
        results.append(
            make_result(
                hook="safety-hooks",
                event=event,
                action="error",
                reason_code="safety_check_error",
                risk_level="unknown",
                message=str(exc),
                metadata={"error_type": type(exc).__name__},
            )
        )
    return results


def build_safety_hook_overrides(
    config: dict[str, Any] | None = None,
    memory_provider: Any = None,
) -> dict[str, list[Callable[..., Any]]]:
    def _pre_llm_call(**kwargs: Any) -> list[dict[str, Any]]:
        return run_safety_checks("pre_llm_call", kwargs, config=config)

    def _post_llm_call(**kwargs: Any) -> list[dict[str, Any]]:
        return run_safety_checks("post_llm_call", kwargs, config=config)

    def _on_session_end(**kwargs: Any) -> dict[str, Any]:
        if not isinstance(kwargs, Mapping):
            return _malformed_payload_error_result("on_session_end", kwargs)
        return _run_session_archiver(kwargs)

    return {
        "pre_llm_call": [_pre_llm_call],
        "post_llm_call": [_post_llm_call],
        "on_session_end": [_on_session_end],
    }
