"""researchos command line.

`doctor` is the important one: it reports what is actually working, with the
same checks the daemon relies on, so a broken integration is visible before an
overnight run rather than after it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import CONFIG_NAME, Settings
from .kaggle.runner import KaggleError, KaggleRunner
from .providers.base import ClaudeCodeProvider, CodexProvider
from .state.db import Store

OK = "PASS"
BAD = "FAIL"
WARN = "WARN"


def _print_row(name: str, status: str, detail: str = "") -> None:
    print(f"  {name:<16} {status:<5} {detail}")


def _build_providers(settings: Settings):
    available = {
        "codex": lambda: CodexProvider(
            model=settings.providers.codex_model,
            allow_paid=settings.providers.allow_paid_fallback,
        ),
        "claude_code": lambda: ClaudeCodeProvider(
            model=settings.providers.claude_model,
            allow_paid=settings.providers.allow_paid_fallback,
        ),
    }
    return [available[name]() for name in settings.providers.order if name in available]


def cmd_doctor(args) -> int:
    settings = Settings.discover(args.workspace)
    print(f"ResearchOS doctor - workspace {settings.root}\n")
    failures = 0

    print("Providers")
    for provider in _build_providers(settings):
        status = provider.status()
        ok = status == "available"
        failures += 0 if ok else 1
        _print_row(provider.name, OK if ok else BAD, status)

    print("\nKaggle")
    try:
        account = KaggleRunner().whoami()
        _print_row("auth", OK, account)
    except (KaggleError, FileNotFoundError, Exception) as exc:  # noqa: BLE001
        failures += 1
        _print_row("auth", BAD, str(exc)[:80])

    print("\nTelegram")
    token_file = settings.secret_path(settings.telegram.token_file)
    allow_file = settings.secret_path(settings.telegram.allowlist_file)
    if not token_file.exists():
        failures += 1
        _print_row("token", BAD, f"missing {token_file}")
    else:
        _print_row("token", OK, "present (not shown)")
    if not allow_file.exists():
        failures += 1
        _print_row("allowlist", BAD, f"missing {allow_file}")
    else:
        count = len([x for x in allow_file.read_text().split() if x.strip()])
        _print_row("allowlist", OK if count else BAD, f"{count} chat id(s)")

    print("\nState")
    try:
        store = Store(settings.db_path)
        state = store.daemon_state()
        orphans = store.find_orphaned_runs()
        _print_row("database", OK, str(settings.db_path))
        _print_row("daemon", OK, state["status"])
        if orphans:
            _print_row("orphaned runs", WARN,
                       f"{len(orphans)} in-flight run(s) from a previous session")
        store.close()
    except Exception as exc:  # noqa: BLE001
        failures += 1
        _print_row("database", BAD, str(exc)[:80])

    print("\nSafety")
    _print_row("paid fallback", OK if not settings.providers.allow_paid_fallback else WARN,
               "OFF" if not settings.providers.allow_paid_fallback else "ON")

    print()
    if failures:
        print(f"{failures} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


def cmd_status(args) -> int:
    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    state = store.daemon_state()
    print(f"daemon        : {state['status']}")
    print(f"last heartbeat: {state['last_heartbeat'] or 'never'}")
    print(f"active exp    : {state['active_experiment_id'] or '-'}")
    if state["pause_reason"]:
        print(f"pause reason  : {state['pause_reason']}")
    if settings.active_project:
        experiments = store.list_experiments(settings.active_project)
        print(f"experiments   : {len(experiments)}")
        best = store.best_experiment(settings.active_project)
        if best:
            print(f"best          : {best['id']} "
                  f"{best['primary_metric_name']}={best['primary_metric']}")
    store.close()
    return 0


def cmd_providers_test(args) -> int:
    settings = Settings.discover(args.workspace)
    for provider in _build_providers(settings):
        result = provider.run("Reply with exactly: PROVIDER_OK", timeout=300)
        marker = OK if result.ok and "PROVIDER_OK" in result.text else BAD
        _print_row(provider.name, marker, f"{result.outcome} ({result.duration_ms} ms)")
    return 0


def cmd_telegram_test(args) -> int:
    from .telegram.bot import TelegramBot, load_allowlist, load_secret

    settings = Settings.discover(args.workspace)
    bot = TelegramBot(
        load_secret(settings.secret_path(settings.telegram.token_file)),
        load_allowlist(settings.secret_path(settings.telegram.allowlist_file)),
    )
    me = bot.get_me()
    if not me.get("ok"):
        print("Telegram token rejected.")
        return 1
    print(f"bot: @{me['result']['username']}")
    bot.broadcast("ResearchOS telegram test successful.")
    print("test message sent to allowlisted chats")
    return 0


def cmd_kaggle_test(args) -> int:
    runner = KaggleRunner()
    try:
        print(f"account: {runner.whoami()}")
    except Exception as exc:  # noqa: BLE001
        print(f"Kaggle check failed: {exc}")
        return 1
    return 0


def cmd_init(args) -> int:
    root = Path(args.workspace or Path.cwd()).resolve()
    settings = Settings(workspace_root=str(root), active_project=args.project)
    target = root / CONFIG_NAME
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)")
        return 1
    settings.save(target)
    store = Store(settings.db_path)
    if args.project and store.get_project(args.project) is None:
        store.create_project(args.project, args.project, str(root))
    store.close()
    print(f"wrote {target}")
    print(f"state database at {settings.db_path}")
    return 0


def _load_policy(settings: Settings, store: Store):
    """Policy for the active project, or None if none is configured yet."""
    if not settings.active_project:
        return None
    project = store.get_project(settings.active_project)
    if project is None or not project["policy_path"]:
        return None
    path = Path(project["policy_path"])
    if not path.is_absolute():
        path = settings.root / path
    if not path.is_file():
        return None
    from .policy.engine import ResearchPolicy

    return ResearchPolicy.load(path)


def _build_bot(settings: Settings):
    if not settings.telegram.enabled:
        return None
    from .telegram.bot import TelegramBot, load_allowlist, load_secret

    token_file = settings.secret_path(settings.telegram.token_file)
    allow_file = settings.secret_path(settings.telegram.allowlist_file)
    if not token_file.exists() or not allow_file.exists():
        return None
    return TelegramBot(load_secret(token_file), load_allowlist(allow_file))


def cmd_start(args) -> int:
    from .daemon import Daemon
    from .providers.router import ProviderRouter

    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    policy = _load_policy(settings, store)
    if policy is None:
        print("warning: no research policy configured; the loop will report "
              "failures but will not act on them")
    daemon = Daemon(
        settings,
        store,
        bot=_build_bot(settings),
        policy=policy,
        router=ProviderRouter(_build_providers(settings), store=store),
    )
    print(f"daemon starting (pid {__import__('os').getpid()}); Ctrl+C to stop")
    try:
        daemon.run(interval=args.interval)
    except KeyboardInterrupt:
        daemon.stop("interrupted")
    finally:
        store.close()
    return 0


def cmd_stop(args) -> int:
    """Signal a daemon in another process to exit via the shared database."""
    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    store.conn.execute(
        "UPDATE daemon_state SET status = 'stopped', pid = NULL WHERE id = 1"
    )
    store.add_event(kind="daemon.stop_requested", message="stop requested from CLI",
                    level="warn")
    store.close()
    print("stop requested; the daemon exits on its next tick")
    return 0


def cmd_events(args) -> int:
    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    for event in reversed(store.recent_events(args.limit)):
        print(f"{event['created_at']}  {event['level']:<5} {event['kind']:<22} "
              f"{event['message']}")
    store.close()
    return 0


def cmd_export(args) -> int:
    """Dump experiments as JSON, so provenance leaves with the researcher."""
    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    rows = store.list_experiments(args.project or settings.active_project)
    print(json.dumps([dict(r) for r in rows], indent=2))
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="researchos", description=__doc__)
    parser.add_argument("--workspace", help="workspace root (default: discover upwards)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check every integration").set_defaults(func=cmd_doctor)
    sub.add_parser("status", help="daemon and experiment status").set_defaults(func=cmd_status)

    init = sub.add_parser("init", help="create researchos.yaml and the state database")
    init.add_argument("--project", help="project id to create")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    start = sub.add_parser("start", help="run the research daemon in the foreground")
    start.add_argument("--interval", type=int, default=None,
                       help="seconds between polls (default: config)")
    start.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="ask a running daemon to exit").set_defaults(func=cmd_stop)

    events = sub.add_parser("events", help="recent event stream")
    events.add_argument("--limit", type=int, default=30)
    events.set_defaults(func=cmd_events)

    export = sub.add_parser("export", help="export experiments as JSON")
    export.add_argument("--project")
    export.set_defaults(func=cmd_export)

    providers = sub.add_parser("providers", help="provider operations")
    providers_sub = providers.add_subparsers(dest="sub", required=True)
    providers_sub.add_parser("test").set_defaults(func=cmd_providers_test)

    telegram = sub.add_parser("telegram", help="telegram operations")
    telegram_sub = telegram.add_subparsers(dest="sub", required=True)
    telegram_sub.add_parser("test").set_defaults(func=cmd_telegram_test)

    kaggle = sub.add_parser("kaggle", help="kaggle operations")
    kaggle_sub = kaggle.add_subparsers(dest="sub", required=True)
    kaggle_sub.add_parser("test").set_defaults(func=cmd_kaggle_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
