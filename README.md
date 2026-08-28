# Revenue Media OS — MVP

An isolated, SQLite-backed implementation of the closed loop:

`Signal → Opportunity → Content → Publication → Performance → Revenue → Learning`

This MVP uses only Python's standard library. It stores every relationship in
SQLite and exposes provider interfaces for real SNS, SERP, publisher, GA4,
Search Console, and AdSense integrations. Providers that are not configured
are reported as `NOT_CONFIGURED`; they are never represented as `PASS`.

## Run

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m revenue_media_os.cli demo --db data/mvp.sqlite3
PYTHONPATH=src python3 -m revenue_media_os.cli daily-report --db data/mvp.sqlite3
```

The demo uses a local publisher and a recorded SERP fixture. It is an
integration smoke test, not evidence of real search ranking or ad revenue.

## Scope

- SQLite intelligence DB with foreign-key links across the complete loop.
- Configurable opportunity scoring and versioned harness metadata.
- Trend velocity, acceleration, cross-platform/country spread, novelty, and
  `FAST_CANDIDATE` detection.
- SERP analysis, content planning, article generation, site fit selection.
- Safe local publisher adapter and publisher interface for WordPress/Blogger.
- Telemetry persistence with explicit provider status.
- Daily report and audit log.
