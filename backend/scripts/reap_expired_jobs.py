#!/usr/bin/env python3
"""
Runs scanner.reaper.reap_expired_jobs() directly, bypassing the
poller's own hourly timer — the escape hatch for local development and
manual triggering (e.g. right after lowering REAP_GRACE_DAYS for a
one-off cleanup), same convention as scripts/run_scan.py for scans.

Usage:
    python scripts/reap_expired_jobs.py
    python scripts/reap_expired_jobs.py --grace-days 3
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
from scanner.reaper import REAP_GRACE_DAYS, reap_expired_jobs  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grace-days", type=int, default=REAP_GRACE_DAYS,
        help=f"Delete scraped jobs this many days past their own deadline (default {REAP_GRACE_DAYS})",
    )
    args = parser.parse_args()

    with app_module.app.app_context():
        deleted = reap_expired_jobs(
            db=app_module.db,
            Job=app_module.Job,
            SavedJob=app_module.SavedJob,
            ScrapedListingReport=app_module.ScrapedListingReport,
            grace_days=args.grace_days,
        )
        print(f"Reaped {deleted} expired listing(s).")


if __name__ == "__main__":
    main()
