# OSS candidate review

Checked 2026-08-28 before importing any code. No external project code is
copied into this MVP; only provider boundaries are implemented.

| Candidate | Repository / license evidence | MVP decision |
|---|---|---|
| OpenClaw | [official docs](https://docs.openclaw.ai/start/why-openclaw) state MIT | Do not embed; evaluate as an optional runtime |
| Browser Use | [repository](https://github.com/browser-use/browser-use) | Evaluate as an optional browser provider; review dependency licenses first |
| OpenSERP | [karust/openserp](https://github.com/karust/openserp), MIT shown by repository | Prefer its API boundary; do not copy implementation |
| SerpTrail | [serpapi/serptrail](https://github.com/serpapi/serptrail), MIT shown by repository | Evaluate as an optional rank-history provider |

License labels above are source-reported and are not a substitute for a
release-time dependency/license scan. The system must retain copyright notices
and review transitive dependencies before distribution.
