import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Automatic discovery of potential Instagram stores")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--queries", type=Path, default=Path("config/queries.json"))
    run.add_argument("--query-budget", type=int, default=14)
    run.add_argument("--results-per-query", type=int, default=20)
    run.add_argument("--delay", type=float, default=3)
    run.add_argument("--engines", default="bing,brave,duckduckgo")
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--smoke", action="store_true", help="Discard profile data and print aggregate counts only")
    run.add_argument("--restore-github", action="store_true")
    run.add_argument("--enrich-only", action="store_true")
    run.add_argument("--enrichment-budget", type=int, default=12)
    commands.add_parser("generate-key")
    auth = commands.add_parser("login", help="Open Chromium for manual Instagram login")
    auth.add_argument("--session", type=Path, default=Path(".auth/instagram.json"))
    local = commands.add_parser("local-test", help="Test public profiles locally without writing Google Sheets")
    local.add_argument("--accounts", nargs="+", required=True)
    local.add_argument("--session", type=Path, default=Path(".auth/instagram.json"))
    local.add_argument("--output", type=Path, default=Path("output/local-test.json"))
    local.add_argument("--headless", action="store_true")
    local.add_argument("--chrome", action="store_true", help="Use the open, authorised Chrome session; no new login")
    local.add_argument("--chrome-user-data-dir", type=Path)
    local.add_argument("--cdp-endpoint", help="Local Chrome server address, e.g. http://127.0.0.1:9222")
    cloud = commands.add_parser('cloud-enrich', help='Check existing Sheet accounts through the cloud profile scraper')
    cloud.add_argument('--limit', type=int, default=20)
    cloud.add_argument('--output', type=Path, default=Path('output/cloud-summary.json'))
    cloud.add_argument('--force', action='store_true', help='Ignore the daily guard for an explicit manual test')
    cloud.add_argument('--max-charge', type=float, default=0.10)
    export = commands.add_parser("export")
    export.add_argument("archive", type=Path)
    export.add_argument("--output", type=Path, default=Path("output/export"))
    args = parser.parse_args()
    if args.command == "login":
        from .local_test import login
        return login(args.session)
    if args.command == "local-test":
        from .local_test import local_test
        try:
            return local_test(args.accounts, args.session, args.output, args.headless, args.chrome, args.chrome_user_data_dir, args.cdp_endpoint)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command == 'cloud-enrich':
        from .cloud import cloud_enrich
        from .apify_profiles import ApifyError
        try:
            return cloud_enrich(args.limit, args.output, args.force, args.max_charge)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ApifyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    from cryptography.fernet import Fernet
    if args.command == "generate-key":
        print(Fernet.generate_key().decode())
        return 0
    key = os.environ.get("RESULTS_ENCRYPTION_KEY", "").strip()
    from .storage import empty_state, export_state, read_state, write_state
    if args.command == "export":
        if not key:
            parser.error("Set RESULTS_ENCRYPTION_KEY to decrypt results")
        export_state(args.archive, key, args.output)
        print("Decrypted state.json and leads.csv into the local output directory")
        return 0
    from .github_state import restore
    from .queries import load_queries
    from .runner import discover, write_summary
    from .search import SearchClient
    from .sheets import SheetsStore, configured_sheet
    from .enrichment import enrich
    if not 1 <= args.query_budget <= 100 or not 1 <= args.results_per_query <= 100 or args.delay < 0:
        parser.error("Query and result budgets must be 1..100; delay must not be negative")
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    if not engines or any(e not in {"bing", "brave", "duckduckgo", "google", "yahoo", "yandex", "mojeek"} for e in engines):
        parser.error("Unsupported search engine")
    archive = args.output / "leads.fernet"
    sheet_store = None
    if not args.smoke:
        credentials = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip() or configured_sheet()
        if credentials and sheet_id:
            sheet_store = SheetsStore(credentials, sheet_id)
            state = sheet_store.load()
        elif key:
            Fernet(key.encode())
            if args.restore_github:
                restore(archive)
            state = read_state(archive, key) if archive.exists() else empty_state()
        else:
            parser.error("Configure GOOGLE_SERVICE_ACCOUNT_JSON and the destination sheet, or use --smoke")
    else:
        state = empty_state()
    if not 1 <= args.enrichment_budget <= 100:
        parser.error("Enrichment budget must be 1..100")
    client = SearchClient(engines)
    stats = {} if args.enrich_only else discover(state, load_queries(args.queries), client,
                     args.query_budget, args.results_per_query, args.delay, max_seconds=300)
    stats["enrichment"] = enrich(state, client, min(args.enrichment_budget, 2) if args.smoke else args.enrichment_budget)
    if not args.smoke and key:
        write_state(archive, state, key)
    if sheet_store:
        stats["sheets"] = sheet_store.save(state)
    summary = write_summary(args.output / "summary.json", stats, state, not args.smoke)
    print(json.dumps(summary, ensure_ascii=False))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("\n### Store discovery\n\n```json\n" + json.dumps(summary, indent=2) + "\n```\n")
    if args.enrich_only:
        return 0
    if not stats["queries_succeeded"]:
        print("Search backends unavailable. No successful queries; database retained.", file=sys.stderr)
        return 2
    if not stats["profile_results"]:
        print("No profile URLs returned. Review queries/backends; posts are not profiles.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Do not expose raw remote errors in a public workflow.
        print("Run failed (" + type(exc).__name__ + "). Check configuration/state; prior archive was not reset.", file=sys.stderr)
        raise SystemExit(1)
