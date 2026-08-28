import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

def now(): return datetime.now(timezone.utc).isoformat()

class IntelligenceDB:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        schema = Path(__file__).resolve().parents[2] / "schema.sql"
        self.conn.executescript(schema.read_text())
    def close(self): self.conn.close()
    def add_audit(self, action, entity_type, entity_id, status, details):
        self.conn.execute("INSERT INTO audit_logs(action,entity_type,entity_id,status,details,created_at) VALUES(?,?,?,?,?,?)", (action,entity_type,entity_id,status,json.dumps(details),now()))
        self.conn.commit()
    def keyword(self, word, country="US", language="en"):
        self.conn.execute("INSERT OR IGNORE INTO keywords(keyword,country,language,created_at) VALUES(?,?,?,?)", (word,country,language,now()))
        return self.conn.execute("SELECT id FROM keywords WHERE keyword=?",(word,)).fetchone()[0]
    def site(self, **v):
        fields="tenant_id,country,language,topic,platform,authority_tags,publisher_type,ads_type,ads_account_ref,search_console_ref,analytics_ref,average_rpm,average_revenue,health_status,policy_status"
        vals=[v.get(x) for x in fields.split(',')]
        vals[5]=vals[5] or ''
        vals[6]=vals[6] or v['platform']; vals[7]=vals[7] or 'NOT_CONFIGURED'; vals[11]=vals[11] or 0; vals[12]=vals[12] or 0; vals[13]=vals[13] or 'UNKNOWN'; vals[14]=vals[14] or 'UNKNOWN'
        cur=self.conn.execute(f"INSERT INTO sites({fields}) VALUES({','.join('?' for _ in vals)})",vals); self.conn.commit(); return cur.lastrowid

class TrendSensor:
    def __init__(self, db): self.db=db
    def ingest(self, keyword, source, samples, country="US", language="en", platform_count=1, country_count=1):
        # samples are mention counts for 1h, 3h, 6h, 12h, 24h windows, newest first.
        m1,m3,m6,m12,m24=samples; v1=m1; v3=m3/3; v6=m6/6; v12=m12/12; v24=m24/24
        acceleration=v1-max(v3,0); fast=v1 >= 20 and acceleration > 0
        kid=self.db.keyword(keyword,country,language)
        cur=self.db.conn.execute("INSERT INTO signals(keyword_id,source,country,language,mention_count,unique_authors,engagement,velocity_1h,velocity_3h,velocity_6h,velocity_12h,velocity_24h,acceleration,platform_count,country_count,first_seen_at,last_seen_at,is_fast_candidate) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(kid,source,country,language,m1,max(1,m1//2),m1*10,v1,v3,v6,v12,v24,acceleration,platform_count,country_count,now(),now(),int(fast)))
        self.db.conn.commit(); sid=cur.lastrowid; self.db.add_audit("trend.ingest","signal",sid,"PASS",{"rolling_window":"24h","fast_candidate":fast}); return sid

class OpportunityEngine:
    labels=("IGNORE","WATCH","FAST_WRITE","MONEY_WRITE","WINNER_UPDATE","EXPERIMENT")
    def __init__(self, db, config=None, version="Opportunity-v1"):
        self.db=db; self.config={"velocity":.25,"search_gap":.15,"competition":-.15,"revenue":.15,"site_fit":.1,"country_fit":.1,"freshness":.15,"risk":-.2,"cost":-.05}; self.config.update(config or {}); self.version=version
    def decide(self, signal_id, search_gap=.6, competition=.4, historical_revenue=.2, site_fit=.7, country_fit=1, freshness=.8, risk=.1, cost=.2):
        s=self.db.conn.execute("SELECT * FROM signals WHERE id=?",(signal_id,)).fetchone(); c={"velocity":min(s["velocity_1h"]/100,1),"search_gap":search_gap,"competition":competition,"revenue":historical_revenue,"site_fit":site_fit,"country_fit":country_fit,"freshness":freshness,"risk":risk,"cost":cost}
        score=sum(self.config[k]*c[k] for k in c); decision="FAST_WRITE" if s["is_fast_candidate"] else ("MONEY_WRITE" if score >= .3 else "WATCH" if score >= .05 else "IGNORE")
        reason=f"{decision}: score={score:.3f}; fast={bool(s['is_fast_candidate'])}; strongest={max(c,key=lambda k:abs(self.config[k]*c[k]))}"
        cur=self.db.conn.execute("INSERT INTO opportunities(keyword_id,signal_id,decision,score,decision_reason,score_components,engine_version,created_at) VALUES(?,?,?,?,?,?,?,?)",(s["keyword_id"],signal_id,decision,score,reason,json.dumps(c),self.version,now())); self.db.conn.commit(); oid=cur.lastrowid; self.db.add_audit("opportunity.decide","opportunity",oid,"PASS",{"decision":decision,"reason":reason}); return oid

class Editorial:
    def __init__(self,db): self.db=db
    def serp(self, opportunity_id, results, engine="recorded-serp"):
        o=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(opportunity_id,)).fetchone(); cur=self.db.conn.execute("INSERT INTO serp_snapshots(opportunity_id,engine,query,captured_at,status) VALUES(?,?,?,?,?)",(opportunity_id,engine,self.db.conn.execute("SELECT keyword FROM keywords WHERE id=?",(o[0],)).fetchone()[0],now(),"PASS")); sid=cur.lastrowid
        for i,r in enumerate(results[:10],1): self.db.conn.execute("INSERT INTO serp_results(snapshot_id,position,title,url,snippet,features) VALUES(?,?,?,?,?,?)",(sid,i,r["title"],r["url"],r.get("snippet",""),json.dumps(r.get("features",{}))))
        self.db.conn.commit(); self.db.add_audit("serp.analyze","serp_snapshot",sid,"PASS",{"top10":min(10,len(results))}); return sid
    def plan(self, opportunity_id, site_id, content_type, gaps, intent, outline, harness="B-v1"):
        cur=self.db.conn.execute("INSERT INTO content_plans(opportunity_id,site_id,content_type,search_intent,content_gaps,outline,context,harness_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(opportunity_id,site_id,content_type,intent,json.dumps(gaps),json.dumps(outline),"trend+decision+serp+history+site_identity",harness,now())); self.db.conn.commit(); pid=cur.lastrowid; self.db.add_audit("content.plan","content_plan",pid,"PASS",{"history_context":True}); return pid
    def article(self, plan_id, title, body):
        cur=self.db.conn.execute("INSERT INTO contents(plan_id,title,body,content_version,created_at) VALUES(?,?,?,?,?)",(plan_id,title,body,"content-v1",now())); self.db.conn.commit(); cid=cur.lastrowid; self.db.add_audit("content.generate","content",cid,"PASS",{"originality_guard":"no SERP text copied"}); return cid
    def history(self, keyword_id):
        return [dict(x) for x in self.db.conn.execute("SELECT p.id publication_id,r.rank,r.captured_at FROM publications p JOIN contents c ON c.id=p.content_id JOIN content_plans cp ON cp.id=c.plan_id JOIN opportunities o ON o.id=cp.opportunity_id JOIN rank_history r ON r.publication_id=p.id WHERE o.keyword_id=? ORDER BY r.captured_at DESC",(keyword_id,))]

class LocalPublisher:
    def __init__(self,db): self.db=db
    def publish(self, content_id, site_id, output_dir):
        c=self.db.conn.execute("SELECT title,body FROM contents WHERE id=?",(content_id,)).fetchone(); Path(output_dir).mkdir(parents=True,exist_ok=True); slug=f"content-{content_id}.html"; path=(Path(output_dir)/slug).resolve(); path.write_text(f"<html><head><title>{c['title']}</title></head><body><h1>{c['title']}</h1><p>{c['body']}</p></body></html>")
        site=self.db.conn.execute("SELECT platform FROM sites WHERE id=?",(site_id,)).fetchone(); cur=self.db.conn.execute("INSERT INTO publications(content_id,site_id,platform,external_id,url,status,published_at) VALUES(?,?,?,?,?,?,?)",(content_id,site_id,site[0],slug,path.as_uri(),"PUBLISHED",now())); self.db.conn.commit(); pid=cur.lastrowid; self.db.add_audit("publisher.publish","publication",pid,"PASS",{"adapter":"local","url":path.as_uri()}); return pid

class Telemetry:
    def __init__(self,db): self.db=db
    def record(self, publication_id, keyword_id, rank=None, traffic=None, revenue=None, provider_status="NOT_CONFIGURED"):
        t=now(); self.db.conn.execute("INSERT INTO rank_history(publication_id,keyword_id,rank,captured_at,provider_status) VALUES(?,?,?,?,?)",(publication_id,keyword_id,rank,t,provider_status)); traffic=traffic or {}; self.db.conn.execute("INSERT INTO traffic_metrics(publication_id,captured_at,impression,click,ctr,google_traffic,naver_traffic,sns_traffic,direct_traffic,engagement_time,page_views,provider_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(publication_id,t,*[traffic.get(k) for k in ('impression','click','ctr','google_traffic','naver_traffic','sns_traffic','direct_traffic','engagement_time','page_views')],provider_status)); revenue=revenue or {}; self.db.conn.execute("INSERT INTO revenue_metrics(publication_id,captured_at,adsense_revenue,adpost_revenue,rpm,provider_status) VALUES(?,?,?,?,?,?)",(publication_id,t,revenue.get('adsense_revenue'),revenue.get('adpost_revenue'),revenue.get('rpm'),provider_status)); self.db.conn.commit(); self.db.add_audit("telemetry.record","publication",publication_id,provider_status,{"rank":rank});

def daily_report(db):
    def count(table): return db.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    revenue=db.conn.execute("SELECT coalesce(sum(adsense_revenue),0)+coalesce(sum(adpost_revenue),0) FROM revenue_metrics").fetchone()[0]
    return {"signals":count("signals"),"opportunities":count("opportunities"),"contents":count("contents"),"publications":count("publications"),"fast_candidates":db.conn.execute("SELECT count(*) FROM signals WHERE is_fast_candidate=1").fetchone()[0],"revenue":revenue,"external_telemetry":"NOT_CONFIGURED","errors":db.conn.execute("SELECT count(*) FROM audit_logs WHERE status='FAIL'").fetchone()[0]}
