import json, sqlite3, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from revenue_media_os.core import *
from revenue_media_os.providers import GoogleAPIProvider, OpenSERPProvider, PublisherRouter, WordPressPublisher, ProviderResult

class MvpTest(unittest.TestCase):
    def setUp(self): self.db=IntelligenceDB()
    def tearDown(self): self.db.close()
    def test_complete_relationship_and_fast_candidate(self):
        sid=TrendSensor(self.db).ingest("test topic","fixture",[50,90,120,160,200],platform_count=2,country_count=2)
        oid=OpportunityEngine(self.db).decide(sid); site=self.db.site(tenant_id="t",country="US",language="en",topic="test",platform="local")
        e=Editorial(self.db); e.serp(oid,[{"title":"one","url":"https://one"}]); plan=e.plan(oid,site,"FAST",["gap"],"informational",["answer"]); content=e.article(plan,"Title","Original body"); pub=LocalPublisher(self.db).publish(content,site,tempfile.mkdtemp()); kid=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(oid,)).fetchone()[0]; Telemetry(self.db).record(pub,kid,provider_status="NOT_CONFIGURED")
        chain=self.db.conn.execute("SELECT count(*) FROM signals s JOIN opportunities o ON o.signal_id=s.id JOIN content_plans cp ON cp.opportunity_id=o.id JOIN contents c ON c.plan_id=cp.id JOIN publications p ON p.content_id=c.id JOIN rank_history r ON r.publication_id=p.id JOIN traffic_metrics t ON t.publication_id=p.id JOIN revenue_metrics m ON m.publication_id=p.id").fetchone()[0]
        self.assertEqual(chain,1); self.assertEqual(self.db.conn.execute("SELECT is_fast_candidate FROM signals WHERE id=?",(sid,)).fetchone()[0],1); self.assertEqual(self.db.conn.execute("SELECT decision_reason FROM opportunities WHERE id=?",(oid,)).fetchone()[0][:4],"FAST"); self.assertEqual(len(e.history(kid)),1)
    def test_configurable_scoring_and_explicit_external_status(self):
        sid=TrendSensor(self.db).ingest("slow","fixture",[1,3,6,12,24]); oid=OpportunityEngine(self.db,{"velocity":1,"search_gap":0,"competition":0,"revenue":0,"site_fit":0,"country_fit":0,"freshness":0,"risk":0,"cost":0}).decide(sid); self.assertIn(self.db.conn.execute("SELECT decision FROM opportunities WHERE id=?",(oid,)).fetchone()[0],("WATCH","IGNORE")); self.assertEqual(daily_report(self.db)["external_telemetry"],"NOT_CONFIGURED")
        self.assertEqual(NotConfiguredPublisher("wordpress").get_status(),"NOT_CONFIGURED")

    def test_hourly_scheduler_and_cost_are_persisted(self):
        result=Scheduler(TrendSensor(self.db)).run_once([{"keyword":"scheduled","source":"fixture","samples":[2,5,8,12,20]}]); self.assertEqual(result["interval"],"1h"); self.assertEqual(len(result["signals"]),1)
        Telemetry(self.db).record_cost("article","local",amount=0.12,input_tokens=10,output_tokens=20,status="PASS")
        self.assertEqual(daily_report(self.db)["ai_cost"],0.12)

    def test_external_adapters_are_explicitly_unconfigured(self):
        self.assertEqual(OpenSERPProvider().search("x").status,"NOT_CONFIGURED")
        self.assertEqual(WordPressPublisher().publish("x","y").status,"NOT_CONFIGURED")
        self.assertEqual(GoogleAPIProvider(None).query("reports").status,"NOT_CONFIGURED")
        site=self.db.site(tenant_id="t",country="US",language="en",topic="tools",platform="local",average_rpm=4)
        self.assertEqual(PublisherRouter(self.db).select_site("x","US","en","tools")["id"],site)

    def test_telemetry_checkpoints_and_period_report(self):
        sid=TrendSensor(self.db).ingest("checkpoint","fixture",[2,4,6,8,10]); oid=OpportunityEngine(self.db).decide(sid); site=self.db.site(tenant_id="t",country="US",language="en",topic="x",platform="local"); e=Editorial(self.db); plan=e.plan(oid,site,"EXPERIMENT",[],"informational",[]); cid=e.article(plan,"x","y"); pub=LocalPublisher(self.db).publish(cid,site,tempfile.mkdtemp()); kid=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(oid,)).fetchone()[0]; Telemetry(self.db).record(pub,kid,checkpoint="7d"); self.assertEqual(self.db.conn.execute("SELECT checkpoint FROM rank_history").fetchone()[0],"7d"); self.assertEqual(period_report(self.db,"weekly")["period"],"weekly")

    def test_keyword_country_language_key(self):
        ids={self.db.keyword("GPT",c,l) for c,l in (("US","en"),("KR","ko"),("JP","ja"))}; self.assertEqual(len(ids),3)

    def test_real_metric_no_fabrication(self):
        sid=TrendSensor(self.db).ingest("observed","api",mention_count=9,unique_authors=4,engagement=77,observed_at="2026-08-28T00:00:00+00:00",status="OBSERVED"); s=self.db.conn.execute("SELECT * FROM signals WHERE id=?",(sid,)).fetchone(); self.assertEqual((s["unique_authors"],s["engagement"],s["status"]),(4,77,"OBSERVED"))

    def test_raw_observation_history(self):
        kid=self.db.keyword("history","US","en"); TrendSensor(self.db).ingest("history","api",mention_count=10,unique_authors=2,engagement=5,observed_at="2026-08-28T00:00:00+00:00",status="OBSERVED"); self.assertEqual(self.db.conn.execute("SELECT count(*) FROM signal_observations WHERE keyword_id=?",(kid,)).fetchone()[0],1)

    def test_real_velocity_1h_3h_acceleration_and_time_boundary(self):
        sensor=TrendSensor(self.db); sensor.ingest("velocity","api",mention_count=0,unique_authors=0,engagement=0,observed_at="2026-08-28T08:00:00+00:00",status="OBSERVED"); sensor.ingest("velocity","api",mention_count=30,unique_authors=3,engagement=30,observed_at="2026-08-28T09:00:00+00:00",status="OBSERVED"); sid=sensor.ingest("velocity","api",mention_count=10,unique_authors=1,engagement=10,observed_at="2026-08-28T10:00:00+00:00",status="OBSERVED"); s=self.db.conn.execute("SELECT velocity_1h,velocity_3h,acceleration FROM signals WHERE id=?",(sid,)).fetchone(); self.assertAlmostEqual(s["velocity_1h"],10); self.assertAlmostEqual(s["velocity_3h"],40/3); self.assertAlmostEqual(s["acceleration"],10-(40/3))

    def test_hourly_aggregation_normalizes_provider_timestamps(self):
        sensor=TrendSensor(self.db, {"fast_signal_min_mentions":10, "fast_signal_min_velocity":0, "fast_signal_min_acceleration":0})
        sensor.ingest("hourly", "api", mention_count=0, unique_authors=0, engagement=0, observed_at="2026-08-28T08:00:00+00:00", status="OBSERVED")
        sensor.ingest("hourly", "api", mention_count=0, unique_authors=0, engagement=0, observed_at="2026-08-28T09:00:00+00:00", status="OBSERVED")
        for stamp, count in (("2026-08-28T10:03:00+00:00", 2), ("2026-08-28T10:21:00+00:00", 3), ("2026-08-28T10:49:00+00:00", 5)):
            sid=sensor.ingest("hourly", "api", mention_count=count, unique_authors=1, engagement=count, observed_at=stamp, status="OBSERVED")
        row=self.db.conn.execute("SELECT velocity_1h FROM signals WHERE id=?", (sid,)).fetchone()
        bucket=self.db.conn.execute("SELECT bucket_start,bucket_end FROM signal_observations ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((bucket["bucket_start"], bucket["bucket_end"]), ("2026-08-28T10:00:00+00:00", "2026-08-28T11:00:00+00:00"))
        self.assertEqual(row["velocity_1h"], 10)
        self.assertEqual(self.db.conn.execute("SELECT trend_state FROM signals WHERE id=?", (sid,)).fetchone()[0], "FAST_SIGNAL")

    def test_missing_zero_separation_and_real_decision_boundary(self):
        sensor=TrendSensor(self.db); zero=sensor.ingest("zero","api",mention_count=0,unique_authors=0,engagement=0,observed_at="2026-08-28T00:00:00+00:00",status="OBSERVED"); missing=sensor.ingest("missing","api",observed_at="2026-08-28T00:00:00+00:00"); z=self.db.conn.execute("SELECT mention_count,unique_authors,engagement,status FROM signals WHERE id=?",(zero,)).fetchone(); m=self.db.conn.execute("SELECT mention_count,unique_authors,engagement,status FROM signals WHERE id=?",(missing,)).fetchone(); self.assertEqual(tuple(z),(0,0,0,"OBSERVED")); self.assertEqual(tuple(m),(None,None,None,"MISSING")); oid=OpportunityEngine(self.db).decide(zero); self.assertEqual(self.db.conn.execute("SELECT decision FROM opportunities WHERE id=?",(oid,)).fetchone()[0],"REVIEW_REQUIRED")

    def test_fixture_real_separation(self):
        fixture=TrendSensor(self.db).ingest("fixture","recorded",[50,90,120,160,200]); real=TrendSensor(self.db).ingest("real","api",mention_count=50,unique_authors=4,engagement=8,observed_at="2026-08-28T00:00:00+00:00",status="OBSERVED"); fo=OpportunityEngine(self.db).decide(fixture); ro=OpportunityEngine(self.db).decide(real,search_gap=1,competition=.1,historical_revenue=.4,site_fit=.8,country_fit=1,freshness=.9,risk=.1,cost=.1,input_statuses={k:"REAL" for k in ("search_gap","competition","history","site_fit","country_fit","freshness","risk","cost")}); self.assertEqual(self.db.conn.execute("SELECT decision_mode FROM opportunities WHERE id=?",(fo,)).fetchone()[0],"FIXTURE"); self.assertEqual(self.db.conn.execute("SELECT decision_mode FROM opportunities WHERE id=?",(ro,)).fetchone()[0],"REAL")

    def test_fast_harness_config_and_risk_veto(self):
        sid=TrendSensor(self.db).ingest("fast","fixture",[50,90,120,160,200]); oid=OpportunityEngine(self.db,{"fast_min_velocity":100,"fast_max_risk":.2}).decide(sid,risk=.9,search_gap=1); row=self.db.conn.execute("SELECT decision FROM opportunities WHERE id=?",(oid,)).fetchone(); self.assertEqual(row[0],"REVIEW_REQUIRED")

    def test_real_provenance_and_unknown_risk_guard(self):
        sid=TrendSensor(self.db).ingest("provenance", "api", mention_count=20, unique_authors=4, engagement=8, observed_at="2026-08-28T00:00:00+00:00", status="OBSERVED")
        captured="2026-08-28T00:05:00+00:00"
        provenance={key:{"value":value,"status":"REAL","source":"test-provider","captured_at":captured} for key,value in {"search_gap":1,"competition":.1,"history":.4,"site_fit":.8,"country_fit":1,"freshness":.9,"risk":.1,"cost":.1}.items()}
        oid=OpportunityEngine(self.db).decide(sid, search_gap=1, competition=.1, historical_revenue=.4, site_fit=.8, country_fit=1, freshness=.9, risk=.1, cost=.1, provenance=provenance, risk_class="unknown")
        stored=json.loads(self.db.conn.execute("SELECT input_provenance FROM opportunities WHERE id=?",(oid,)).fetchone()[0])
        self.assertEqual(stored["search_gap"], provenance["search_gap"])
        self.assertEqual(self.db.conn.execute("SELECT decision FROM opportunities WHERE id=?",(oid,)).fetchone()[0], "REVIEW_REQUIRED")

    def test_real_risk_veto_overrides_fast_score(self):
        sid=TrendSensor(self.db).ingest("risky", "api", mention_count=30, unique_authors=5, engagement=10, observed_at="2026-08-28T00:00:00+00:00", status="OBSERVED")
        captured="2026-08-28T00:05:00+00:00"
        provenance={key:{"value":value,"status":"REAL","source":"test-provider","captured_at":captured} for key,value in {"search_gap":1,"competition":.1,"history":.4,"site_fit":.8,"country_fit":1,"freshness":.9,"risk":.9,"cost":.1}.items()}
        oid=OpportunityEngine(self.db).decide(sid, search_gap=1, competition=.1, historical_revenue=.4, site_fit=.8, country_fit=1, freshness=.9, risk=.9, cost=.1, provenance=provenance, risk_class="finance")
        self.assertEqual(self.db.conn.execute("SELECT decision FROM opportunities WHERE id=?",(oid,)).fetchone()[0], "REVIEW_REQUIRED")

    def test_provenance_value_mismatch_blocks_real_decision(self):
        sid=TrendSensor(self.db).ingest("bad provenance", "api", mention_count=20, unique_authors=4, engagement=8, observed_at="2026-08-28T00:00:00+00:00", status="OBSERVED")
        captured="2026-08-28T00:05:00+00:00"
        provenance={key:{"value":value,"status":"REAL","source":"test-provider","captured_at":captured} for key,value in {"search_gap":.2,"competition":.1,"history":.4,"site_fit":.8,"country_fit":1,"freshness":.9,"risk":.1,"cost":.1}.items()}
        provenance["search_gap"]["value"]=.9
        oid=OpportunityEngine(self.db).decide(sid, search_gap=.2, competition=.1, historical_revenue=.4, site_fit=.8, country_fit=1, freshness=.9, risk=.1, cost=.1, provenance=provenance, risk_class="general")
        stored=json.loads(self.db.conn.execute("SELECT input_provenance,input_statuses FROM opportunities WHERE id=?",(oid,)).fetchone()[0])
        self.assertEqual(stored["search_gap"]["status"], "MISSING")
        self.assertEqual(self.db.conn.execute("SELECT decision FROM opportunities WHERE id=?",(oid,)).fetchone()[0], "WATCH")

    def test_cost_attribution(self):
        sid=TrendSensor(self.db).ingest("cost","fixture",[2,4,6,8,10]); oid=OpportunityEngine(self.db).decide(sid); Telemetry(self.db).record_cost("serp","openserp",amount=.03,opportunity_id=oid,idempotency_key="cost-1"); self.assertEqual(self.db.conn.execute("SELECT opportunity_id FROM cost_metrics WHERE idempotency_key='cost-1'").fetchone()[0],oid); self.assertEqual(content_economics(self.db,999)["contribution_profit"],0)

    def test_cost_no_double_count_and_contribution_profit(self):
        sid=TrendSensor(self.db).ingest("profit","fixture",[2,4,6,8,10]); oid=OpportunityEngine(self.db).decide(sid); site=self.db.site(tenant_id="t",country="US",language="en",topic="x",platform="local"); e=Editorial(self.db); plan=e.plan(oid,site,"MONEY",[],"x",[]); cid=e.article(plan,"x","y"); pub=LocalPublisher(self.db).publish(cid,site,tempfile.mkdtemp()); kid=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(oid,)).fetchone()[0]; Telemetry(self.db).record(pub,kid,revenue={"adsense_revenue":2},revenue_status="PASS"); t=Telemetry(self.db); t.record_cost("writer","local",amount=.5,opportunity_id=oid,content_id=cid,publication_id=pub,idempotency_key="same-row"); self.assertEqual(content_economics(self.db,cid)["contribution_profit"],1.5)

    def test_daily_time_window(self):
        sid=TrendSensor(self.db).ingest("today","fixture",[2,4,6,8,10]); self.db.conn.execute("UPDATE signals SET last_seen_at='2026-08-27T12:00:00+00:00' WHERE id=?",(sid,)); self.db.conn.commit(); self.assertEqual(daily_report(self.db,"2026-08-28")["signals"],0)

    def test_weekly_monthly_time_window(self):
        sid=TrendSensor(self.db).ingest("window","fixture",[2,4,6,8,10]); self.db.conn.execute("UPDATE signals SET last_seen_at='2026-08-20T12:00:00+00:00' WHERE id=?",(sid,)); self.db.conn.commit(); self.assertEqual(period_report(self.db,"weekly","2026-08-28")["period_start"],"2026-08-22"); self.assertEqual(period_report(self.db,"monthly","2026-08-28")["period_start"],"2026-07-30")

    def test_content_type_reporting_and_decision_reporting(self):
        sid=TrendSensor(self.db).ingest("report","fixture",[2,4,6,8,10]); oid=OpportunityEngine(self.db).decide(sid); site=self.db.site(tenant_id="t",country="US",language="en",topic="x",platform="local"); Editorial(self.db).plan(oid,site,"MONEY",[],"commercial",[]); result=period_report(self.db,"weekly","2099-01-01"); self.assertEqual(result["by_content_type"],[]); self.assertEqual(result["by_decision"],[]); result=period_report(self.db,"monthly"); self.assertEqual(result["by_content_type"][0]["content_type"],"MONEY")

    def test_c_history_revenue_context(self):
        sid=TrendSensor(self.db).ingest("revenue","fixture",[2,4,6,8,10]); oid=OpportunityEngine(self.db).decide(sid); site=self.db.site(tenant_id="t",country="US",language="en",topic="x",platform="local"); e=Editorial(self.db); plan=e.plan(oid,site,"MONEY",[],"commercial",[],harness="B-v9"); cid=e.article(plan,"title","body"); pub=LocalPublisher(self.db).publish(cid,site,tempfile.mkdtemp()); kid=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(oid,)).fetchone()[0]; Telemetry(self.db).record(pub,kid,rank=3,traffic={"impression":100,"click":10,"ctr":.1,"page_views":12},revenue={"adsense_revenue":1.2,"rpm":10},rank_status="PASS",analytics_status="PASS",revenue_status="PASS",search_console_status="PASS",checkpoint="24h"); h=e.history(kid)[0]; self.assertEqual((h["content_type"],h["harness_version"],h["adsense_revenue"],h["click"]),("MONEY","B-v9",1.2,10))

    def test_provider_status_separation(self):
        sid=TrendSensor(self.db).ingest("status","fixture",[2,4,6,8,10]); oid=OpportunityEngine(self.db).decide(sid); site=self.db.site(tenant_id="t",country="US",language="en",topic="x",platform="local"); e=Editorial(self.db); plan=e.plan(oid,site,"FAST",[],"x",[]); cid=e.article(plan,"x","y"); pub=LocalPublisher(self.db).publish(cid,site,tempfile.mkdtemp()); kid=self.db.conn.execute("SELECT keyword_id FROM opportunities WHERE id=?",(oid,)).fetchone()[0]; Telemetry(self.db).record(pub,kid,rank=1,rank_status="PASS",search_console_status="CONFIGURED_NO_DATA",analytics_status="NOT_CONFIGURED",revenue_status="FAIL",search={"query":"x","impressions":10},analytics={"source":"google","sessions":2}); row=self.db.conn.execute("SELECT provider_status FROM rank_history").fetchone(); self.assertEqual(row[0],"PASS"); self.assertEqual(self.db.conn.execute("SELECT provider_status FROM traffic_metrics").fetchone()[0],"NOT_CONFIGURED"); self.assertEqual(self.db.conn.execute("SELECT provider_status FROM revenue_metrics").fetchone()[0],"FAIL"); self.assertEqual(self.db.conn.execute("SELECT provider_status FROM search_metrics").fetchone()[0],"CONFIGURED_NO_DATA"); self.assertEqual(self.db.conn.execute("SELECT provider_status FROM analytics_metrics").fetchone()[0],"NOT_CONFIGURED"); self.assertEqual(daily_report(self.db)["external_telemetry"],"PARTIAL")
        self.assertEqual(WordPressPublisher.validate_post_response(ProviderResult("PASS",{"id":1,"link":"https://example.test/p","status":"publish"})).status,"PASS"); self.assertEqual(WordPressPublisher.validate_post_response(ProviderResult("PASS",{"id":1})).status,"FAIL")

    def test_site_fit_reason(self):
        site=self.db.site(tenant_id="t",country="US",language="en",topic="AI tools",platform="local",authority_tags="AI,tools,home",average_revenue=50); result=PublisherRouter(self.db).select_site_result("x","US","en","tools",["tools"]); self.assertGreater(result["site_fit_score"],0); self.assertIn("topic_fit=1.00",result["selection_reason"]); self.assertEqual(result["site"]["id"],site); self.assertEqual(result["router_version"],"SiteFit-v2")
        other=self.db.site(tenant_id="t",country="US",language="en",topic="finance",platform="local",average_rpm=100,average_revenue=99); selected=PublisherRouter(self.db).select_site_result("AI coding agent","US","en")["site"]; self.assertEqual(selected["id"],site)
        self.assertEqual(PublisherRouter, __import__("revenue_media_os.providers",fromlist=["PublisherRouter"]).PublisherRouter)

    def test_timezone_report(self):
        self.db.set_config("report_timezone","Asia/Seoul"); sid=TrendSensor(self.db).ingest("kst","fixture",[2,4,6,8,10]); self.db.conn.execute("UPDATE signals SET last_seen_at='2026-08-27T15:30:00+00:00' WHERE id=?",(sid,)); self.db.conn.commit(); self.assertEqual(daily_report(self.db,"2026-08-28")["signals"],1); self.db.set_config("report_timezone","America/New_York"); self.assertEqual(daily_report(self.db,"2026-08-27")["signals"],1)

    def test_old_db_migration_preserves_data_and_foreign_keys(self):
        path=tempfile.mktemp(suffix=".sqlite3"); old=sqlite3.connect(path); old.executescript((Path(__file__).parent/"fixtures"/"stage1_schema.sql").read_text()); old.executescript("INSERT INTO keywords VALUES(1,'GPT','US','en','2026-01-01'); INSERT INTO signals VALUES(1,1,'old','US','en',3,1,2,3,1,1,1,1,2,1,1,'2026-01-01','2026-01-01',0); INSERT INTO cost_metrics VALUES(1,'writer','local',1,2,.5,'USD','2026-01-01','PASS');"); old.commit(); old.close(); db=IntelligenceDB(path); self.assertEqual(db.keyword("GPT","KR","ko"),2); self.assertEqual(db.conn.execute("SELECT keyword,country FROM keywords WHERE id=1").fetchone()[1],"US"); self.assertEqual(db.conn.execute("SELECT amount FROM cost_metrics WHERE id=1").fetchone()[0],.5); self.assertTrue({r[2] for r in db.conn.execute("PRAGMA foreign_key_list(cost_metrics)")} >= {"opportunities","contents","publications"}); self.assertEqual(db.conn.execute("PRAGMA foreign_key_check").fetchall(),[]); db.close()

    def test_schema_versioning(self):
        self.assertEqual(self.db.conn.execute("SELECT max(version) FROM schema_version").fetchone()[0],3); self.assertGreaterEqual(self.db.conn.execute("SELECT count(*) FROM migration_history").fetchone()[0],1)

    def test_idempotency_boundary(self):
        s=Scheduler(TrendSensor(self.db)); a=s.run_once([{"keyword":"same","source":"api","mention_count":3,"unique_authors":1,"engagement":2,"observed_at":"2026-08-28T00:00:00+00:00","status":"OBSERVED","idempotency_key":"event-1"}],run_id="r1"); b=s.run_once([{"keyword":"same","source":"api","mention_count":3,"unique_authors":1,"engagement":2,"observed_at":"2026-08-28T00:00:00+00:00","status":"OBSERVED","idempotency_key":"event-1"}],run_id="r2"); self.assertEqual(a["signals"],b["signals"]); self.assertEqual(self.db.conn.execute("SELECT count(*) FROM signals").fetchone()[0],1)

if __name__ == "__main__": unittest.main()
