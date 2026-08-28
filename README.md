# Revenue Media OS — Stage 1.6

An isolated, SQLite-backed implementation of the closed loop:

`Signal → Opportunity → Content → Publication → Performance → Revenue → Learning`

Stage 1.6 hardens the data boundary before real providers are connected. It does **not** claim real SNS, SERP, ranking, traffic, or advertising revenue yet.

## What Stage 1.6 guarantees

- `UNIQUE(keyword, country, language)` identity.
- Raw `signal_observations` preserve `NULL` for missing metrics; missing data is not rewritten as zero.
- Real rolling velocity is calculated from persisted observations, including the current bucket.
- `FAST_SIGNAL` (A-team trend signal) is separate from `FAST_WRITE` (Opportunity investment decision).
- REAL Opportunity decisions require explicit data provenance/status. Missing risk becomes `REVIEW_REQUIRED`; other missing required inputs block writing with `WATCH`.
- High-risk classes (`INCIDENT`, `FINANCE`, `HEALTH`, `LEGAL`, `POLITICS`, `PERSON_RUMOR`) require review.
- GSC/search telemetry and GA4/analytics telemetry are stored in separate tables.
- Site Portfolio routing scores every eligible site before selecting a winner and persists candidate scores/reason when an opportunity is supplied.
- Cost rows have one attribution scope only (Opportunity, Content, or Publication), supporting contribution-profit calculation without double counting.
- Schema version 3 includes migration checks and `PRAGMA foreign_key_check` after migration.
- Daily/weekly/monthly reports use a configured IANA timezone. Weekly and monthly reports are rolling 7-day and rolling 30-day windows.
- Provider status reporting is calculated from persisted provider rows, not hard-coded.
- Scheduler/signal/publication/telemetry/cost boundaries support idempotency.

## Provider boundary

Provider interfaces exist for RSS, OpenSERP, WordPress, and authenticated Google APIs. Missing credentials/endpoints return `NOT_CONFIGURED`; they are never represented as `PASS`.

Real provider integration is the next stage.

## Run

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m revenue_media_os.cli demo --db data/mvp.sqlite3
PYTHONPATH=src python3 -m revenue_media_os.cli daily-report --db data/mvp.sqlite3
PYTHONPATH=src python3 -m revenue_media_os.cli period-report weekly --db data/mvp.sqlite3
```

The demo uses fixture signals, recorded SERP data, and a local publisher. It proves orchestration only, not real ranking or revenue.
