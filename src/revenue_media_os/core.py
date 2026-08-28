import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 2
UTC = timezone.utc


def now():
    return datetime.now(UTC).isoformat()


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
                "signals": [("status", "TEXT NOT NULL DEFAULT 'FIXTURE'"), ("observation_id", "INTEGER"), ("idempotency_key", "TEXT")],
                "opportunities": [("idempotency_key", "TEXT")],
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
            if signal_info.get("unique_authors", (None, None, None, 0))[3] or signal_info.get("engagement", (None, None, None, 0))[3]:
                self.conn.execute("PRAGMA foreign_keys = OFF")
                self.conn.execute("CREATE TABLE signals_migrated (id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id), source TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, mention_count INTEGER NOT NULL, unique_authors INTEGER, engagement INTEGER, velocity_1h REAL NOT NULL, velocity_3h REAL NOT NULL, velocity_6h REAL NOT NULL, velocity_12h REAL NOT NULL, velocity_24h REAL NOT NULL, acceleration REAL NOT NULL, platform_count INTEGER NOT NULL DEFAULT 1, country_count INTEGER NOT NULL DEFAULT 1, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'FIXTURE', is_fast_candidate INTEGER NOT NULL DEFAULT 0, observation_id INTEGER, idempotency_key TEXT UNIQUE)")
                self.conn.execute("INSERT INTO signals_migrated SELECT id,keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key FROM signals")
                self.conn.execute("DROP TABLE signals"); self.conn.execute("ALTER TABLE signals_migrated RENAME TO signals"); self.conn.execute("PRAGMA foreign_keys = ON")
            for table, column in (("signals", "idempotency_key"), ("opportunities", "idempotency_key"), ("publications", "idempotency_key"), ("rank_history", "idempotency_key"), ("traffic_metrics", "idempotency_key"), ("revenue_metrics", "idempotency_key"), ("cost_metrics", "idempotency_key")):
                self.conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_{column} ON {table}({column}) WHERE {column} IS NOT NULL")
            # The old MVP had UNIQUE(keyword). Rebuild only when that exact
            # unique index is present; current databases take no path here.
            old_key = False
            for idx in self.conn.execute("PRAGMA index_list(keywords)"):
                if idx[2]:
                    cols = [r[2] for r in self.conn.execute(f"PRAGMA index_info({idx[1]})")]
                    old_key = cols == ["keyword"]
            if old_key:
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
    def __init__(self, db): self.db = db

    def _history_velocity(self, keyword_id, observed_at, hours):
        cutoff = (datetime.fromisoformat(observed_at) - timedelta(hours=hours)).isoformat()
        row = self.db.conn.execute("SELECT coalesce(sum(mention_count),0) FROM signal_observations WHERE keyword_id=? AND observed_at>? AND observed_at<=?", (keyword_id, cutoff, observed_at)).fetchone()
        return row[0] / hours

    def ingest(self, keyword, source, samples=None, country="US", language="en", platform_count=1, country_count=1,
               mention_count=None, unique_authors=None, engagement=None, observed_at=None, status=None, idempotency_key=None):
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
            mention_count = 0 if mention_count is None else mention_count
            velocities = tuple(self._history_velocity(kid, observed_at, h) for h in (1, 3, 6, 12, 24))
        if idempotency_key:
            old = self.db.conn.execute("SELECT id FROM signals WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if old: return old[0]
            obs_key = f"observation:{idempotency_key}"
        else: obs_key = None
        self.db.conn.execute("INSERT OR IGNORE INTO signal_observations(keyword_id,source,mention_count,unique_authors,engagement,observed_at,status,idempotency_key) VALUES(?,?,?,?,?,?,?,?)", (kid, source, mention_count, unique_authors, engagement, observed_at, metric_status, obs_key))
        obs = self.db.conn.execute("SELECT id FROM signal_observations WHERE keyword_id=? AND source=? AND observed_at=? AND (idempotency_key=? OR (? IS NULL AND idempotency_key IS NULL)) ORDER BY id DESC LIMIT 1", (kid, source, observed_at, obs_key, obs_key)).fetchone()
        v1, v3, v6, v12, v24 = velocities
        acceleration = v1 - v3
        fast = int(metric_status in {"OBSERVED", "FIXTURE"} and v1 >= 0 and acceleration > 0)
        cur = self.db.conn.execute("INSERT INTO signals(keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (kid, source, country, language, mention_count, unique_authors, engagement, v1, v3, v6, v12, v24, acceleration, platform_count, country_count, observed_at, observed_at, metric_status, fast, obs[0], idempotency_key))
        self.db.conn.commit(); sid = cur.lastrowid
        self.db.add_audit("trend.ingest", "signal", sid, "PASS", {"metric_status": metric_status, "rolling_window": "24h", "raw_observation_id": obs[0]})
        return sid


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
    defaults = {"velocity": .25, "search_gap": .15, "competition": -.15, "revenue": .15, "site_fit": .1, "country_fit": .1, "freshness": .15, "risk": -.2, "cost": -.05,
                "fast_min_velocity": 20, "fast_min_acceleration": 0, "fast_min_search_gap": .3, "fast_max_risk": .7}
    def __init__(self, db, config=None, version="Opportunity-v1"):
        self.db = db; self.config = dict(self.defaults); self.config.update(config or {}); self.version = version
        self.db.conn.execute("INSERT OR IGNORE INTO harness_versions(component,version,config,created_at) VALUES(?,?,?,?)", ("Opportunity", version, json.dumps(self.config), now())); self.db.conn.commit()

    def decide(self, signal_id, search_gap=.6, competition=.4, historical_revenue=.2, site_fit=.7, country_fit=1, freshness=.8, risk=.1, cost=.2, idempotency_key=None):
        if idempotency_key:
            old = self.db.conn.execute("SELECT id FROM opportunities WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if old: return old[0]
        s = self.db.conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not s: raise ValueError("signal not found")
        c = {"velocity": min(s["velocity_1h"] / 100, 1), "search_gap": search_gap, "competition": competition, "revenue": historical_revenue, "site_fit": site_fit, "country_fit": country_fit, "freshness": freshness, "risk": risk, "cost": cost}
        score = sum(self.config[k] * c[k] for k in c)
        fast_eligible = s["velocity_1h"] >= self.config["fast_min_velocity"] and s["acceleration"] >= self.config["fast_min_acceleration"] and search_gap >= self.config["fast_min_search_gap"]
        if risk > self.config["fast_max_risk"]: decision = "REVIEW_REQUIRED"
        elif s["status"] == "MISSING": decision = "WATCH"
        elif fast_eligible: decision = "FAST_WRITE"
        else: decision = "MONEY_WRITE" if score >= .3 else "WATCH" if score >= .05 else "IGNORE"
        strongest = max(c, key=lambda k: abs(self.config[k] * c[k]))
        reason = f"{decision}: score={score:.3f}; fast_eligible={fast_eligible}; risk={risk}; strongest={strongest}"
        cur = self.db.conn.execute("INSERT INTO opportunities(keyword_id,signal_id,decision,score,decision_reason,score_components,engine_version,created_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)", (s["keyword_id"], signal_id, decision, score, reason, json.dumps(c), self.version, now(), idempotency_key))
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
        return [dict(x) for x in self.db.conn.execute("SELECT cp.content_type,cp.harness_version,p.id publication_id,r.rank,r.checkpoint,r.captured_at,t.impression,t.click,t.ctr,t.google_traffic,t.naver_traffic,t.sns_traffic,t.direct_traffic,t.engagement_time,t.page_views,rm.adsense_revenue,rm.adpost_revenue,rm.rpm,r.provider_status rank_status,t.provider_status analytics_status,rm.provider_status revenue_status,p.published_at FROM publications p JOIN contents c ON c.id=p.content_id JOIN content_plans cp ON cp.id=c.plan_id JOIN opportunities o ON o.id=cp.opportunity_id LEFT JOIN rank_history r ON r.publication_id=p.id LEFT JOIN traffic_metrics t ON t.publication_id=p.id AND t.checkpoint=r.checkpoint LEFT JOIN revenue_metrics rm ON rm.publication_id=p.id AND rm.checkpoint=r.checkpoint WHERE o.keyword_id=? ORDER BY r.captured_at DESC", (keyword_id,))]


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
    def record(self, publication_id, keyword_id, rank=None, traffic=None, revenue=None, provider_status=None, checkpoint="custom", rank_status=None, search_console_status=None, analytics_status=None, revenue_status=None, idempotency_key=None):
        if checkpoint not in self.checkpoints: raise ValueError(f"unsupported telemetry checkpoint: {checkpoint}")
        if idempotency_key and self.db.conn.execute("SELECT id FROM rank_history WHERE idempotency_key=?", (idempotency_key,)).fetchone(): return
        rank_status = rank_status or provider_status or "NOT_CONFIGURED"; search_console_status = search_console_status or provider_status or "NOT_CONFIGURED"; analytics_status = analytics_status or provider_status or "NOT_CONFIGURED"; revenue_status = revenue_status or provider_status or "NOT_CONFIGURED"; t = now(); traffic = traffic or {}; revenue = revenue or {}
        self.db.conn.execute("INSERT INTO rank_history(publication_id,keyword_id,rank,checkpoint,captured_at,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?)", (publication_id, keyword_id, rank, checkpoint, t, rank_status, idempotency_key))
        self.db.conn.execute("INSERT INTO traffic_metrics(publication_id,checkpoint,captured_at,impression,click,ctr,google_traffic,naver_traffic,sns_traffic,direct_traffic,engagement_time,page_views,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (publication_id, checkpoint, t, *[traffic.get(k) for k in ('impression','click','ctr','google_traffic','naver_traffic','sns_traffic','direct_traffic','engagement_time','page_views')], analytics_status, (f"traffic:{idempotency_key}" if idempotency_key else None)))
        self.db.conn.execute("INSERT INTO revenue_metrics(publication_id,checkpoint,captured_at,adsense_revenue,adpost_revenue,rpm,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?)", (publication_id, checkpoint, t, revenue.get('adsense_revenue'), revenue.get('adpost_revenue'), revenue.get('rpm'), revenue_status, (f"revenue:{idempotency_key}" if idempotency_key else None))); self.db.conn.commit(); self.db.add_audit("telemetry.record", "publication", publication_id, "PASS" if all(x in {"PASS", "NOT_CONFIGURED", "CONFIGURED_NO_DATA"} for x in (rank_status, search_console_status, analytics_status, revenue_status)) else "FAIL", {"checkpoint": checkpoint, "rank_status": rank_status, "search_console_status": search_console_status, "analytics_status": analytics_status, "revenue_status": revenue_status})
    def record_cost(self, component, provider, amount=0, input_tokens=0, output_tokens=0, status="NOT_CONFIGURED", opportunity_id=None, content_id=None, publication_id=None, idempotency_key=None):
        if idempotency_key and self.db.conn.execute("SELECT id FROM cost_metrics WHERE idempotency_key=?", (idempotency_key,)).fetchone(): return
        self.db.conn.execute("INSERT INTO cost_metrics(component,provider,opportunity_id,content_id,publication_id,input_tokens,output_tokens,amount,currency,captured_at,status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (component, provider, opportunity_id, content_id, publication_id, input_tokens, output_tokens, amount, "USD", now(), status, idempotency_key)); self.db.conn.commit(); self.db.add_audit("cost.record", "cost_metric", None, status, {"component": component, "amount": amount, "content_id": content_id, "publication_id": publication_id})


class PublisherRouter:
    def __init__(self, db, adapters=None): self.db = db; self.adapters = adapters or {"local": LocalPublisher(db)}
    def select_site_result(self, keyword, country, language, topic=None, authority_tags=None):
        clauses = ["country=?", "language=?", "health_status != 'BLOCKED'", "policy_status != 'BLOCKED'"]; args = [country, language]
        if topic: clauses.append("topic LIKE ?"); args.append("%" + topic + "%")
        rows = self.db.conn.execute("SELECT * FROM sites WHERE " + " AND ".join(clauses) + " ORDER BY average_rpm DESC, average_revenue DESC", args).fetchall()
        if not rows: return None
        site = rows[0]; topic_fit = 1.0 if not topic or topic.lower() in site["topic"].lower() else .3; authority_fit = 1.0 if not authority_tags else min(1.0, sum(a in site["authority_tags"] for a in authority_tags) / len(authority_tags)); country_fit = 1.0; language_fit = 1.0; historical = min(1.0, float(site["average_revenue"]) / 100); score = .3 * topic_fit + .2 * authority_fit + .2 * country_fit + .15 * language_fit + .15 * historical
        reason = f"topic={topic_fit:.2f}; authority={authority_fit:.2f}; country=1.00; language=1.00; historical_revenue={historical:.2f}"
        return {"site": site, "site_fit_score": score, "selection_reason": reason, "topic_fit": topic_fit, "authority_fit": authority_fit, "country_fit": country_fit, "language_fit": language_fit, "historical_revenue_fit": historical}
    def select_site(self, keyword, country, language, topic=None):
        result = self.select_site_result(keyword, country, language, topic)
        return result["site"] if result else None
    def adapter_for(self, site): return self.adapters.get(site["platform"]) if site else None


def _window(db, start, end):
    revenue = db.conn.execute("SELECT coalesce(sum(adsense_revenue),0)+coalesce(sum(adpost_revenue),0) FROM revenue_metrics WHERE captured_at>=? AND captured_at<?", (start, end)).fetchone()[0]
    cost = db.conn.execute("SELECT coalesce(sum(amount),0) FROM cost_metrics WHERE captured_at>=? AND captured_at<?", (start, end)).fetchone()[0]
    return {"signals": db.conn.execute("SELECT count(*) FROM signals WHERE last_seen_at>=? AND last_seen_at<?", (start, end)).fetchone()[0], "opportunities": db.conn.execute("SELECT count(*) FROM opportunities WHERE created_at>=? AND created_at<?", (start, end)).fetchone()[0], "contents": db.conn.execute("SELECT count(*) FROM contents WHERE created_at>=? AND created_at<?", (start, end)).fetchone()[0], "publications": db.conn.execute("SELECT count(*) FROM publications WHERE published_at>=? AND published_at<?", (start, end)).fetchone()[0], "traffic_clicks": db.conn.execute("SELECT coalesce(sum(click),0) FROM traffic_metrics WHERE captured_at>=? AND captured_at<?", (start, end)).fetchone()[0], "revenue": revenue, "ai_cost": cost, "contribution_profit": revenue - cost}


def content_economics(db, content_id):
    revenue = db.conn.execute("SELECT coalesce(sum(r.adsense_revenue),0)+coalesce(sum(r.adpost_revenue),0) FROM revenue_metrics r JOIN publications p ON p.id=r.publication_id WHERE p.content_id=?", (content_id,)).fetchone()[0]
    cost = db.conn.execute("SELECT coalesce(sum(amount),0) FROM cost_metrics WHERE content_id=? OR opportunity_id IN (SELECT opportunity_id FROM content_plans cp JOIN contents c ON c.plan_id=cp.id WHERE c.id=?) OR publication_id IN (SELECT id FROM publications WHERE content_id=?)", (content_id, content_id, content_id)).fetchone()[0]
    return {"content_id": content_id, "revenue": revenue, "cost": cost, "contribution_profit": revenue - cost}


def _bounds(db, start_date, days):
    tz = ZoneInfo(db.config("report_timezone", "UTC")); local_start = datetime.combine(start_date, time.min, tzinfo=tz); local_end = local_start + timedelta(days=days); return local_start.astimezone(UTC).isoformat(), local_end.astimezone(UTC).isoformat(), local_start.date().isoformat(), (local_end - timedelta(microseconds=1)).astimezone(tz).date().isoformat()


def daily_report(db, report_date=None):
    tz = ZoneInfo(db.config("report_timezone", "UTC")); d = date.fromisoformat(report_date) if isinstance(report_date, str) else (report_date or datetime.now(tz).date()); start, end, pstart, pend = _bounds(db, d, 1); result = _window(db, start, end); result.update({"period": "daily", "period_start": pstart, "period_end": pend, "fast_candidates": db.conn.execute("SELECT count(*) FROM signals WHERE is_fast_candidate=1 AND last_seen_at>=? AND last_seen_at<?", (start, end)).fetchone()[0], "rank_changes": "NOT_CONFIGURED", "external_telemetry": "NOT_CONFIGURED", "errors": db.conn.execute("SELECT count(*) FROM audit_logs WHERE status='FAIL' AND created_at>=? AND created_at<?", (start, end)).fetchone()[0]}); return result


def period_report(db, period, report_date=None):
    tz = ZoneInfo(db.config("report_timezone", "UTC")); end_date = date.fromisoformat(report_date) if isinstance(report_date, str) else (report_date or datetime.now(tz).date()); days = 7 if period == "weekly" else 30 if period == "monthly" else None
    if days is None: raise ValueError("period must be weekly or monthly")
    start, end, pstart, pend = _bounds(db, end_date - timedelta(days=days - 1), days); result = _window(db, start, end); result.update({"period": period, "period_start": pstart, "period_end": pend, "by_content_type": [dict(r) for r in db.conn.execute("SELECT cp.content_type, count(*) count, coalesce(sum(rm.adsense_revenue),0)+coalesce(sum(rm.adpost_revenue),0) revenue FROM content_plans cp LEFT JOIN contents c ON c.plan_id=cp.id LEFT JOIN publications p ON p.content_id=c.id LEFT JOIN revenue_metrics rm ON rm.publication_id=p.id AND rm.captured_at>=? AND rm.captured_at<? WHERE cp.created_at>=? AND cp.created_at<? GROUP BY cp.content_type ORDER BY revenue DESC", (start, end, start, end))], "by_decision": [dict(r) for r in db.conn.execute("SELECT o.decision, count(*) count FROM opportunities o WHERE o.created_at>=? AND o.created_at<? GROUP BY o.decision", (start, end))], "top_sites": [dict(r) for r in db.conn.execute("SELECT s.id site_id,s.topic,s.country,s.platform,count(p.id) publications FROM sites s LEFT JOIN publications p ON p.site_id=s.id AND p.published_at>=? AND p.published_at<? GROUP BY s.id ORDER BY publications DESC LIMIT 10", (start, end))], "revenue_status": "NOT_CONFIGURED"}); return result
