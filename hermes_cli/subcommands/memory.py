"""``hermes memory`` subcommand parser.

Extracted from ``hermes_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


def build_memory_parser(subparsers, *, cmd_memory: Callable) -> None:
    """Attach the ``memory`` subcommand to ``subparsers``."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Available providers: honcho, openviking, mem0, hindsight,\n"
            "holographic, retaindb, byterover.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration"
    )
    _setup_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider to configure directly (e.g. honcho), skipping the picker",
    )
    memory_sub.add_parser("status", help="Show current memory provider config")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset",
        help="Erase all built-in memory (MEMORY.md and USER.md)",
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'",
    )

    versions_parser = memory_sub.add_parser("versions", help="List or restore built-in memory versions")
    versions_sub = versions_parser.add_subparsers(dest="versions_command", required=True)
    versions_list = versions_sub.add_parser("list", help="List local memory snapshots")
    versions_list.add_argument("--target", choices=["memory", "user"], default=None)
    versions_list.add_argument("--limit", type=int, default=20)
    versions_rollback = versions_sub.add_parser("rollback", help="Restore a local memory snapshot")
    versions_rollback.add_argument("version_id")
    versions_rollback.add_argument("--target", choices=["memory", "user"], required=True)
    versions_rollback.add_argument("--yes", action="store_true", help="Confirm the restore")

    step_parser = memory_sub.add_parser("step", help="Record repeated failed memory-write patterns")
    step_sub = step_parser.add_subparsers(dest="step_command", required=True)
    step_record = step_sub.add_parser("record", help="Record one failed pattern")
    step_record.add_argument("pattern")
    step_record.add_argument("--note", default=None)
    step_list = step_sub.add_parser("list", help="List recorded failed patterns")
    step_list.add_argument("--limit", type=int, default=20)

    meta_parser = memory_sub.add_parser("meta", help="Record memory strategy outcomes")
    meta_sub = meta_parser.add_subparsers(dest="meta_command", required=True)
    meta_log = meta_sub.add_parser("log", help="Log one strategy outcome")
    meta_log.add_argument("strategy")
    meta_log.add_argument("--result", choices=["success", "failure"], required=True)
    meta_log.add_argument("--note", default=None)
    meta_list = meta_sub.add_parser("list", help="List strategy outcomes")
    meta_list.add_argument("--limit", type=int, default=20)
    memory_parser.set_defaults(func=cmd_memory)


def handle_governance_command(args) -> None:
    """Handle local built-in memory governance commands."""
    from tools import memory_governance
    from tools.memory_tool import get_memory_dir

    memory_dir = get_memory_dir()
    governance_dir = memory_dir / "governance"
    versions_dir = memory_dir / "l2" / "versions"
    sub = getattr(args, "memory_command", None)

    if sub == "versions":
        action = getattr(args, "versions_command", None)
        if action == "list":
            result = memory_governance.list_snapshots(
                versions_dir, target=getattr(args, "target", None), limit=args.limit
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if not args.yes:
            print(f"Rollback {args.version_id} requires --yes; no files changed.")
            return
        target_path = memory_dir / ("USER.md" if args.target == "user" else "MEMORY.md")
        result = memory_governance.rollback(
            args.version_id,
            target_path=target_path,
            versions_dir=versions_dir,
            target=args.target,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if sub == "step":
        if args.step_command == "record":
            result = memory_governance.record_step(governance_dir, args.pattern, args.note)
        else:
            result = memory_governance.list_steps(governance_dir, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if sub == "meta":
        if args.meta_command == "log":
            result = memory_governance.record_meta(governance_dir, args.strategy, args.result, args.note)
        else:
            result = memory_governance.list_meta(governance_dir, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
