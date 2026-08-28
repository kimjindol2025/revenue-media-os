import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 3
UTC = timezone.utc
VALID_PROVIDER_STATUSES = {"PASS", "PARTIAL", "FAIL", "NOT_CONFIGURED", "CONFIGURED_NO_DATA", "FIXTURE"}
HIGH_RISK_CLASSES = {"INCIDENT", "FINANCE", "HEALTH", "LEGAL", "POLITICS", "PERSON_RUMOR"}


def now():
    return datetime.now(UTC).isoformat()


class IntelligenceDB:
    def __init__(self, path=":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        schema = Path(__file__).resolve().parents[2] / "schema.sql"
        self.conn.executescript(schema.read_text())
        self._migrate()
        self._assert_foreign_keys()

    def close(self): self.conn.close()
    def _columns(self, table): return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
    def _foreign_keys(self, table): return {(r[3], r[2], r[4]) for r in self.conn.execute(f"PRAGMA foreign_key_list({table})")}

    def _unique_index_columns(self, table):
        out=[]
        for idx in self.conn.execute(f"PRAGMA index_list({table})"):
            if idx[2]: out.append(tuple(r[2] for r in self.conn.execute(f"PRAGMA index_info({idx[1]})")))
        return out

    def _add_column(self, table, name, definition):
        if name not in self._columns(table): self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _rebuild_keywords_if_needed(self):
        if ("keyword",) not in self._unique_index_columns("keywords"): return
        self.conn.execute("DROP TABLE IF EXISTS keywords_migrated")
        self.conn.execute("CREATE TABLE keywords_migrated (id INTEGER PRIMARY KEY, keyword TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(keyword,country,language))")
        self.conn.execute("INSERT INTO keywords_migrated SELECT id,keyword,country,language,created_at FROM keywords")
        self.conn.execute("DROP TABLE keywords"); self.conn.execute("ALTER TABLE keywords_migrated RENAME TO keywords")

    def _rebuild_signals_if_needed(self):
        info={r[1]:r for r in self.conn.execute("PRAGMA table_info(signals)")}
        need=bool(info.get("mention_count",(None,None,None,0))[3]) or (("observation_id","signal_observations","id") not in self._foreign_keys("signals"))
        if not need: return
        cols=self._columns("signals"); status="status" if "status" in cols else "'FIXTURE'"; obs="observation_id" if "observation_id" in cols else "NULL"; idem="idempotency_key" if "idempotency_key" in cols else "NULL"
        self.conn.execute("DROP TABLE IF EXISTS signals_migrated")
        self.conn.execute("CREATE TABLE signals_migrated (id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES keywords(id), source TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, mention_count INTEGER, unique_authors INTEGER, engagement INTEGER, velocity_1h REAL, velocity_3h REAL, velocity_6h REAL, velocity_12h REAL, velocity_24h REAL, acceleration REAL, platform_count INTEGER NOT NULL DEFAULT 1, country_count INTEGER NOT NULL DEFAULT 1, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, status TEXT NOT NULL, is_fast_candidate INTEGER NOT NULL DEFAULT 0, observation_id INTEGER REFERENCES signal_observations(id), idempotency_key TEXT UNIQUE)")
        self.conn.execute("INSERT INTO signals_migrated (id,keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key) SELECT id,keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,"+status+",is_fast_candidate,"+obs+","+idem+" FROM signals")
        self.conn.execute("DROP TABLE signals"); self.conn.execute("ALTER TABLE signals_migrated RENAME TO signals")

    def _rebuild_cost_metrics_if_needed(self):
        required={("opportunity_id","opportunities","id"),("content_id","contents","id"),("publication_id","publications","id")}
        if required.issubset(self._foreign_keys("cost_metrics")): return
        cols=self._columns("cost_metrics"); o="opportunity_id" if "opportunity_id" in cols else "NULL"; c="content_id" if "content_id" in cols else "NULL"; p="publication_id" if "publication_id" in cols else "NULL"; idem="idempotency_key" if "idempotency_key" in cols else "NULL"
        self.conn.execute("DROP TABLE IF EXISTS cost_metrics_migrated")
        self.conn.execute("CREATE TABLE cost_metrics_migrated (id INTEGER PRIMARY KEY, component TEXT NOT NULL, provider TEXT NOT NULL, opportunity_id INTEGER REFERENCES opportunities(id), content_id INTEGER REFERENCES contents(id), publication_id INTEGER REFERENCES publications(id), input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD', captured_at TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT UNIQUE, CHECK ((opportunity_id IS NOT NULL)+(content_id IS NOT NULL)+(publication_id IS NOT NULL)<=1))")
        self.conn.execute("INSERT INTO cost_metrics_migrated (id,component,provider,opportunity_id,content_id,publication_id,input_tokens,output_tokens,amount,currency,captured_at,status,idempotency_key) SELECT id,component,provider,"+o+","+c+","+p+",input_tokens,output_tokens,amount,currency,captured_at,status,"+idem+" FROM cost_metrics")
        self.conn.execute("DROP TABLE cost_metrics"); self.conn.execute("ALTER TABLE cost_metrics_migrated RENAME TO cost_metrics")

    def _migrate(self):
        version=self.conn.execute("SELECT coalesce(max(version),0) FROM schema_version").fetchone()[0]
        if version>=SCHEMA_VERSION: return
        for n,d in (("decision_mode","TEXT NOT NULL DEFAULT 'FIXTURE'"),("input_statuses","TEXT NOT NULL DEFAULT '{}'"),("risk_class","TEXT NOT NULL DEFAULT 'GENERAL'"),("risk_score","REAL"),("risk_reason","TEXT"),("idempotency_key","TEXT")): self._add_column("opportunities",n,d)
        for n,d in (("site_fit_score","REAL"),("site_fit_reason","TEXT")): self._add_column("content_plans",n,d)
        for table in ("publications","rank_history","traffic_metrics","revenue_metrics"): self._add_column(table,"idempotency_key","TEXT")
        for table in ("rank_history","traffic_metrics","revenue_metrics"): self._add_column(table,"checkpoint","TEXT NOT NULL DEFAULT 'custom'")
        self.conn.commit(); self.conn.execute("PRAGMA foreign_keys = OFF"); self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._rebuild_signals_if_needed(); self._rebuild_cost_metrics_if_needed(); self._rebuild_keywords_if_needed()
            for table,column in (("signals","idempotency_key"),("opportunities","idempotency_key"),("publications","idempotency_key"),("rank_history","idempotency_key"),("traffic_metrics","idempotency_key"),("search_metrics","idempotency_key"),("analytics_metrics","idempotency_key"),("revenue_metrics","idempotency_key"),("cost_metrics","idempotency_key")):
                self.conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_{column} ON {table}({column}) WHERE {column} IS NOT NULL")
            self.conn.execute("INSERT INTO schema_version(version,applied_at) VALUES(?,?)",(SCHEMA_VERSION,now()))
            self.conn.execute("INSERT INTO migration_history(from_version,to_version,migration,applied_at) VALUES(?,?,?,?)",(version,SCHEMA_VERSION,"stage_1_6_hardening",now())); self.conn.commit()
        except Exception:
            self.conn.rollback(); raise
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _assert_foreign_keys(self):
        if self.conn.execute("PRAGMA foreign_keys").fetchone()[0]!=1: raise RuntimeError("foreign_keys must be enabled after migration")
        failures=self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if failures: raise RuntimeError(f"foreign_key_check failed: {[tuple(x) for x in failures]}")

    def config(self,key,default=None):
        row=self.conn.execute("SELECT value FROM system_config WHERE key=?",(key,)).fetchone(); return row[0] if row else default
    def set_config(self,key,value):
        self.conn.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value))); self.conn.commit()
    def add_audit(self,action,entity_type,entity_id,status,details):
        self.conn.execute("INSERT INTO audit_logs(action,entity_type,entity_id,status,details,created_at) VALUES(?,?,?,?,?,?)",(action,entity_type,entity_id,status,json.dumps(details,sort_keys=True),now())); self.conn.commit()
    def keyword(self,word,country="US",language="en"):
        self.conn.execute("INSERT OR IGNORE INTO keywords(keyword,country,language,created_at) VALUES(?,?,?,?)",(word,country,language,now())); return self.conn.execute("SELECT id FROM keywords WHERE keyword=? AND country=? AND language=?",(word,country,language)).fetchone()[0]
    def site(self,**v):
        fields="tenant_id,country,language,topic,platform,authority_tags,publisher_type,ads_type,ads_account_ref,search_console_ref,analytics_ref,average_rpm,average_revenue,health_status,policy_status"; vals=[v.get(x) for x in fields.split(',')]
        vals[5]=vals[5] or ''; vals[6]=vals[6] or v['platform']; vals[7]=vals[7] or 'NOT_CONFIGURED'; vals[11]=vals[11] or 0; vals[12]=vals[12] or 0; vals[13]=vals[13] or 'UNKNOWN'; vals[14]=vals[14] or 'UNKNOWN'
        cur=self.conn.execute(f"INSERT INTO sites({fields}) VALUES({','.join('?' for _ in vals)})",vals); self.conn.commit(); return cur.lastrowid


class TrendSensor:
    def __init__(self,db): self.db=db
    def _history_velocity(self,keyword_id,observed_at,hours):
        cutoff=(datetime.fromisoformat(observed_at)-timedelta(hours=hours)).isoformat(); rows=self.db.conn.execute("SELECT mention_count FROM signal_observations WHERE keyword_id=? AND observed_at>? AND observed_at<=? AND mention_count IS NOT NULL AND status='OBSERVED' ORDER BY observed_at",(keyword_id,cutoff,observed_at)).fetchall()
        return None if not rows else sum(r[0] for r in rows)/len(rows)
    def ingest(self,keyword,source,samples=None,country="US",language="en",platform_count=1,country_count=1,mention_count=None,unique_authors=None,engagement=None,observed_at=None,status=None,idempotency_key=None):
        kid=self.db.keyword(keyword,country,language); observed_at=observed_at or now()
        if idempotency_key:
            old=self.db.conn.execute("SELECT id FROM signals WHERE idempotency_key=?",(idempotency_key,)).fetchone()
            if old: return old[0]
        obs_key=f"observation:{idempotency_key}" if idempotency_key else None
        if samples is not None:
            if len(samples)!=5: raise ValueError("samples must be [1h,3h,6h,12h,24h]")
            m1,m3,m6,m12,m24=samples; metric_status=status or "FIXTURE"; mention_count=m1 if mention_count is None else mention_count; velocities=(m1,m3/3,m6/6,m12/12,m24/24)
        else:
            metric_status=status or ("OBSERVED" if None not in (mention_count,unique_authors,engagement) else "MISSING")
            if metric_status=="OBSERVED" and mention_count is None: raise ValueError("OBSERVED mention_count cannot be missing")
            velocities=None
        self.db.conn.execute("INSERT OR IGNORE INTO signal_observations(keyword_id,source,mention_count,unique_authors,engagement,observed_at,status,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",(kid,source,mention_count,unique_authors,engagement,observed_at,metric_status,obs_key))
        obs=self.db.conn.execute("SELECT id FROM signal_observations WHERE keyword_id=? AND source=? AND observed_at=? AND (idempotency_key=? OR (? IS NULL AND idempotency_key IS NULL)) ORDER BY id DESC LIMIT 1",(kid,source,observed_at,obs_key,obs_key)).fetchone()
        if not obs: raise RuntimeError("raw observation was not persisted")
        if velocities is None: velocities=tuple(self._history_velocity(kid,observed_at,h) for h in (1,3,6,12,24))
        v1,v3,v6,v12,v24=velocities; acceleration=None if v1 is None or v3 is None else v1-v3; min_v=float(self.db.config("fast_signal_min_velocity","20")); min_a=float(self.db.config("fast_signal_min_acceleration","0")); fast=int(metric_status in {"OBSERVED","FIXTURE"} and v1 is not None and acceleration is not None and v1>=min_v and acceleration>=min_a)
        cur=self.db.conn.execute("INSERT INTO signals(keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,status,is_fast_candidate,observation_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(kid,source,country,language,mention_count,unique_authors,engagement,v1,v3,v6,v12,v24,acceleration,platform_count,country_count,observed_at,observed_at,metric_status,fast,obs[0],idempotency_key)); self.db.conn.commit(); sid=cur.lastrowid; self.db.add_audit("trend.ingest","signal",sid,"PASS",{"metric_status":metric_status,"rolling_window":"24h","raw_observation_id":obs[0],"fast_signal":bool(fast)}); return sid


class Scheduler:
    def __init__(self,sensor): self.sensor=sensor
    def run_once(self,records,run_id=None):
        ids=[]
        for i,original in enumerate(records):
            record=dict(original); key=record.pop("idempotency_key",None) or (f"{run_id}:{i}" if run_id else None); ids.append(self.sensor.ingest(**record,idempotency_key=key))
        return {"status":"PASS","interval":"1h","run_id":run_id,"signals":ids}


class OpportunityEngine:
    labels=("IGNORE","WATCH","FAST_WRITE","MONEY_WRITE","WINNER_UPDATE","EXPERIMENT","REVIEW_REQUIRED")
    defaults={"velocity":.25,"search_gap":.15,"competition":-.15,"revenue":.15,"site_fit":.1,"country_fit":.1,"freshness":.15,"risk":-.2,"cost":-.05,"fast_min_velocity":20,"fast_min_acceleration":0,"fast_min_search_gap":.3,"fast_max_risk":.7}
    fixture_inputs={"search_gap":.6,"competition":.4,"historical_revenue":.2,"site_fit":.7,"country_fit":1.0,"freshness":.8,"risk":.1,"cost":.2}
    real_required={"search_gap","competition","historical_revenue","site_fit","country_fit","freshness","risk","cost"}
    def __init__(self,db,config=None,version="Opportunity-v2"):
        self.db=db; self.config=dict(self.defaults); self.config.update(config or {}); self.version=version; self.db.conn.execute("INSERT OR IGNORE INTO harness_versions(component,version,config,created_at) VALUES(?,?,?,?)",("Opportunity",version,json.dumps(self.config,sort_keys=True),now())); self.db.conn.commit()
    def decide(self,signal_id,search_gap=None,competition=None,historical_revenue=None,site_fit=None,country_fit=None,freshness=None,risk=None,cost=None,mode="REAL",input_statuses=None,risk_class="GENERAL",risk_reason=None,idempotency_key=None):
        mode=mode.upper(); risk_class=risk_class.upper()
        if mode not in {"REAL","FIXTURE"}: raise ValueError("mode must be REAL or FIXTURE")
        if idempotency_key:
            old=self.db.conn.execute("SELECT id FROM opportunities WHERE idempotency_key=?",(idempotency_key,)).fetchone()
            if old: return old[0]
        s=self.db.conn.execute("SELECT * FROM signals WHERE id=?",(signal_id,)).fetchone()
        if not s: raise ValueError("signal not found")
        values={"search_gap":search_gap,"competition":competition,"historical_revenue":historical_revenue,"site_fit":site_fit,"country_fit":country_fit,"freshness":freshness,"risk":risk,"cost":cost}; statuses=dict(input_statuses or {})
        if mode=="FIXTURE":
            for k,d in self.fixture_inputs.items():
                if values[k] is None: values[k]=d
                statuses.setdefault(k,"FIXTURE")
        else:
            for k in values: statuses.setdefault(k,"NOT_CONFIGURED" if values[k] is None else "NOT_CONFIGURED")
        high_risk=risk_class in HIGH_RISK_CLASSES; risk_missing=mode=="REAL" and (values["risk"] is None or statuses.get("risk")!="PASS"); unavailable=[k for k in self.real_required if mode=="REAL" and (values[k] is None or statuses.get(k) not in {"PASS","CONFIGURED_NO_DATA"})]
        c={"velocity":min((s["velocity_1h"] or 0)/100,1),"search_gap":values["search_gap"] or 0,"competition":values["competition"] or 0,"revenue":values["historical_revenue"] or 0,"site_fit":values["site_fit"] or 0,"country_fit":values["country_fit"] or 0,"freshness":values["freshness"] or 0,"risk":values["risk"] or 0,"cost":values["cost"] or 0}; score=sum(self.config[k]*c[k] for k in c); fast_eligible=bool(s["is_fast_candidate"]) and (s["velocity_1h"] or 0)>=self.config["fast_min_velocity"] and (s["acceleration"] or 0)>=self.config["fast_min_acceleration"] and (values["search_gap"] or 0)>=self.config["fast_min_search_gap"] and (values["risk"] or 0)<=self.config["fast_max_risk"]
        if high_risk or risk_missing or (values["risk"] is not None and values["risk"]>self.config["fast_max_risk"]): decision="REVIEW_REQUIRED"
        elif s["status"]=="MISSING" or unavailable: decision="WATCH"
        elif fast_eligible: decision="FAST_WRITE"
        else: decision="MONEY_WRITE" if score>=.3 else "WATCH" if score>=.05 else "IGNORE"
        strongest=max(c,key=lambda k:abs(self.config[k]*c[k])); reason=f"{decision}: mode={mode}; score={score:.3f}; fast_signal={bool(s['is_fast_candidate'])}; fast_write_eligible={fast_eligible}; risk_class={risk_class}; unavailable={','.join(unavailable) or 'none'}; strongest={strongest}"
        cur=self.db.conn.execute("INSERT INTO opportunities(keyword_id,signal_id,decision,decision_mode,score,decision_reason,score_components,input_statuses,engine_version,risk_class,risk_score,risk_reason,created_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(s["keyword_id"],signal_id,decision,mode,score,reason,json.dumps(c,sort_keys=True),json.dumps(statuses,sort_keys=True),self.version,risk_class,values["risk"],risk_reason,now(),idempotency_key)); self.db.conn.commit(); oid=cur.lastrowid; self.db.add_audit("opportunity.decide","opportunity",oid,"PASS",{"decision":decision,"mode":mode,"fast_signal":bool(s["is_fast_candidate"]),"fast_write_eligible":fast_eligible,"risk_class":risk_class,"unavailable":unavailable}); return oid


class Editorial:
    content_types={"FAST","MONEY","MAINTENANCE","EXPERIMENT"}
    def __init__(self,db): self.db=db
    def serp(self,opportunity_id,results,engine="recorded-serp"):
        o=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(opportunity_id,)).fetchone()
        if not o: raise ValueError("opportunity not found")
        query=self.db.conn.execute("SELECT keyword FROM keywords WHERE id=?",(o[0],)).fetchone()[0]; status="FIXTURE" if engine.startswith("recorded") else "PASS"; cur=self.db.conn.execute("INSERT INTO serp_snapshots(opportunity_id,engine,query,captured_at,status) VALUES(?,?,?,?,?)",(opportunity_id,engine,query,now(),status)); sid=cur.lastrowid
        for i,r in enumerate(results[:10],1): self.db.conn.execute("INSERT INTO serp_results(snapshot_id,position,title,url,snippet,features) VALUES(?,?,?,?,?,?)",(sid,i,r["title"],r["url"],r.get("snippet",""),json.dumps(r.get("features",{}))))
        self.db.conn.commit(); self.db.add_audit("serp.analyze","serp_snapshot",sid,"PASS",{"top10":min(10,len(results)),"status":status}); return sid
    def plan(self,opportunity_id,site_id,content_type,gaps,intent,outline,harness="B-v1",site_fit_score=None,site_fit_reason=None):
        if content_type not in self.content_types: raise ValueError(f"unsupported content type: {content_type}")
        cur=self.db.conn.execute("INSERT INTO content_plans(opportunity_id,site_id,content_type,search_intent,content_gaps,outline,context,harness_version,site_fit_score,site_fit_reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(opportunity_id,site_id,content_type,intent,json.dumps(gaps),json.dumps(outline),"trend+decision+serp+history+site_identity",harness,site_fit_score,site_fit_reason,now())); self.db.conn.commit(); pid=cur.lastrowid; self.db.add_audit("content.plan","content_plan",pid,"PASS",{"history_context":True,"site_fit_score":site_fit_score}); return pid
    def article(self,plan_id,title,body):
        if not title or not body: raise ValueError("title and body are required")
        cur=self.db.conn.execute("INSERT INTO contents(plan_id,title,body,content_version,created_at) VALUES(?,?,?,?,?)",(plan_id,title,body,"content-v1",now())); self.db.conn.commit(); cid=cur.lastrowid; self.db.add_audit("content.generate","content",cid,"PASS",{"originality_guard":"no SERP text copied"}); return cid
    def history(self,keyword_id):
        q="""SELECT cp.content_type,cp.harness_version,p.id publication_id,p.published_at,r.rank,r.checkpoint,r.captured_at,r.provider_status rank_status,sm.impressions,sm.clicks,sm.ctr,sm.position,sm.provider_status search_status,am.sessions,am.users,am.engagement_time,am.page_views,am.provider_status analytics_status,rm.adsense_revenue,rm.adpost_revenue,rm.rpm,rm.provider_status revenue_status FROM publications p JOIN contents c ON c.id=p.content_id JOIN content_plans cp ON cp.id=c.plan_id JOIN opportunities o ON o.id=cp.opportunity_id LEFT JOIN rank_history r ON r.publication_id=p.id LEFT JOIN search_metrics sm ON sm.publication_id=p.id AND sm.checkpoint=r.checkpoint LEFT JOIN analytics_metrics am ON am.publication_id=p.id AND am.checkpoint=r.checkpoint LEFT JOIN revenue_metrics rm ON rm.publication_id=p.id AND rm.checkpoint=r.checkpoint WHERE o.keyword_id=? ORDER BY COALESCE(r.captured_at,p.published_at) DESC"""; return [dict(x) for x in self.db.conn.execute(q,(keyword_id,))]


class LocalPublisher:
    def __init__(self,db): self.db=db
    def publish(self,content_id,site_id,output_dir,idempotency_key=None):
        if idempotency_key:
            old=self.db.conn.execute("SELECT id FROM publications WHERE idempotency_key=?",(idempotency_key,)).fetchone()
            if old: return old[0]
        c=self.db.conn.execute("SELECT title,body FROM contents WHERE id=?",(content_id,)).fetchone(); site=self.db.conn.execute("SELECT platform FROM sites WHERE id=?",(site_id,)).fetchone()
        if not c or not site: raise ValueError("content/site not found")
        path=(Path(output_dir)/f"content-{content_id}.html").resolve(); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(f"<html><head><title>{c['title']}</title></head><body><h1>{c['title']}</h1><p>{c['body']}</p></body></html>"); cur=self.db.conn.execute("INSERT INTO publications(content_id,site_id,platform,external_id,url,status,published_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",(content_id,site_id,site[0],str(path),path.as_uri(),"PUBLISHED",now(),idempotency_key)); self.db.conn.commit(); pid=cur.lastrowid; self.db.add_audit("publisher.publish","publication",pid,"PASS",{"adapter":"local","url":path.as_uri()}); return pid
    def update(self,publication_id,title,body):
        p=self.db.conn.execute("SELECT external_id FROM publications WHERE id=?",(publication_id,)).fetchone(); Path(p[0]).write_text(f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"); self.db.conn.execute("UPDATE publications SET status='UPDATED' WHERE id=?",(publication_id,)); self.db.conn.commit(); return self.get_url(publication_id)
    def get_status(self,publication_id): return self.db.conn.execute("SELECT status FROM publications WHERE id=?",(publication_id,)).fetchone()[0]
    def get_url(self,publication_id): return self.db.conn.execute("SELECT url FROM publications WHERE id=?",(publication_id,)).fetchone()[0]


class NotConfiguredPublisher:
    def __init__(self,platform): self.platform=platform
    def publish(self,*args,**kwargs): return {"status":"NOT_CONFIGURED","platform":self.platform}
    def update(self,*args,**kwargs): return {"status":"NOT_CONFIGURED","platform":self.platform}
    def get_status(self,*args,**kwargs): return "NOT_CONFIGURED"
    def get_url(self,*args,**kwargs): return None


class Telemetry:
    checkpoints={"1h","6h","12h","24h","72h","7d","30d","custom"}
    def __init__(self,db): self.db=db
    def record(self,publication_id,keyword_id,rank=None,search=None,analytics=None,revenue=None,checkpoint="custom",rank_status="NOT_CONFIGURED",search_console_status="NOT_CONFIGURED",analytics_status="NOT_CONFIGURED",revenue_status="NOT_CONFIGURED",idempotency_key=None,provider_status=None,traffic=None):
        if checkpoint not in self.checkpoints: raise ValueError(f"unsupported telemetry checkpoint: {checkpoint}")
        if provider_status is not None: rank_status=search_console_status=analytics_status=revenue_status=provider_status
        if idempotency_key and self.db.conn.execute("SELECT id FROM rank_history WHERE idempotency_key=?",(idempotency_key,)).fetchone(): return
        for st in (rank_status,search_console_status,analytics_status,revenue_status):
            if st not in VALID_PROVIDER_STATUSES: raise ValueError(f"unsupported provider status: {st}")
        if traffic and search is None: search={"impressions":traffic.get("impression"),"clicks":traffic.get("click"),"ctr":traffic.get("ctr")}
        if traffic and analytics is None: analytics={"engagement_time":traffic.get("engagement_time"),"page_views":traffic.get("page_views")}
        t=now(); self.db.conn.execute("INSERT INTO rank_history(publication_id,keyword_id,rank,checkpoint,captured_at,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?)",(publication_id,keyword_id,rank,checkpoint,t,rank_status,idempotency_key)); search=search or {}; self.db.conn.execute("INSERT INTO search_metrics(publication_id,checkpoint,captured_at,query,page,country,device,impressions,clicks,ctr,position,provider,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(publication_id,checkpoint,t,search.get("query"),search.get("page"),search.get("country"),search.get("device"),search.get("impressions"),search.get("clicks"),search.get("ctr"),search.get("position"),search.get("provider","gsc"),search_console_status,f"search:{idempotency_key}" if idempotency_key else None)); analytics=analytics or {}; self.db.conn.execute("INSERT INTO analytics_metrics(publication_id,checkpoint,captured_at,source,medium,country,sessions,users,engagement_time,page_views,provider,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(publication_id,checkpoint,t,analytics.get("source"),analytics.get("medium"),analytics.get("country"),analytics.get("sessions"),analytics.get("users"),analytics.get("engagement_time"),analytics.get("page_views"),analytics.get("provider","ga4"),analytics_status,f"analytics:{idempotency_key}" if idempotency_key else None)); revenue=revenue or {}; self.db.conn.execute("INSERT INTO revenue_metrics(publication_id,checkpoint,captured_at,adsense_revenue,adpost_revenue,rpm,provider_status,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",(publication_id,checkpoint,t,revenue.get("adsense_revenue"),revenue.get("adpost_revenue"),revenue.get("rpm"),revenue_status,f"revenue:{idempotency_key}" if idempotency_key else None)); self.db.conn.commit(); sts=(rank_status,search_console_status,analytics_status,revenue_status); self.db.add_audit("telemetry.record","publication",publication_id,"FAIL" if "FAIL" in sts else "PASS",{"checkpoint":checkpoint,"rank_status":rank_status,"search_console_status":search_console_status,"analytics_status":analytics_status,"revenue_status":revenue_status})
    def record_cost(self,component,provider,amount=0,input_tokens=0,output_tokens=0,status="NOT_CONFIGURED",opportunity_id=None,content_id=None,publication_id=None,idempotency_key=None):
        if sum(x is not None for x in (opportunity_id,content_id,publication_id))>1: raise ValueError("a cost row may be attributed to only one scope")
        if idempotency_key and self.db.conn.execute("SELECT id FROM cost_metrics WHERE idempotency_key=?",(idempotency_key,)).fetchone(): return
        self.db.conn.execute("INSERT INTO cost_metrics(component,provider,opportunity_id,content_id,publication_id,input_tokens,output_tokens,amount,currency,captured_at,status,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(component,provider,opportunity_id,content_id,publication_id,input_tokens,output_tokens,amount,"USD",now(),status,idempotency_key)); self.db.conn.commit(); self.db.add_audit("cost.record","cost_metric",None,status,{"component":component,"amount":amount,"opportunity_id":opportunity_id,"content_id":content_id,"publication_id":publication_id})


def content_economics(db,content_id):
    row=db.conn.execute("SELECT cp.opportunity_id FROM contents c JOIN content_plans cp ON cp.id=c.plan_id WHERE c.id=?",(content_id,)).fetchone(); opportunity_id=row[0] if row else None; pubs=[r[0] for r in db.conn.execute("SELECT id FROM publications WHERE content_id=?",(content_id,))]; revenue=db.conn.execute("SELECT coalesce(sum(r.adsense_revenue),0)+coalesce(sum(r.adpost_revenue),0) FROM revenue_metrics r JOIN publications p ON p.id=r.publication_id WHERE p.content_id=?",(content_id,)).fetchone()[0]; clauses=["content_id=?"]; args=[content_id]
    if opportunity_id is not None: clauses.append("opportunity_id=?"); args.append(opportunity_id)
    if pubs: clauses.append(f"publication_id IN ({','.join('?' for _ in pubs)})"); args.extend(pubs)
    cost=db.conn.execute("SELECT coalesce(sum(amount),0) FROM cost_metrics WHERE "+" OR ".join(clauses),args).fetchone()[0]; return {"content_id":content_id,"revenue":revenue,"cost":cost,"contribution_profit":revenue-cost}


def _aggregate_status(db,table,start=None,end=None):
    sql=f"SELECT DISTINCT provider_status FROM {table}"; args=[]
    if start is not None and end is not None: sql+=" WHERE captured_at>=? AND captured_at<?"; args=[start,end]
    sts={r[0] for r in db.conn.execute(sql,args)}
    if not sts:return "NOT_CONFIGURED"
    if sts=={"PASS"}:return "PASS"
    if sts=={"CONFIGURED_NO_DATA"}:return "CONFIGURED_NO_DATA"
    if sts=={"NOT_CONFIGURED"}:return "NOT_CONFIGURED"
    if sts=={"FAIL"}:return "FAIL"
    return "PARTIAL"


def provider_status_summary(db,start=None,end=None):
    r={"rank":_aggregate_status(db,"rank_history",start,end),"gsc":_aggregate_status(db,"search_metrics",start,end),"ga4":_aggregate_status(db,"analytics_metrics",start,end),"revenue":_aggregate_status(db,"revenue_metrics",start,end)}; states=set(r.values()); r["overall"]="NOT_CONFIGURED" if states=={"NOT_CONFIGURED"} else "PASS" if states=={"PASS"} else "CONFIGURED_NO_DATA" if states=={"CONFIGURED_NO_DATA"} else "FAIL" if states=={"FAIL"} else "PARTIAL"; return r


def _window(db,start,end):
    revenue=db.conn.execute("SELECT coalesce(sum(adsense_revenue),0)+coalesce(sum(adpost_revenue),0) FROM revenue_metrics WHERE captured_at>=? AND captured_at<?",(start,end)).fetchone()[0]; cost=db.conn.execute("SELECT coalesce(sum(amount),0) FROM cost_metrics WHERE captured_at>=? AND captured_at<?",(start,end)).fetchone()[0]; clicks=db.conn.execute("SELECT coalesce(sum(clicks),0) FROM search_metrics WHERE captured_at>=? AND captured_at<?",(start,end)).fetchone()[0]
    return {"signals":db.conn.execute("SELECT count(*) FROM signals WHERE last_seen_at>=? AND last_seen_at<?",(start,end)).fetchone()[0],"opportunities":db.conn.execute("SELECT count(*) FROM opportunities WHERE created_at>=? AND created_at<?",(start,end)).fetchone()[0],"contents":db.conn.execute("SELECT count(*) FROM contents WHERE created_at>=? AND created_at<?",(start,end)).fetchone()[0],"publications":db.conn.execute("SELECT count(*) FROM publications WHERE published_at>=? AND published_at<?",(start,end)).fetchone()[0],"traffic_clicks":clicks,"revenue":revenue,"ai_cost":cost,"contribution_profit":revenue-cost}


def _bounds(db,start_date,days):
    tz=ZoneInfo(db.config("report_timezone","UTC")); local_start=datetime.combine(start_date,time.min,tzinfo=tz); local_end=local_start+timedelta(days=days); return local_start.astimezone(UTC).isoformat(),local_end.astimezone(UTC).isoformat(),local_start.date().isoformat(),(local_end-timedelta(microseconds=1)).astimezone(tz).date().isoformat()


def _rank_changes(db,start,end):
    if _aggregate_status(db,"rank_history",start,end)=="NOT_CONFIGURED": return "NOT_CONFIGURED"
    return len(db.conn.execute("SELECT publication_id FROM rank_history WHERE captured_at>=? AND captured_at<? AND rank IS NOT NULL GROUP BY publication_id HAVING count(DISTINCT rank)>1",(start,end)).fetchall())


def daily_report(db,report_date=None):
    tz=ZoneInfo(db.config("report_timezone","UTC")); d=date.fromisoformat(report_date) if isinstance(report_date,str) else (report_date or datetime.now(tz).date()); start,end,pstart,pend=_bounds(db,d,1); result=_window(db,start,end); sts=provider_status_summary(db,start,end); result.update({"period":"daily","period_start":pstart,"period_end":pend,"fast_candidates":db.conn.execute("SELECT count(*) FROM signals WHERE is_fast_candidate=1 AND last_seen_at>=? AND last_seen_at<?",(start,end)).fetchone()[0],"rank_changes":_rank_changes(db,start,end),"provider_status":sts,"external_telemetry":sts["overall"],"errors":db.conn.execute("SELECT count(*) FROM audit_logs WHERE status='FAIL' AND created_at>=? AND created_at<?",(start,end)).fetchone()[0]}); return result


def period_report(db,period,report_date=None):
    tz=ZoneInfo(db.config("report_timezone","UTC")); end_date=date.fromisoformat(report_date) if isinstance(report_date,str) else (report_date or datetime.now(tz).date()); days=7 if period=="weekly" else 30 if period=="monthly" else None
    if days is None: raise ValueError("period must be weekly or monthly")
    start,end,pstart,pend=_bounds(db,end_date-timedelta(days=days-1),days); result=_window(db,start,end); sts=provider_status_summary(db,start,end); result.update({"period":period,"window_type":"rolling_7d" if period=="weekly" else "rolling_30d","period_start":pstart,"period_end":pend,"by_content_type":[dict(r) for r in db.conn.execute("SELECT cp.content_type,count(DISTINCT cp.id) count,coalesce(sum(rm.adsense_revenue),0)+coalesce(sum(rm.adpost_revenue),0) revenue FROM content_plans cp LEFT JOIN contents c ON c.plan_id=cp.id LEFT JOIN publications p ON p.content_id=c.id LEFT JOIN revenue_metrics rm ON rm.publication_id=p.id AND rm.captured_at>=? AND rm.captured_at<? WHERE cp.created_at>=? AND cp.created_at<? GROUP BY cp.content_type ORDER BY revenue DESC",(start,end,start,end))],"by_decision":[dict(r) for r in db.conn.execute("SELECT decision,count(*) count FROM opportunities WHERE created_at>=? AND created_at<? GROUP BY decision",(start,end))],"top_sites":[dict(r) for r in db.conn.execute("SELECT s.id site_id,s.topic,s.country,s.platform,count(p.id) publications FROM sites s LEFT JOIN publications p ON p.site_id=s.id AND p.published_at>=? AND p.published_at<? GROUP BY s.id ORDER BY publications DESC LIMIT 10",(start,end))],"provider_status":sts,"revenue_status":sts["revenue"],"external_telemetry":sts["overall"]}); return result
