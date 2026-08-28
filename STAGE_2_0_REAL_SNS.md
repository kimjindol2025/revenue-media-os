# Stage 2.0 — Real SNS Provider 1

This stage adds the Reddit provider boundary and connects a provider result to
`signal_observations` and `TrendSensor`. It does not add writing, SERP,
publishing, or automated social actions.

## Provider contract

`RedditTrendsProvider.fetch_trends(country, language, since, until)` reads
`REDDIT_ACCESS_TOKEN` and `REDDIT_USER_AGENT` from the environment. Without an
access token it returns `NOT_CONFIGURED`; there is no fixture fallback. A
successful response is normalized into hourly keyword aggregates and passed to
`TrendSensor.ingest_provider()`.

The normalized observation retains the raw event timestamp, UTC bucket,
provider request fingerprint, capture timestamp, and redacted audit evidence.
Secrets are not included in the stored evidence. Invalid, negative, NaN,
infinite, future, or stale values are rejected or marked non-investable.

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
