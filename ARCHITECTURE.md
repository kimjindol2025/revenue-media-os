# Revenue Media OS — Stage 1.6 architecture

## Planes

```text
A Trend Sensor
  raw observation -> rolling velocity -> FAST_SIGNAL
        |
        v
Opportunity Engine
  provenance + search gap + competition + history + site fit + freshness + risk + cost
        |
        +-- WATCH / REVIEW_REQUIRED
        +-- FAST_WRITE / MONEY_WRITE / ...
        v
B Editorial -> Site Portfolio Router -> Publisher Adapter
                                      |
                                      v
                                  Publication
                                      |
                                      v
C Telemetry
  Rank | GSC Search | GA4 Analytics | Revenue | Cost
                                      |
                                      v
                           Intelligence / reports
```

D-team control is represented by versioned harness configuration, provider status, audit logs, migration history, reports, and risk gates.

## Important boundaries

### Raw observation

`MISSING` is different from observed zero. `signal_observations` keeps missing numeric values as `NULL`.

`observed_at` is the end of a measurement bucket. Rolling velocity uses stored `OBSERVED` rows in `(cutoff, observed_at]`; the current persisted bucket therefore participates in the calculation.

### FAST_SIGNAL vs FAST_WRITE

`signals.is_fast_candidate` means only that A-team trend velocity/acceleration crossed its harness thresholds. It does not authorize publishing.

`FAST_WRITE` is produced only after Opportunity inputs and risk gates are satisfied.

### REAL vs FIXTURE

Fixture defaults exist only in `mode=FIXTURE`. `mode=REAL` does not invent Search Gap, competition, historical revenue, site fit, freshness, risk, or cost. Each input carries a provider/data status.

### Telemetry

GSC and GA4 are different sources and are stored independently:

- `search_metrics`: query/page/country/device/impressions/clicks/CTR/position.
- `analytics_metrics`: source/medium/country/sessions/users/engagement/page views.

Rank and revenue retain their own provider states as well.

### Site Portfolio

All eligible country/language candidates are scored before selection. Topic/authority fit can beat a higher-RPM unrelated site. Candidate scores, router version, and selection reason can be persisted in `site_selections`.

### Cost attribution

One `cost_metrics` row has at most one scope: Opportunity, Content, or Publication. `content_economics()` combines the scopes once and calculates contribution profit.

## Migration

Schema version is 3. Existing Stage 1/1.5 databases are upgraded with IDs preserved. Rebuilt tables restore required foreign keys, foreign keys are re-enabled after the transaction, and `PRAGMA foreign_key_check` must be empty before the database is accepted.

## Reporting windows

Timezone comes from `system_config.report_timezone` and accepts IANA zone names such as `Asia/Seoul` and `America/New_York`.

- Daily: local calendar day.
- Weekly: rolling 7 days including report date.
- Monthly: rolling 30 days including report date.

## Still not claimed

- Real SNS collection: `NOT_CONFIGURED`
- Real Google/Naver SERP: `NOT_CONFIGURED`
- Real WordPress publishing: `NOT_CONFIGURED`
- Real GSC/GA4/AdSense telemetry: `NOT_CONFIGURED`
- Real advertising revenue loop: not complete
