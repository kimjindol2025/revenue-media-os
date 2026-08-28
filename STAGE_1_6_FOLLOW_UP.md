# Stage 1.6 Follow-up Boundaries

This follow-up hardens the pre-provider integration boundary. It does not
connect live SNS, SERP, publisher, Search Console, Analytics, or advertising
accounts.

## Observation semantics

Provider timestamps are retained as `observed_at` and normalized to an
explicit UTC `bucket_start` and `bucket_end`. Multiple observations in one
hour are preserved and summed for that hour. Trend velocity is the average of
the most recent 1, 3, 6, 12, or 24 complete hour buckets; a one-hour value is
never allowed to include two adjacent buckets. Missing measurements remain
`NULL` and are not treated as observed zeroes.

## Decision provenance and safety

Fixture decisions are marked `FIXTURE`. Real opportunity inputs must carry a
value, status, source, and capture timestamp. Missing real inputs produce a
non-investment decision, and an unknown or high-risk classification produces
`REVIEW_REQUIRED`. `FAST_SIGNAL` is a trend state; `FAST_WRITE` is a separate
opportunity decision subject to search, risk, and site-fit checks.

## Migration and reporting

Schema version 3 adds normalized observation buckets and provenance while
preserving raw observations and existing IDs. The migration is regression
tested from the fixed Stage 1 fixture and validates foreign keys after the
migration. Search Console and Analytics telemetry have separate tables and
statuses. Reports use the configured timezone and real daily/weekly/calendar
window boundaries; monthly reporting currently uses a rolling 30-day window.

## Provider status

Live provider accounts are not configured in this stage. Their status is
reported as `NOT_CONFIGURED`, `CONFIGURED_NO_DATA`, `PARTIAL`, `PASS`, or
`FAIL` based on persisted provider rows rather than a hard-coded report value.
