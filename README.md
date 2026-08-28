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
PYTHONPATH=src python3 -m revenue_media_os.cli period-report weekly --db data/mvp.sqlite3
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
- Dependency-free RSS, OpenSERP, WordPress REST, and Google authenticated API
  adapter boundaries with explicit provider statuses.
- Telemetry persistence with explicit provider status.
- Daily report and audit log.

## Stage 1.6 hardening

- Schema version 2 with explicit migration history and foreign-key checks.
- Raw observations preserve `NULL` for missing values; fixture data is marked
  `FIXTURE` and real observations are marked `OBSERVED`.
- Real velocity is calculated after inserting the observation, with inclusive
  lower time-window boundaries.
- Opportunity inputs carry status/provenance; missing real inputs cannot become
  `FAST_WRITE` or `MONEY_WRITE`, and unknown risk becomes `REVIEW_REQUIRED`.
- FAST trend signal and final FAST investment decision are separate concepts.
- GSC and GA4 payloads use separate `search_metrics` and `analytics_metrics`
  tables. Reports derive provider status from stored rows.
- Site Fit evaluates every eligible candidate and records component scores,
  candidate scores, reason, and router version before selection.
- Cost rows may be linked to opportunity, content, and publication, but
  `content_economics()` counts each row once. Monthly reports use a documented
  rolling 30-day window; all report dates are rendered in configured timezone.
- Idempotency keys cover observations, signals, opportunities, publications,
  telemetry, and costs.

The GitHub Actions matrix runs the same unit tests and compile/static checks on
Python 3.11 and 3.12. Real SNS, SERP, rank, traffic, revenue, and publisher
accounts remain intentionally unconfigured in this stage.
