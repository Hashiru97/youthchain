#!/usr/bin/env python3
"""
Runs one job_source's scan directly, bypassing pg_cron and the poller's
claim loop entirely (that Postgres-only pipeline never fires on local
SQLite dev anyway — see the enable_pg_cron_scan_scheduling migration).
This is the escape hatch for local development and manual smoke-testing
once real FIRECRAWL_API_KEY/ANTHROPIC_API_KEY values are configured.

Usage:
    python scripts/run_scan.py --source-id 3
    python scripts/run_scan.py --list-sources
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
from scanner.pipeline import run_scan_for_source  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, help="job_source.id to scan")
    parser.add_argument("--list-sources", action="store_true", help="List configured sources and exit")
    args = parser.parse_args()

    with app_module.app.app_context():
        if args.list_sources:
            sources = app_module.JobSource.query.order_by(app_module.JobSource.id.asc()).all()
            if not sources:
                print("No job sources configured yet — add one via /admin/scanner.")
                return
            for s in sources:
                print(f"[{s.id}] {s.name} — {s.base_url} (active={s.active}, every {s.scan_frequency_minutes}m)")
            return

        if not args.source_id:
            parser.error("--source-id is required unless --list-sources is given")

        outcome = run_scan_for_source(
            source_id=args.source_id,
            db=app_module.db,
            Job=app_module.Job,
            JobSource=app_module.JobSource,
            ScanRun=app_module.ScanRun,
            ScrapedCompany=app_module.ScrapedCompany,
            firecrawl_key=app_module._get_secret("FIRECRAWL_API_KEY"),
            anthropic_key=app_module._get_secret("ANTHROPIC_API_KEY"),
            logger=app_module.logger,
        )
        if outcome.success:
            print(
                f"Scan succeeded: found={outcome.jobs_found} created={outcome.jobs_created} "
                f"updated={outcome.jobs_updated} backfilled={outcome.jobs_backfilled}"
            )
        else:
            print(f"Scan failed: {outcome.error_message}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
