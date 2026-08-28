PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS keywords (
  id INTEGER PRIMARY KEY, keyword TEXT NOT NULL UNIQUE, country TEXT NOT NULL,
  language TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id),
  source TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL,
  mention_count INTEGER NOT NULL, unique_authors INTEGER NOT NULL,
  engagement INTEGER NOT NULL, velocity_1h REAL NOT NULL, velocity_3h REAL NOT NULL,
  velocity_6h REAL NOT NULL, velocity_12h REAL NOT NULL, velocity_24h REAL NOT NULL,
  acceleration REAL NOT NULL, platform_count INTEGER NOT NULL DEFAULT 1,
  country_count INTEGER NOT NULL DEFAULT 1, first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL, is_fast_candidate INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id),
  signal_id INTEGER REFERENCES signals(id), decision TEXT NOT NULL,
  score REAL NOT NULL, decision_reason TEXT NOT NULL, score_components TEXT NOT NULL,
  engine_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS serp_snapshots (
  id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  engine TEXT NOT NULL, query TEXT NOT NULL, captured_at TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS serp_results (
  id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL REFERENCES serp_snapshots(id),
  position INTEGER NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, snippet TEXT NOT NULL,
  features TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, country TEXT NOT NULL,
  language TEXT NOT NULL, topic TEXT NOT NULL, platform TEXT NOT NULL,
  authority_tags TEXT NOT NULL, publisher_type TEXT NOT NULL, ads_type TEXT,
  ads_account_ref TEXT, search_console_ref TEXT, analytics_ref TEXT,
  average_rpm REAL NOT NULL DEFAULT 0, average_revenue REAL NOT NULL DEFAULT 0,
  health_status TEXT NOT NULL DEFAULT 'UNKNOWN', policy_status TEXT NOT NULL DEFAULT 'UNKNOWN'
);
CREATE TABLE IF NOT EXISTS content_plans (
  id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  site_id INTEGER REFERENCES sites(id), content_type TEXT NOT NULL, search_intent TEXT NOT NULL,
  content_gaps TEXT NOT NULL, outline TEXT NOT NULL, context TEXT NOT NULL,
  harness_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contents (
  id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES content_plans(id),
  title TEXT NOT NULL, body TEXT NOT NULL, content_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY, content_id INTEGER NOT NULL REFERENCES contents(id),
  site_id INTEGER NOT NULL REFERENCES sites(id), platform TEXT NOT NULL, external_id TEXT,
  url TEXT, status TEXT NOT NULL, published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rank_history (
  id INTEGER PRIMARY KEY, publication_id INTEGER NOT NULL REFERENCES publications(id),
  keyword_id INTEGER NOT NULL REFERENCES keywords(id), rank INTEGER, captured_at TEXT NOT NULL,
  provider_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traffic_metrics (
  id INTEGER PRIMARY KEY, publication_id INTEGER NOT NULL REFERENCES publications(id),
  captured_at TEXT NOT NULL, impression INTEGER, click INTEGER, ctr REAL,
  google_traffic INTEGER, naver_traffic INTEGER, sns_traffic INTEGER, direct_traffic INTEGER,
  engagement_time REAL, page_views INTEGER, provider_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revenue_metrics (
  id INTEGER PRIMARY KEY, publication_id INTEGER NOT NULL REFERENCES publications(id),
  captured_at TEXT NOT NULL, adsense_revenue REAL, adpost_revenue REAL, rpm REAL,
  provider_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, canary_percent REAL NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harness_versions (
  id INTEGER PRIMARY KEY, component TEXT NOT NULL, version TEXT NOT NULL, config TEXT NOT NULL,
  created_at TEXT NOT NULL, UNIQUE(component, version)
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY, action TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id INTEGER, status TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_last_seen ON signals(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_rank_pub_time ON rank_history(publication_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_revenue_pub_time ON revenue_metrics(publication_id, captured_at);
