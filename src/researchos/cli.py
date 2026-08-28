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


def _build_sync(settings: Settings):
    """Coordination sync, or None when the project is not linked to Supabase."""
    if not settings.coordination.enabled:
        return None
    from .coordination.supabase_sync import SupabaseConfig, SupabaseSync

    secrets_dir = settings.root / settings.coordination.secrets_dir
    required = ["supabase_project_ref", "supabase_service_role_key"]
    if not all((secrets_dir / name).exists() for name in required):
        return None
    return SupabaseSync(
        SupabaseConfig.from_secrets(secrets_dir, settings.coordination.runner_id)
    )


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
        sync=_build_sync(settings),
    )
    api_server = None
    if not args.no_api:
        import threading

        from .api import ensure_token, serve

        api_server = serve(settings, port=args.api_port)
        threading.Thread(target=api_server.serve_forever, daemon=True).start()
        ensure_token(settings)
        print(f"local API on http://127.0.0.1:{args.api_port} (loopback only)")

    print(f"daemon starting (pid {__import__('os').getpid()}); Ctrl+C to stop")
    try:
        daemon.run(interval=args.interval)
    except KeyboardInterrupt:
        daemon.stop("interrupted")
    finally:
        if api_server is not None:
            api_server.shutdown()
        store.close()
    return 0


def cmd_token(args) -> int:
    """Print the local API token, for pasting into the dashboard."""
    from .api import ensure_token

    print(ensure_token(Settings.discover(args.workspace)))
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


def cmd_bundle(args) -> int:
    """Package an experiment for manual execution on someone else's account."""
    import json as _json

    from .kaggle.bundle import RunManifest, create_bundle

    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    exp = store.get_experiment(args.experiment)
    if exp is None:
        print(f"unknown experiment: {args.experiment}")
        store.close()
        return 1

    manifest = RunManifest(
        experiment_id=exp["id"],
        project_id=exp["project_id"],
        git_sha=exp["git_sha"],
        methodology_version=exp["methodology_version"],
        config_hash=exp["config_hash"],
        dataset=exp["dataset"],
        dataset_version=exp["dataset_version"],
        accelerator=args.accelerator,
        internet=settings.kaggle.enable_internet,
        seeds=_json.loads(exp["seeds"]) if exp["seeds"] else [],
        primary_metric=exp["primary_metric_name"],
        estimated_runtime=args.runtime,
    )
    dest = Path(args.output or settings.root / f"{exp['id']}-run-bundle.zip")
    create_bundle(dest, manifest, Path(args.code))
    store.add_event(kind="bundle.created", message=str(dest), experiment_id=exp["id"])
    store.close()
    print(f"wrote {dest}")
    return 0


def cmd_import(args) -> int:
    """Ingest results from an externally executed run."""
    from .kaggle.bundle import BundleError, import_results

    settings = Settings.discover(args.workspace)
    store = Store(settings.db_path)
    try:
        result = import_results(
            Path(args.archive),
            expected_experiment_id=args.experiment,
            expected_config_hash=args.config_hash,
        )
    except BundleError as exc:
        print(f"import rejected: {exc}")
        store.close()
        return 1

    exp = store.get_experiment(result.experiment_id)
    if exp is None:
        print(f"unknown experiment: {result.experiment_id}")
        store.close()
        return 1

    run_id = store.create_run(exp["id"], backend="external_manual")
    for name, value in result.metrics.items():
        store.record_metric(exp["id"], name, value, run_id=run_id)

    policy = _load_policy(settings, store)
    if policy and policy.primary_metric_name in result.metrics:
        store.set_primary_metric(
            exp["id"], policy.primary_metric_name,
            result.metrics[policy.primary_metric_name],
            policy.primary_metric_direction,
        )

    if exp["status"] == "running":
        store.transition_experiment(exp["id"], "imported")
        store.transition_experiment(exp["id"], "completed")

    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"imported {len(result.metrics)} metric(s) into {exp['id']}")
    store.close()
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
    start.add_argument("--api-port", type=int, default=8765)
    start.add_argument("--no-api", action="store_true", help="do not serve the local API")
    start.set_defaults(func=cmd_start)

    sub.add_parser("token", help="print the local API token").set_defaults(func=cmd_token)

    sub.add_parser("stop", help="ask a running daemon to exit").set_defaults(func=cmd_stop)

    bundle = sub.add_parser("bundle", help="package an experiment for external execution")
    bundle.add_argument("experiment")
    bundle.add_argument("--code", required=True, help="notebook or script to bundle")
    bundle.add_argument("--output")
    bundle.add_argument("--accelerator", default="GPU")
    bundle.add_argument("--runtime", default="unknown", help="estimated runtime")
    bundle.set_defaults(func=cmd_bundle)

    imp = sub.add_parser("import", help="import results from an external run")
    imp.add_argument("archive")
    imp.add_argument("--experiment", help="experiment id the bundle must match")
    imp.add_argument("--config-hash", dest="config_hash")
    imp.set_defaults(func=cmd_import)

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
