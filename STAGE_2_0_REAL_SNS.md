# Stage 2.0 — Real SNS Provider 1

This stage adds the Reddit provider boundary and connects a provider result to
`signal_observations` and `TrendSensor`. It does not add writing, SERP,
publishing, or automated social actions.

## Provider contract

`RedditTrendsProvider.fetch_trends(country, language, since, until)` reads
`REDDIT_ACCESS_TOKEN` and a required, operator-supplied unique
`REDDIT_USER_AGENT` from the environment. Without either credential it returns
`NOT_CONFIGURED`; there is no fixture fallback. Reddit listing data does not
carry country or language attribution, so this adapter accepts only
`GLOBAL/und` and returns `PARTIAL` for a narrower requested market. A
successful response is normalized into hourly keyword aggregates and passed to
`TrendSensor.ingest_provider()`.

The normalized observation retains the raw event timestamp, UTC bucket,
provider request fingerprint, unique fetch evidence id, capture timestamp, and
redacted audit evidence.
Secrets are not included in the stored evidence. Pagination is followed until
the listing ends; if the configured page limit is reached, the result is
`PARTIAL` and is not persisted as complete data. Re-fetching the same provider
keyword/hour aggregate updates that aggregate rather than freezing its first
count. Invalid, negative, NaN, infinite, future, or stale values are rejected
or marked non-investable, and provider batches are validated before any row is
written. Persisting a validated batch is one database transaction; a storage
failure rolls the entire batch back. Pagination stops once the oldest fetched
post reaches the requested `since` boundary.

## Status meanings

`NOT_CONFIGURED`, `CONFIGURED_NO_DATA`, `PASS`, `PARTIAL`, and `FAIL` are kept
separate. This checkout has not been given a Reddit credential, so no live
provider call was made and the Stage 2.0 live status remains:

```text
LIVE_PROVIDER=NOT_CONFIGURED
STATUS=PARTIAL
```

To perform a live smoke test, configure the environment outside the repository
and run the provider integration through the application boundary. Never put a
token in source, SQLite data, audit logs, or test fixtures.
