import json
import math
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .providers import PublisherRouter

SCHEMA_VERSION = 4
UTC = timezone.utc


def now():
    return datetime.now(UTC).isoformat()


def _bucket_bounds(observed_at):
    moment = datetime.fromisoformat(observed_at)
    start = moment.replace(minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(hours=1)).isoformat()


def _provenance_value_matches(expected, actual):
    try:
        return math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return expected == actual


def _valid_captured_at(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


class IntelligenceDB:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        schema = Path(__file__).resolve().parents[2] / "schema.sql"
        self.conn.executescript(schema.read_text())
        self._migrate()

    def _columns(self, table):
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _add_column(self, table, name, definition):
        if name not in self._columns(table):
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate(self):
        """Apply additive migrations and rebuild the pre-Stage-1.5 keyword key.

        schema.sql creates the current shape for new databases. Existing MVP
        databases are upgraded here, rather than by blindly re-running SQL.
        IDs are preserved while the old UNIQUE(keyword) table is rebuilt.
        """
        version = self.conn.execute("SELECT coalesce(max(version),0) FROM schema_version").fetchone()[0]
        if version < 2:
            for table, columns in {
                "signals": [("status", "TEXT NOT NULL DEFAULT 'FIXTURE'"), ("observation_id", "INTEGER"), ("idempotency_key", "TEXT"), ("trend_state", "TEXT NOT NULL DEFAULT 'NORMAL'")],
                "opportunities": [("idempotency_key", "TEXT"), ("decision_mode", "TEXT NOT NULL DEFAULT 'FIXTURE'"), ("input_statuses", "TEXT NOT NULL DEFAULT '{}'"), ("risk_class", "TEXT NOT NULL DEFAULT 'unknown'"), ("risk_score", "REAL"), ("risk_reason", "TEXT NOT NULL DEFAULT ''")],
                "content_plans": [("site_fit_score", "REAL"), ("site_fit_reason", "TEXT")],
                "publications": [("idempotency_key", "TEXT")],
                "rank_history": [("checkpoint", "TEXT NOT NULL DEFAULT 'custom'"), ("idempotency_key", "TEXT")],
                "traffic_metrics": [("checkpoint", "TEXT NOT NULL DEFAULT 'custom'"), ("idempotency_key", "TEXT")],
                "revenue_metrics": [("checkpoint", "TEXT NOT NULL DEFAULT 'custom'"), ("idempotency_key", "TEXT")],
                "cost_metrics": [("opportunity_id", "INTEGER"), ("content_id", "INTEGER"), ("publication_id", "INTEGER"), ("idempotency_key", "TEXT")],
            }.items():
                for name, definition in columns:
                    self._add_column(table, name, definition)
            signal_info = {r[1]: r for r in self.conn.execute("PRAGMA table_info(signals)")}
            if signal_info.get("mention_count", (None, None, None, 0))[3] or signal_info.get("unique_authors", (None, None, None, 0))[3] or signal_info.get("engagement", (None, None, None, 0))[3]:
                self.conn.execute("PRAGMA foreign_keys = OFF")
                self.conn.execute("CREATE TABLE signals_migrated (id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id), source TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, mention_count INTEGER, unique_authors INTEGER, engagement INTEGER, velocity_1h REAL NOT NULL, velocity_3h REAL NOT NULL, velocity_6h REAL NOT NULL, velocity_12h REAL NOT NULL, velocity_24h REAL NOT NULL, acceleration REAL NOT NULL, platform_count INTEGER NOT NULL DEFAULT 1, country_count INTEGER NOT NULL DEFAULT 1, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'FIXTURE', is_fast_candidate INTEGER NOT NULL DEFAULT 0, observation_id INTEGER REFERENCES signal_observations(id), idempotency_key TEXT UNIQUE, trend_state TEXT NOT NULL DEFAULT 'NORMAL')")
                self.conn.execute("INSERT INTO signals_migrated SELECT id,keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key,'NORMAL' FROM signals")
                self.conn.execute("DROP TABLE signals"); self.conn.execute("ALTER TABLE signals_migrated RENAME TO signals"); self.conn.execute("PRAGMA foreign_keys = ON")
            cost_targets = {r[2] for r in self.conn.execute("PRAGMA foreign_key_list(cost_metrics)")}
            if not {"opportunities", "contents", "publications"}.issubset(cost_targets):
                self.conn.commit(); self.conn.execute("PRAGMA foreign_keys = OFF")
                self.conn.execute("CREATE TABLE cost_metrics_migrated (id INTEGER PRIMARY KEY, component TEXT NOT NULL, provider TEXT NOT NULL, opportunity_id INTEGER REFERENCES opportunities(id), content_id INTEGER REFERENCES contents(id), publication_id INTEGER REFERENCES publications(id), input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD', captured_at TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT UNIQUE)")
                self.conn.execute("INSERT INTO cost_metrics_migrated SELECT id,component,provider,opportunity_id,content_id,publication_id,input_tokens,output_tokens,amount,currency,captured_at,status,idempotency_key FROM cost_metrics")
                self.conn.execute("DROP TABLE cost_metrics"); self.conn.execute("ALTER TABLE cost_metrics_migrated RENAME TO cost_metrics"); self.conn.commit(); self.conn.execute("PRAGMA foreign_keys = ON")
            for table, column in (("signals", "idempotency_key"), ("opportunities", "idempotency_key"), ("publications", "idempotency_key"), ("rank_history", "idempotency_key"), ("traffic_metrics", "idempotency_key"), ("revenue_metrics", "idempotency_key"), ("cost_metrics", "idempotency_key")):
                self.conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_{column} ON {table}({column}) WHERE {column} IS NOT NULL")
            self.conn.commit()
            self.conn.execute("PRAGMA foreign_keys = ON")
            if self.conn.execute("PRAGMA foreign_key_check").fetchall(): raise sqlite3.IntegrityError("foreign_key_check failed after migration")
            # The old MVP had UNIQUE(keyword). Rebuild only when that exact
            # unique index is present; current databases take no path here.
            old_key = False
            for idx in self.conn.execute("PRAGMA index_list(keywords)"):
                if idx[2]:
                    cols = [r[2] for r in self.conn.execute(f"PRAGMA index_info({idx[1]})")]
                    old_key = cols == ["keyword"]
            if old_key:
                self.conn.commit()
                self.conn.execute("PRAGMA foreign_keys = OFF")
                self.conn.execute("CREATE TABLE keywords_migrated (id INTEGER PRIMARY KEY, keyword TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(keyword,country,language))")
                self.conn.execute("INSERT INTO keywords_migrated SELECT id,keyword,country,language,created_at FROM keywords")
                self.conn.execute("DROP TABLE keywords")
                self.conn.execute("ALTER TABLE keywords_migrated RENAME TO keywords")
                self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)", (2, now()))
            self.conn.execute("INSERT INTO migration_history(from_version,to_version,migration,applied_at) VALUES(?,?,?,?)", (version, 2, "stage_1_5_boundaries", now()))
            self.conn.commit()
        elif version == 0:
            self.conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)", (SCHEMA_VERSION, now()))
            self.conn.execute("INSERT INTO migration_history(from_version,to_version,migration,applied_at) VALUES(?,?,?,?)", (0, SCHEMA_VERSION, "initial_schema", now()))
            self.conn.commit()

        if version < 3:
            # Stage 1.6 Follow-up: raw observations are retained, while their
            # normalized hour bucket becomes an explicit persisted boundary.
            self.conn.commit(); self.conn.execute("PRAGMA foreign_keys = OFF")
            obs_cols = self._columns("signal_observations")
            if "bucket_start" not in obs_cols:
                self.conn.execute("CREATE TABLE signal_observations_v3 (id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id), source TEXT NOT NULL, mention_count INTEGER, unique_authors INTEGER, engagement INTEGER, observed_at TEXT NOT NULL, bucket_start TEXT NOT NULL, bucket_end TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT UNIQUE)")
                rows = self.conn.execute("SELECT id,keyword_id,source,mention_count,unique_authors,engagement,observed_at,status,idempotency_key FROM signal_observations").fetchall()
                for row in rows:
                    start, end = _bucket_bounds(row[6]); self.conn.execute("INSERT INTO signal_observations_v3 VALUES(?,?,?,?,?,?,?,?,?,?,?)", (row[0], row[1], row[2], row[3], row[4], row[5], row[6], start, end, row[7], row[8]))
                self.conn.execute("DROP TABLE signal_observations"); self.conn.execute("ALTER TABLE signal_observations_v3 RENAME TO signal_observations")
            signal_cols = self._columns("signals")
            signal_info = {r[1]: r for r in self.conn.execute("PRAGMA table_info(signals)")}
            signal_fk_targets = {r[2] for r in self.conn.execute("PRAGMA foreign_key_list(signals)")}
            if signal_info.get("velocity_1h", (None, None, None, 0))[3] or "signal_observations" not in signal_fk_targets or "trend_state" not in signal_cols:
                self.conn.execute("CREATE TABLE signals_v3 (id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id), source TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, mention_count INTEGER, unique_authors INTEGER, engagement INTEGER, velocity_1h REAL, velocity_3h REAL, velocity_6h REAL, velocity_12h REAL, velocity_24h REAL, acceleration REAL, platform_count INTEGER NOT NULL DEFAULT 1, country_count INTEGER NOT NULL DEFAULT 1, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'FIXTURE', is_fast_candidate INTEGER NOT NULL DEFAULT 0, observation_id INTEGER REFERENCES signal_observations(id), idempotency_key TEXT UNIQUE, trend_state TEXT NOT NULL DEFAULT 'NORMAL')")
                select_trend = "trend_state" if "trend_state" in signal_cols else "'NORMAL'"
                self.conn.execute(f"INSERT INTO signals_v3 (id,keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key,trend_state) SELECT id,keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key,{select_trend} FROM signals")
                self.conn.execute("DROP TABLE signals"); self.conn.execute("ALTER TABLE signals_v3 RENAME TO signals")
            if "input_provenance" not in self._columns("opportunities"): self._add_column("opportunities", "input_provenance", "TEXT NOT NULL DEFAULT '{}'")
            if "risk_class" in self._columns("opportunities"): self.conn.execute("UPDATE opportunities SET risk_class='UNKNOWN' WHERE risk_class IN ('unknown','') OR risk_class IS NULL")
            self.conn.commit(); self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)", (3, now()))
            self.conn.execute("INSERT INTO migration_history(from_version,to_version,migration,applied_at) VALUES(?,?,?,?)", (max(version, 2), 3, "stage_1_6_followup_buckets", now()))
            self.conn.commit()
            if self.conn.execute("PRAGMA foreign_key_check").fetchall(): raise sqlite3.IntegrityError("foreign_key_check failed after v3 migration")

        if version < 4:
            for name, definition in (("captured_at", "TEXT NOT NULL DEFAULT ''"), ("provider_request_id", "TEXT"), ("raw_evidence", "TEXT NOT NULL DEFAULT '{}'")):
                self._add_column("signal_observations", name, definition)
            self.conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)", (4, now()))
            self.conn.execute("INSERT INTO migration_history(from_version,to_version,migration,applied_at) VALUES(?,?,?,?)", (max(version, 3), 4, "stage_2_0_provider_evidence", now()))
            self.conn.commit()

    def close(self):
        self.conn.close()

    def config(self, key, default=None):
        row = self.conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_config(self, key, value):
        self.conn.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.conn.commit()

    def add_audit(self, action, entity_type, entity_id, status, details):
        self.conn.execute("INSERT INTO audit_logs(action,entity_type,entity_id,status,details,created_at) VALUES(?,?,?,?,?,?)", (action, entity_type, entity_id, status, json.dumps(details), now()))
        self.conn.commit()

    def keyword(self, word, country="US", language="en"):
        self.conn.execute("INSERT OR IGNORE INTO keywords(keyword,country,language,created_at) VALUES(?,?,?,?)", (word, country, language, now()))
        return self.conn.execute("SELECT id FROM keywords WHERE keyword=? AND country=? AND language=?", (word, country, language)).fetchone()[0]

    def site(self, **v):
        fields = "tenant_id,country,language,topic,platform,authority_tags,publisher_type,ads_type,ads_account_ref,search_console_ref,analytics_ref,average_rpm,average_revenue,health_status,policy_status"
        vals = [v.get(x) for x in fields.split(',')]
        vals[5] = vals[5] or ''
        vals[6] = vals[6] or v['platform']; vals[7] = vals[7] or 'NOT_CONFIGURED'
        vals[11] = vals[11] or 0; vals[12] = vals[12] or 0
        vals[13] = vals[13] or 'UNKNOWN'; vals[14] = vals[14] or 'UNKNOWN'
        cur = self.conn.execute(f"INSERT INTO sites({fields}) VALUES({','.join('?' for _ in vals)})", vals)
        self.conn.commit()
        return cur.lastrowid


class TrendSensor:
    defaults = {"fast_signal_min_mentions": 10, "fast_signal_min_velocity": 10, "fast_signal_min_acceleration": 0}
    def __init__(self, db, config=None, version="A-v2"):
        self.db = db; self.config = dict(self.defaults); self.config.update(config or {}); self.version = version
        self.db.conn.execute("INSERT OR IGNORE INTO harness_versions(component,version,config,created_at) VALUES(?,?,?,?)", ("TrendSensor", version, json.dumps(self.config), now())); self.db.conn.commit()

    def _history_velocity(self, keyword_id, observed_at, hours):
        bucket_start = datetime.fromisoformat(_bucket_bounds(observed_at)[0])
        values = []
        for offset in range(hours):
            start = (bucket_start - timedelta(hours=offset)).isoformat()
            rows = self.db.conn.execute("SELECT mention_count,status FROM signal_observations WHERE keyword_id=? AND bucket_start=?", (keyword_id, start)).fetchall()
            if any(r[1] == "MISSING" or r[0] is None for r in rows): return None
            if not rows: return None
            values.append(sum(r[0] for r in rows))
        return sum(values) / hours

    def ingest(self, keyword, source, samples=None, country="US", language="en", platform_count=1, country_count=1,
               mention_count=None, unique_authors=None, engagement=None, observed_at=None, status=None, idempotency_key=None,
               captured_at=None, provider_request_id=None, raw_evidence=None):
        kid = self.db.keyword(keyword, country, language)
        observed_at = observed_at or now()
        if samples is not None:
            if len(samples) != 5: raise ValueError("samples must be [1h,3h,6h,12h,24h]")
            m1, m3, m6, m12, m24 = samples
            metric_status = status or "FIXTURE"
            mention_count = m1 if mention_count is None else mention_count
            velocities = (m1, m3 / 3, m6 / 6, m12 / 12, m24 / 24)
        else:
            metric_status = status or ("OBSERVED" if None not in (mention_count, unique_authors, engagement) else "MISSING")
            velocities = None
        if idempotency_key:
            old = self.db.conn.execute("SELECT id FROM signals WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if old: return old[0]
            obs_key = f"observation:{idempotency_key}"
        else: obs_key = None
        bucket_start, bucket_end = _bucket_bounds(observed_at)
        self.db.conn.execute("INSERT OR IGNORE INTO signal_observations(keyword_id,source,mention_count,unique_authors,engagement,observed_at,bucket_start,bucket_end,captured_at,provider_request_id,raw_evidence,status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (kid, source, mention_count, unique_authors, engagement, observed_at, bucket_start, bucket_end, captured_at or now(), provider_request_id, json.dumps(raw_evidence or {}), metric_status, obs_key))
        obs = self.db.conn.execute("SELECT id FROM signal_observations WHERE keyword_id=? AND source=? AND observed_at=? AND (idempotency_key=? OR (? IS NULL AND idempotency_key IS NULL)) ORDER BY id DESC LIMIT 1", (kid, source, observed_at, obs_key, obs_key)).fetchone()
        if velocities is None: velocities = tuple(self._history_velocity(kid, observed_at, h) for h in (1, 3, 6, 12, 24))
        v1, v3, v6, v12, v24 = velocities
        acceleration = None if None in (v1, v3) else v1 - v3
        fast = int(metric_status in {"OBSERVED", "FIXTURE"} and v1 is not None and acceleration is not None and v1 >= self.config["fast_signal_min_mentions"] and v1 >= self.config["fast_signal_min_velocity"] and acceleration >= self.config["fast_signal_min_acceleration"])
        trend_state = "FAST_SIGNAL" if fast else "NORMAL"
        cur = self.db.conn.execute("INSERT INTO signals(keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key,trend_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (kid, source, country, language, mention_count, unique_authors, engagement, v1, v3, v6, v12, v24, acceleration, platform_count, country_count, observed_at, observed_at, metric_status, fast, obs[0], idempotency_key, trend_state))
        self.db.conn.commit(); sid = cur.lastrowid
        self.db.add_audit("trend.ingest", "signal", sid, "PASS", {"metric_status": metric_status, "rolling_window": "24h", "raw_observation_id": obs[0]})
        return sid

    def ingest_provider(self, provider, country, language, since, until):
        result = provider.fetch_trends(country, language, since, until)
        if result.status != "PASS":
            return {"provider_status": result.status, "signals": [], "normalized_count": 0, "error": result.error}
        if not isinstance(result.data, list):
            return {"provider_status": "FAIL", "signals": [], "normalized_count": 0, "error": "provider data must be a list"}
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if since_dt.tzinfo is None or until_dt.tzinfo is None: raise ValueError
        except (AttributeError, TypeError, ValueError):
            return {"provider_status": "FAIL", "signals": [], "normalized_count": 0, "error": "invalid provider window"}
        ids = []
        for item in result.data:
            if not isinstance(item, dict):
                return {"provider_status": "FAIL", "signals": ids, "normalized_count": len(ids), "error": "provider item must be an object"}
            required = ("keyword", "source", "mention_count", "unique_authors", "engagement", "observed_at", "country", "language")
            if any(field not in item for field in required) or item["country"] != country or item["language"] != language:
                return {"provider_status": "FAIL", "signals": ids, "normalized_count": len(ids), "error": "malformed normalized provider item"}
            if item["source"] != getattr(provider, "name", item["source"]):
                return {"provider_status": "FAIL", "signals": ids, "normalized_count": len(ids), "error": "provider source mismatch"}
            if not all(isinstance(item[field], (int, float)) and not isinstance(item[field], bool) and math.isfinite(item[field]) and item[field] >= 0 for field in ("mention_count", "unique_authors", "engagement")):
                return {"provider_status": "FAIL", "signals": ids, "normalized_count": len(ids), "error": "invalid provider metric"}
            try:
                stamp = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
                if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > datetime.now(stamp.tzinfo): raise ValueError
            except (AttributeError, TypeError, ValueError):
                return {"provider_status": "FAIL", "signals": ids, "normalized_count": len(ids), "error": "invalid provider observed_at"}
            captured_at = item.get("captured_at") or result.captured_at
            if not _valid_captured_at(captured_at):
                return {"provider_status": "FAIL", "signals": ids, "normalized_count": len(ids), "error": "invalid provider captured_at"}
            status = "STALE" if stamp.astimezone(timezone.utc) < since_dt.astimezone(timezone.utc) else "OBSERVED"
            ids.append(self.ingest(item["keyword"], item["source"], country=country, language=language, mention_count=item["mention_count"], unique_authors=item["unique_authors"], engagement=item["engagement"], observed_at=item["observed_at"], status=status, idempotency_key=item.get("idempotency_key") or f"{item['source']}:{item['keyword']}:{item['observed_at']}", captured_at=captured_at, provider_request_id=item.get("provider_request_id") or result.provider_request_id, raw_evidence=item.get("raw_evidence") or {"provider": item["source"], "normalized_count": len(result.data)}))
        return {"provider_status": "PASS", "signals": ids, "normalized_count": len(ids)}


class Scheduler:
    def __init__(self, sensor): self.sensor = sensor
    def run_once(self, records, run_id=None):
        ids = []
        for i, original in enumerate(records):
            record = dict(original); key = record.pop("idempotency_key", None) or (f"{run_id}:{i}" if run_id else None)
            ids.append(self.sensor.ingest(**record, idempotency_key=key))
        return {"status": "PASS", "interval": "1h", "run_id": run_id, "signals": ids}


class OpportunityEngine:
    labels = ("IGNORE", "WATCH", "FAST_WRITE", "MONEY_WRITE", "WINNER_UPDATE", "EXPERIMENT", "REVIEW_REQUIRED")
    high_risk_classes = {"FINANCE", "HEALTH", "LAW", "POLITICS", "ACCIDENT", "RUMOR_PERSON"}
    defaults = {"velocity": .25, "search_gap": .15, "competition": -.15, "revenue": .15, "site_fit": .1, "country_fit": .1, "freshness": .15, "risk": -.2, "cost": -.05,
                "fast_min_velocity": 20, "fast_min_acceleration": 0, "fast_min_search_gap": .3, "fast_max_risk": .7}
    def __init__(self, db, config=None, version="Opportunity-v1"):
        self.db = db; self.config = dict(self.defaults); self.config.update(config or {}); self.version = version
        self.db.conn.execute("INSERT OR IGNORE INTO harness_versions(component,version,config,created_at) VALUES(?,?,?,?)", ("Opportunity", version, json.dumps(self.config), now())); self.db.conn.commit()

    def decide(self, signal_id, search_gap=None, competition=None, historical_revenue=None, site_fit=None, country_fit=None, freshness=None, risk=None, cost=None, idempotency_key=None, input_statuses=None, provenance=None, mode=None, risk_class="unknown", risk_reason=""):
        if idempotency_key:
            old = self.db.conn.execute("SELECT id FROM opportunities WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if old: return old[0]
        s = self.db.conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not s: raise ValueError("signal not found")
        fixture = s["status"] == "FIXTURE" and (mode or "FIXTURE") == "FIXTURE"
        if fixture:
            search_gap = .6 if search_gap is None else search_gap; competition = .4 if competition is None else competition; historical_revenue = .2 if historical_revenue is None else historical_revenue; site_fit = .7 if site_fit is None else site_fit; country_fit = 1 if country_fit is None else country_fit; freshness = .8 if freshness is None else freshness; risk = .1 if risk is None else risk; cost = .2 if cost is None else cost
            statuses = {k: "FIXTURE" for k in ("search_gap", "competition", "history", "site_fit", "country_fit", "freshness", "risk", "cost")}; decision_mode = "FIXTURE"
        else:
            values = {"search_gap": search_gap, "competition": competition, "history": historical_revenue, "site_fit": site_fit, "country_fit": country_fit, "freshness": freshness, "risk": risk, "cost": cost}; statuses = input_statuses or {}; statuses = {k: statuses.get(k, "MISSING" if values[k] is None else "REAL") for k in values}; statuses = {k: ("MISSING" if statuses[k] == "REAL" and values[k] is None else statuses[k]) for k in values}; decision_mode = "REAL"
            if statuses["risk"] not in {"REAL", "FIXTURE"}: decision = "REVIEW_REQUIRED"
            elif any(statuses[k] not in {"REAL", "FIXTURE"} for k in values): decision = "WATCH"
            else: decision = None
            search_gap = 0 if search_gap is None else search_gap; competition = 0 if competition is None else competition; historical_revenue = 0 if historical_revenue is None else historical_revenue; site_fit = 0 if site_fit is None else site_fit; country_fit = 0 if country_fit is None else country_fit; freshness = 0 if freshness is None else freshness; risk = 0 if risk is None else risk; cost = 0 if cost is None else cost
        c = {"velocity": min(s["velocity_1h"] / 100, 1) if s["velocity_1h"] is not None else 0, "search_gap": search_gap, "competition": competition, "revenue": historical_revenue, "site_fit": site_fit, "country_fit": country_fit, "freshness": freshness, "risk": risk, "cost": cost}
        score = sum(self.config[k] * c[k] for k in c)
        fast_eligible = s["velocity_1h"] is not None and s["acceleration"] is not None and s["velocity_1h"] >= self.config["fast_min_velocity"] and s["acceleration"] >= self.config["fast_min_acceleration"] and search_gap >= self.config["fast_min_search_gap"]
        if risk > self.config["fast_max_risk"]: decision = "REVIEW_REQUIRED"
        elif not fixture and decision is not None: pass
        elif s["status"] == "MISSING": decision = "WATCH"
        elif fast_eligible: decision = "FAST_WRITE"
        else: decision = "MONEY_WRITE" if score >= .3 else "WATCH" if score >= .05 else "IGNORE"
        strongest = max(c, key=lambda k: abs(self.config[k] * c[k]))
        provenance_payload = {}
        for key, value in (("search_gap", search_gap), ("competition", competition), ("history", historical_revenue), ("site_fit", site_fit), ("country_fit", country_fit), ("freshness", freshness), ("risk", risk), ("cost", cost)):
            supplied = (provenance or {}).get(key) if isinstance(provenance, dict) else None
            if fixture: provenance_payload[key] = {"value": value, "status": "FIXTURE", "source": "fixture", "captured_at": now()}
            elif (isinstance(supplied, dict) and supplied.get("status") == "REAL" and supplied.get("source")
                  and _valid_captured_at(supplied.get("captured_at"))
                  and _provenance_value_matches(value, supplied.get("value"))):
                provenance_payload[key] = supplied
            else:
                statuses[key] = "MISSING"; provenance_payload[key] = {"value": value, "status": "MISSING", "source": None, "captured_at": None}
        normalized_risk_class = (risk_class or "UNKNOWN").upper()
        if not fixture and (statuses["risk"] == "MISSING" or normalized_risk_class == "UNKNOWN" or normalized_risk_class in self.high_risk_classes): decision = "REVIEW_REQUIRED"
        elif not fixture and any(statuses[k] == "MISSING" for k in statuses): decision = "WATCH"
        reason = f"{decision}: score={score:.3f}; fast_eligible={fast_eligible}; risk={risk}; strongest={strongest}"
        cur = self.db.conn.execute("INSERT INTO opportunities(keyword_id,signal_id,decision,score,decision_reason,score_components,engine_version,created_at,idempotency_key,decision_mode,input_statuses,risk_class,risk_score,risk_reason,input_provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (s["keyword_id"], signal_id, decision, score, reason, json.dumps(c), self.version, now(), idempotency_key, decision_mode, json.dumps(statuses), normalized_risk_class, risk, risk_reason, json.dumps(provenance_payload)))
        self.db.conn.commit(); oid = cur.lastrowid; self.db.add_audit("opportunity.decide", "opportunity", oid, "PASS", {"decision": decision, "reason": reason, "risk_veto": risk > self.config["fast_max_risk"]}); return oid


class Editorial:
    content_types = {"FAST", "MONEY", "MAINTENANCE", "EXPERIMENT"}
    def __init__(self, db): self.db = db
    def serp(self, opportunity_id, results, engine="recorded-serp"):
        o = self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?", (opportunity_id,)).fetchone(); query = self.db.conn.execute("SELECT keyword FROM keywords WHERE id=?", (o[0],)).fetchone()[0]
        status = "FIXTURE" if engine.startswith("recorded") else "PASS"
        cur = self.db.conn.execute("INSERT INTO serp_snapshots(opportunity_id,engine,query,captured_at,status) VALUES(?,?,?,?,?)", (opportunity_id, engine, query, now(), status)); sid = cur.lastrowid
        for i, r in enumerate(results[:10], 1): self.db.conn.execute("INSERT INTO serp_results(snapshot_id,position,title,url,snippet,features) VALUES(?,?,?,?,?,?)", (sid, i, r["title"], r["url"], r.get("snippet", ""), json.dumps(r.get("features", {}))))
        self.db.conn.commit(); self.db.add_audit("serp.analyze", "serp_snapshot", sid, "PASS", {"top10": min(10, len(results)), "status": status}); return sid
    def plan(self, opportunity_id, site_id, content_type, gaps, intent, outline, harness="B-v1", site_fit_score=None, site_fit_reason=None):
        if content_type not in self.content_types: raise ValueError(f"unsupported content type: {content_type}")
        cur = self.db.conn.execute("INSERT INTO content_plans(opportunity_id,site_id,content_type,search_intent,content_gaps,outline,context,harness_version,site_fit_score,site_fit_reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (opportunity_id, site_id, content_type, intent, json.dumps(gaps), json.dumps(outline), "trend+decision+serp+history+site_identity", harness, site_fit_score, site_fit_reason, now())); self.db.conn.commit(); pid = cur.lastrowid; self.db.add_audit("content.plan", "content_plan", pid, "PASS", {"history_context": True, "site_fit_score": site_fit_score}); return pid
    def article(self, plan_id, title, body):
        if not title or not body: raise ValueError("title and body are required")
        cur = self.db.conn.execute("INSERT INTO contents(plan_id,title,body,content_version,created_at) VALUES(?,?,?,?,?)", (plan_id, title, body, "content-v1", now())); self.db.conn.commit(); cid = cur.lastrowid; self.db.add_audit("content.generate", "content", cid, "PASS", {"originality_guard": "no SERP text copied"}); return cid
    def history(self, keyword_id):
        return [dict(x) for x in self.db.conn.execute("SELECT cp.content_type,cp.harness_version,p.id publication_id,r.rank,r.checkpoint,r.captured_at,t.impression,t.click,t.ctr,t.google_traffic,t.naver_traffic,t.sns_traffic,t.direct_traffic,t.engagement_time,t.page_views,sm.query,sm.page,sm.country search_country,sm.device,sm.provider_status search_console_status,am.source,am.medium,am.country analytics_country,am.sessions,am.users,am.provider_status analytics_status,rm.adsense_revenue,rm.adpost_revenue,rm.rpm,r.provider_status rank_status,rm.provider_status revenue_status,p.published_at FROM publications p JOIN contents c ON c.id=p.content_id JOIN content_plans cp ON cp.id=c.plan_id JOIN opportunities o ON o.id=cp.opportunity_id LEFT JOIN rank_history r ON r.publication_id=p.id LEFT JOIN traffic_metrics t ON t.publication_id=p.id AND t.checkpoint=r.checkpoint LEFT JOIN search_metrics sm ON sm.publication_id=p.id AND sm.checkpoint=r.checkpoint LEFT JOIN analytics_metrics am ON am.publication_id=p.id AND am.checkpoint=r.checkpoint LEFT JOIN revenue_metrics rm ON rm.publication_id=p.id AND rm.checkpoint=r.checkpoint WHERE o.keyword_id=? ORDER BY r.captured_at DESC", (keyword_id,))]


class LocalPublisher:
    def __init__(self, db): self.db = db
    def publish(self, content_id, site_id, output_dir, idempotency_key=None):
        if idempotency_key:
            old = self.db.conn.execute("SELECT id FROM publications WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if old: return old[0]
        c = self.db.conn.execute("SELECT title,body FROM contents WHERE id=?", (content_id,)).fetchone(); path = (Path(output_dir) / f"content-{content_id}.html").resolve(); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(f"<html><head><title>{c['title']}</title></head><body><h1>{c['title']}</h1><p>{c['body']}</p></body></html>")
        site = self.db.conn.execute("SELECT platform FROM sites WHERE id=?", (site_id,)).fetchone(); cur = self.db.conn.execute("INSERT INTO publications(content_id,site_id,platform,external_id,url,status,published_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?)", (content_id, site_id, site[0], str(path), path.as_uri(), "PUBLISHED", now(), idempotency_key)); self.db.conn.commit(); pid = cur.lastrowid; self.db.add_audit("publisher.publish", "publication", pid, "PASS", {"adapter": "local", "url": path.as_uri()}); return pid
    def update(self, publication_id, title, body):
        p = self.db.conn.execute("SELECT external_id FROM publications WHERE id=?", (publication_id,)).fetchone(); Path(p[0]).write_text(f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"); self.db.conn.execute("UPDATE publications SET status='UPDATED' WHERE id=?", (publication_id,)); self.db.conn.commit(); return self.get_url(publication_id)
    def get_status(self, publication_id): return self.db.conn.execute("SELECT status FROM publications WHERE id=?", (publication_id,)).fetchone()[0]
    def get_url(self, publication_id): return self.db.conn.execute("SELECT url FROM publications WHERE id=?", (publication_id,)).fetchone()[0]


class NotConfiguredPublisher:
    def __init__(self, platform): self.platform = platform
    def publish(self, *args, **kwargs): return {"status": "NOT_CONFIGURED", "platform": self.platform}
    def update(self, *args, **kwargs): return {"status": "NOT_CONFIGURED", "platform": self.platform}
    def get_status(self, *args, **kwargs): return "NOT_CONFIGURED"
    def get_url(self, *args, **kwargs): return None


class Telemetry:
    checkpoints = {"1h", "6h", "12h", "24h", "72h", "7d", "30d", "custom"}
    def __init__(self, db): self.db = db
    def record(self, publication_id, keyword_id, rank=None, traffic=None, revenue=None, provider_status=None, checkpoint="custom", rank_status=None, search_console_status=None, analytics_status=None, revenue_status=None, idempotency_key=None, search=None, analytics=None):
        if checkpoint not in self.checkpoints: raise ValueError(f"unsupported telemetry checkpoint: {checkpoint}")
        if idempotency_key and self.db.conn.execute("SELECT id FROM rank_history WHERE idempotency_key=?", (idempotency_key,)).fetchone(): return
        rank_status = rank_status or provider_status or "NOT_CONFIGURED"; search_console_status = search_console_status or provider_status or "NOT_CONFIGURED"; analytics_status = analytics_status or provider_status or "NOT_CONFIGURED"; revenue_status = revenue_status or provider_status or "NOT_CONFIGURED"; t = now(); traffic = traffic or {}; revenue = revenue or {}
        self.db.conn.execute("INSERT INTO rank_history(publication_id,keyword_id,rank,checkpoint,captured_at,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?)", (publication_id, keyword_id, rank, checkpoint, t, rank_status, idempotency_key))
        self.db.conn.execute("INSERT INTO traffic_metrics(publication_id,checkpoint,captured_at,impression,click,ctr,google_traffic,naver_traffic,sns_traffic,direct_traffic,engagement_time,page_views,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (publication_id, checkpoint, t, *[traffic.get(k) for k in ('impression','click','ctr','google_traffic','naver_traffic','sns_traffic','direct_traffic','engagement_time','page_views')], analytics_status, (f"traffic:{idempotency_key}" if idempotency_key else None)))
        self.db.conn.execute("INSERT INTO revenue_metrics(publication_id,checkpoint,captured_at,adsense_revenue,adpost_revenue,rpm,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?)", (publication_id, checkpoint, t, revenue.get('adsense_revenue'), revenue.get('adpost_revenue'), revenue.get('rpm'), revenue_status, (f"revenue:{idempotency_key}" if idempotency_key else None)))
        search = search or {}; analytics = analytics or {}
        self.db.conn.execute("INSERT INTO search_metrics(publication_id,checkpoint,captured_at,query,page,country,device,impressions,clicks,ctr,position,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (publication_id, checkpoint, t, search.get('query'), search.get('page'), search.get('country'), search.get('device'), search.get('impressions', traffic.get('impression')), search.get('clicks', traffic.get('click')), search.get('ctr', traffic.get('ctr')), search.get('position', rank), search_console_status, (f"search:{idempotency_key}" if idempotency_key else None)))
        self.db.conn.execute("INSERT INTO analytics_metrics(publication_id,checkpoint,captured_at,source,medium,country,sessions,users,engagement_time,page_views,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (publication_id, checkpoint, t, analytics.get('source'), analytics.get('medium'), analytics.get('country'), analytics.get('sessions'), analytics.get('users'), analytics.get('engagement_time', traffic.get('engagement_time')), analytics.get('page_views', traffic.get('page_views')), analytics_status, (f"analytics:{idempotency_key}" if idempotency_key else None)))
        self.db.conn.commit(); self.db.add_audit("telemetry.record", "publication", publication_id, "PASS" if all(x in {"PASS", "NOT_CONFIGURED", "CONFIGURED_NO_DATA", "PARTIAL"} for x in (rank_status, search_console_status, analytics_status, revenue_status)) else "FAIL", {"checkpoint": checkpoint, "rank_status": rank_status, "search_console_status": search_console_status, "analytics_status": analytics_status, "revenue_status": revenue_status})
    def record_cost(self, component, provider, amount=0, input_tokens=0, output_tokens=0, status="NOT_CONFIGURED", opportunity_id=None, content_id=None, publication_id=None, idempotency_key=None):
        if idempotency_key and self.db.conn.execute("SELECT id FROM cost_metrics WHERE idempotency_key=?", (idempotency_key,)).fetchone(): return
        self.db.conn.execute("INSERT INTO cost_metrics(component,provider,opportunity_id,content_id,publication_id,input_tokens,output_tokens,amount,currency,captured_at,status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (component, provider, opportunity_id, content_id, publication_id, input_tokens, output_tokens, amount, "USD", now(), status, idempotency_key)); self.db.conn.commit(); self.db.add_audit("cost.record", "cost_metric", None, status, {"component": component, "amount": amount, "content_id": content_id, "publication_id": publication_id})


def _status(db, table, start, end, timestamp="captured_at"):
    values = {r[0] for r in db.conn.execute(f"SELECT DISTINCT provider_status FROM {table} WHERE {timestamp}>=? AND {timestamp}<?", (start, end))}
    if not values: return "NOT_CONFIGURED"
    if "FAIL" in values and len(values) > 1: return "PARTIAL"
    if "FAIL" in values: return "FAIL"
    if "PASS" in values and len(values) > 1: return "PARTIAL"
    return next(iter(values))


def _overall_status(statuses):
    statuses = set(statuses)
    if not statuses or statuses == {"NOT_CONFIGURED"}: return "NOT_CONFIGURED"
    if "FAIL" in statuses and len(statuses) == 1: return "FAIL"
    if "PASS" in statuses and statuses <= {"PASS"}: return "PASS"
    return "PARTIAL"


def _window(db, start, end):
    revenue = db.conn.execute("SELECT coalesce(sum(adsense_revenue),0)+coalesce(sum(adpost_revenue),0) FROM revenue_metrics WHERE captured_at>=? AND captured_at<?", (start, end)).fetchone()[0]
    cost = db.conn.execute("SELECT coalesce(sum(amount),0) FROM cost_metrics WHERE captured_at>=? AND captured_at<?", (start, end)).fetchone()[0]
    rank_status = _status(db, "rank_history", start, end); gsc_status = _status(db, "search_metrics", start, end); ga4_status = _status(db, "analytics_metrics", start, end); revenue_status = _status(db, "revenue_metrics", start, end)
    return {"signals": db.conn.execute("SELECT count(*) FROM signals WHERE last_seen_at>=? AND last_seen_at<?", (start, end)).fetchone()[0], "opportunities": db.conn.execute("SELECT count(*) FROM opportunities WHERE created_at>=? AND created_at<?", (start, end)).fetchone()[0], "contents": db.conn.execute("SELECT count(*) FROM contents WHERE created_at>=? AND created_at<?", (start, end)).fetchone()[0], "publications": db.conn.execute("SELECT count(*) FROM publications WHERE published_at>=? AND published_at<?", (start, end)).fetchone()[0], "traffic_clicks": db.conn.execute("SELECT coalesce(sum(click),0) FROM traffic_metrics WHERE captured_at>=? AND captured_at<?", (start, end)).fetchone()[0], "revenue": revenue, "ai_cost": cost, "contribution_profit": revenue - cost, "rank_status": rank_status, "search_console_status": gsc_status, "analytics_status": ga4_status, "revenue_status": revenue_status, "external_telemetry": _overall_status((rank_status, gsc_status, ga4_status, revenue_status))}


def content_economics(db, content_id):
    revenue = db.conn.execute("SELECT coalesce(sum(r.adsense_revenue),0)+coalesce(sum(r.adpost_revenue),0) FROM revenue_metrics r JOIN publications p ON p.id=r.publication_id WHERE p.content_id=?", (content_id,)).fetchone()[0]
    cost = db.conn.execute("SELECT coalesce(sum(amount),0) FROM cost_metrics WHERE content_id=? OR opportunity_id IN (SELECT opportunity_id FROM content_plans cp JOIN contents c ON c.plan_id=cp.id WHERE c.id=?) OR publication_id IN (SELECT id FROM publications WHERE content_id=?)", (content_id, content_id, content_id)).fetchone()[0]
    return {"content_id": content_id, "revenue": revenue, "cost": cost, "contribution_profit": revenue - cost}


def _bounds(db, start_date, days):
    tz = ZoneInfo(db.config("report_timezone", "UTC")); local_start = datetime.combine(start_date, time.min, tzinfo=tz); local_end = local_start + timedelta(days=days); return local_start.astimezone(UTC).isoformat(), local_end.astimezone(UTC).isoformat(), local_start.date().isoformat(), (local_end - timedelta(microseconds=1)).astimezone(tz).date().isoformat()


def daily_report(db, report_date=None):
    tz = ZoneInfo(db.config("report_timezone", "UTC")); d = date.fromisoformat(report_date) if isinstance(report_date, str) else (report_date or datetime.now(tz).date()); start, end, pstart, pend = _bounds(db, d, 1); result = _window(db, start, end); result.update({"period": "daily", "period_start": pstart, "period_end": pend, "fast_candidates": db.conn.execute("SELECT count(*) FROM signals WHERE is_fast_candidate=1 AND last_seen_at>=? AND last_seen_at<?", (start, end)).fetchone()[0], "rank_changes": result["rank_status"], "errors": db.conn.execute("SELECT count(*) FROM audit_logs WHERE status='FAIL' AND created_at>=? AND created_at<?", (start, end)).fetchone()[0]}); return result


def period_report(db, period, report_date=None):
    tz = ZoneInfo(db.config("report_timezone", "UTC")); end_date = date.fromisoformat(report_date) if isinstance(report_date, str) else (report_date or datetime.now(tz).date()); days = 7 if period == "weekly" else 30 if period == "monthly" else None
    if days is None: raise ValueError("period must be weekly or monthly")
    start, end, pstart, pend = _bounds(db, end_date - timedelta(days=days - 1), days); result = _window(db, start, end); result.update({"period": period, "period_start": pstart, "period_end": pend, "by_content_type": [dict(r) for r in db.conn.execute("SELECT cp.content_type, count(*) count, coalesce(sum(rm.adsense_revenue),0)+coalesce(sum(rm.adpost_revenue),0) revenue FROM content_plans cp LEFT JOIN contents c ON c.plan_id=cp.id LEFT JOIN publications p ON p.content_id=c.id LEFT JOIN revenue_metrics rm ON rm.publication_id=p.id AND rm.captured_at>=? AND rm.captured_at<? WHERE cp.created_at>=? AND cp.created_at<? GROUP BY cp.content_type ORDER BY revenue DESC", (start, end, start, end))], "by_decision": [dict(r) for r in db.conn.execute("SELECT o.decision, count(*) count FROM opportunities o WHERE o.created_at>=? AND o.created_at<? GROUP BY o.decision", (start, end))], "top_sites": [dict(r) for r in db.conn.execute("SELECT s.id site_id,s.topic,s.country,s.platform,count(p.id) publications FROM sites s LEFT JOIN publications p ON p.site_id=s.id AND p.published_at>=? AND p.published_at<? GROUP BY s.id ORDER BY publications DESC LIMIT 10", (start, end))], "revenue_status": "NOT_CONFIGURED"}); return result
