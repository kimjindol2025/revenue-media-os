import json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from revenue_media_os.core import *
from revenue_media_os.providers import GoogleAPIProvider, OpenSERPProvider, PublisherRouter, WordPressPublisher

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

if __name__ == "__main__": unittest.main()
