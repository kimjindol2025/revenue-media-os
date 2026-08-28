import json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from revenue_media_os.core import *

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

if __name__ == "__main__": unittest.main()
