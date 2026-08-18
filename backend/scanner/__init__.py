"""
Job scanner: scrapes external Sierra Leone job listing sites (Firecrawl)
and extracts structured job fields from the scraped content (Claude),
populating Job rows with source="scraped". Kept as its own package,
separate from the 7700+-line app.py, and imported there only for route
wiring — every function here takes its dependencies (db, models, API
keys, logger) as explicit parameters rather than importing app.py, so
this package has no circular import and stays independently testable.

See migrations/versions/b6d3e81f5a97_enable_pg_cron_scan_scheduling.py
for the full pg_cron + poller scheduling design (pg_cron owns *when* a
scan is due, poller.py's claim loop owns *how* it actually runs).
"""
