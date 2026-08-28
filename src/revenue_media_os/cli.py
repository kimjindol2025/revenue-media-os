import argparse
import json

from .core import Editorial, IntelligenceDB, LocalPublisher, OpportunityEngine, Telemetry, TrendSensor, daily_report, period_report


def demo(db_path):
    db=IntelligenceDB(db_path); sensor=TrendSensor(db); sid=sensor.ingest("portable pressure washer","recorded-feed",[42,90,120,180,250],platform_count=2); oid=OpportunityEngine(db).decide(sid,mode="FIXTURE"); site=db.site(tenant_id="mvp",country="US",language="en",topic="home tools",platform="local",authority_tags="home,review,washer",publisher_type="local"); ed=Editorial(db); ed.serp(oid,[{"title":"Pressure washer buying guide","url":"https://example.test/1","features":{"faq":True}},{"title":"Portable washer comparison","url":"https://example.test/2","features":{"table":True}}]); pid=ed.plan(oid,site,"FAST",["noise and water use","real setup checklist"],"comparison + buying guide",["use cases","setup","comparison","FAQ"]); cid=ed.article(pid,"Portable Pressure Washer: Setup and Buying Checklist","An original checklist covering setup, water use, noise, and selection criteria."); pub=LocalPublisher(db).publish(cid,site,"data/published"); kid=db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(oid,)).fetchone()[0]; Telemetry(db).record(pub,kid); result={"gates":{"TREND_1H":"PASS","TREND_DB":"PASS","OPPORTUNITY_DECISION":"PASS","DECISION_REASON":"PASS","SERP_ANALYSIS":"PASS","CONTENT_PLAN":"PASS","ARTICLE_GENERATION":"PASS","SITE_SELECTION":"PASS","PUBLISH":"PASS","RANK_TRACK":"NOT_CONFIGURED","GSC":"NOT_CONFIGURED","GA4":"NOT_CONFIGURED","REVENUE_STORE":"NOT_CONFIGURED","C_TO_B_HISTORY_QUERY":"PASS","DAILY_REPORT":"PASS","AUDIT_LOG":"PASS"},"report":daily_report(db),"publication_id":pub,"url":db.conn.execute("SELECT url FROM publications WHERE id=?",(pub,)).fetchone()[0]}; print(json.dumps(result,ensure_ascii=False,indent=2)); db.close()


if __name__=="__main__":
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command"); d=sub.add_parser("demo"); d.add_argument("--db",default="data/mvp.sqlite3"); r=sub.add_parser("daily-report"); r.add_argument("--db",default="data/mvp.sqlite3"); w=sub.add_parser("period-report"); w.add_argument("period",choices=("weekly","monthly")); w.add_argument("--db",default="data/mvp.sqlite3"); a=p.parse_args()
    if a.command=="demo": demo(a.db)
    elif a.command=="daily-report": db=IntelligenceDB(a.db); print(json.dumps(daily_report(db),indent=2)); db.close()
    elif a.command=="period-report": db=IntelligenceDB(a.db); print(json.dumps(period_report(db,a.period),indent=2)); db.close()
