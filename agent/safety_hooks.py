"""Deterministic safety hook primitives for pre-LLM guardrails."""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any, Callable


ALLOWED_ACTIONS = frozenset({"allow", "warn", "block", "error", "skip"})
ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical", "unknown"})
ALLOWED_EXECUTION_KINDS = frozenset({"interactive", "cron", "subagent"})
_MAX_TEXT_CHARS = 200
_MAX_LIST_ITEMS = 20
_MAX_DICT_ITEMS = 20
_MAX_DEPTH = 4
_REDACTED = "[REDACTED]"
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


def _execution_context_error_result(
    event: str,
    payload: dict[str, Any],
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


def run_safety_checks(
    event: str,
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    context: dict[str, Any] = {}
    checks: list[Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any] | list[dict[str, Any]]]] = [
        _check_identity,
        _check_mode,
        _check_subagent,
        _check_local_recall,
        _check_context_propagation,
    ]
    results: list[dict[str, Any]] = []
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
        execution_context_error: dict[str, Any] | None = None
        try:
            context = normalize_execution_context(raw_context_payload)
        except ExecutionContextError as exc:
            execution_context_error = _execution_context_error_result(event, payload, exc.missing_fields)
            context = {}
        for index, check in enumerate(checks):
            outcome = check(event, payload, context)
            if isinstance(outcome, list):
                results.extend(outcome)
            else:
                results.append(outcome)
            if index == 0 and execution_context_error is not None:
                results.append(execution_context_error)
        results.extend(_check_security(event, payload, context))
    except Exception as exc:
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

    return {"pre_llm_call": [_pre_llm_call]}
