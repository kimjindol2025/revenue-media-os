# Revenue Media OS MVP architecture

## Runtime boundary

The project is an isolated Python process with SQLite as its intelligence DB.
It does not restart or mutate any service outside its own directory.

```text
TrendSensor -> OpportunityEngine -> Editorial -> Site Registry
                                      |             |
                                      v             v
                                 Content       Publisher Adapter
                                                    |
                                                    v
                                              Publication
                                                    |
                                      Telemetry -> rank/traffic/revenue
                                                    |
                                                    v
                                      Editorial.history / Daily report
```

Every arrow is represented by foreign keys in `schema.sql`. Provider-specific
implementations can be added behind the same interfaces; absent credentials or
provider responses remain `NOT_CONFIGURED`.

Stage 1.6 adds `signal_observations` as the source for real velocity windows,
separate `search_metrics` (GSC) and `analytics_metrics` (GA4) tables, and
`schema_version`/`migration_history` for safe upgrades. UTC timestamps are
converted to `system_config.report_timezone` only at report boundaries.

`PublisherRouter` is canonical in `providers.py`; `core.py` imports that class
for backwards-compatible access. It scores all eligible sites before choosing
one, and stores the candidate scores and selection reason in the content plan.

## Safety and rollout

`harness_versions` and `experiments` are schema primitives for canary/rollback
metadata. The MVP does not automatically distribute content across sites,
post to social platforms, click ads, bypass CAPTCHAs, or copy SERP text.

## Provider plan

The local SERP fixture and local publisher prove orchestration only. Real
Search Console, GA4, AdSense, SERP, SNS, and WordPress/Blogger adapters are
not configured in this environment and therefore are not claimed as complete.
