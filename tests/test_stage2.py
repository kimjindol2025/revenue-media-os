import json
import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from revenue_media_os.core import IntelligenceDB, TrendSensor
from revenue_media_os.providers import ProviderResult, RedditTrendsProvider


class FakeProvider:
    name = "reddit"

    def __init__(self, status="PASS", data=None, error=None):
        self.result = ProviderResult(status, data, error, provider_request_id="fake-request", captured_at="2026-08-28T00:05:00+00:00")

    def fetch_trends(self, country, language, since, until):
        return self.result


class Stage2Test(unittest.TestCase):
    def setUp(self):
        self.db = IntelligenceDB()

    def tearDown(self):
        self.db.close()

    def window(self):
        end = datetime.now(timezone.utc).replace(microsecond=0)
        return (end - timedelta(hours=2)).isoformat(), end.isoformat()

    def reddit_request(self, payload):
        def request(url, headers=None):
            return ProviderResult("PASS", payload)
        return request

    def test_real_provider_not_configured(self):
        result = RedditTrendsProvider(access_token=None, endpoint="https://example.test").fetch_trends("US", "en", *self.window())
        self.assertEqual(result.status, "NOT_CONFIGURED")

    def test_real_provider_success_normalizes_and_persists(self):
        created = datetime.now(timezone.utc).timestamp() - 60
        payload = {"data": {"children": [{"data": {"id": "abc", "title": "OpenAI coding agent", "author": "alice", "score": 7, "num_comments": 3, "created_utc": created}}]}}
        provider = RedditTrendsProvider(access_token="secret-token", endpoint="https://example.test", request_fn=self.reddit_request(payload))
        result = provider.fetch_trends("US", "en", *self.window())
        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.data[0]["provider_request_id"])
        self.assertNotIn("secret-token", json.dumps(result.raw))
        saved = TrendSensor(self.db).ingest_provider(provider, "US", "en", *self.window())
        self.assertEqual(saved["provider_status"], "PASS")
        row = self.db.conn.execute("SELECT * FROM signal_observations").fetchone()
        self.assertEqual((row["status"], row["source"], row["mention_count"]), ("OBSERVED", "reddit", 1))
        self.assertTrue(row["bucket_start"].endswith(":00:00+00:00"))
        self.assertTrue(row["bucket_end"].endswith(":00:00+00:00"))
        self.assertEqual(row["provider_request_id"], result.data[0]["provider_request_id"])
        self.assertEqual(json.loads(row["raw_evidence"])["provider"], "reddit")

    def test_real_provider_no_data_and_failure_states(self):
        empty = {"data": {"children": []}}
        result = RedditTrendsProvider(access_token="token", endpoint="https://example.test", request_fn=self.reddit_request(empty)).fetch_trends("US", "en", *self.window())
        self.assertEqual(result.status, "CONFIGURED_NO_DATA")
        failure = RedditTrendsProvider(access_token="token", endpoint="https://example.test", request_fn=lambda *args, **kwargs: ProviderResult("FAIL", error="upstream"))
        self.assertEqual(failure.fetch_trends("US", "en", *self.window()).status, "FAIL")

    def test_real_provider_malformed_response(self):
        provider = RedditTrendsProvider(access_token="token", endpoint="https://example.test", request_fn=self.reddit_request({"unexpected": []}))
        self.assertEqual(provider.fetch_trends("US", "en", *self.window()).status, "FAIL")

    def test_real_provider_validation_rejects_bad_numeric_and_future_data(self):
        since, until = self.window()
        captured = "2026-08-28T00:05:00+00:00"
        base = {"keyword": "bad", "source": "reddit", "unique_authors": 1, "engagement": 1, "observed_at": until, "country": "US", "language": "en", "captured_at": captured}
        for bad in (math.nan, math.inf, -1):
            item = dict(base, mention_count=bad)
            result = TrendSensor(self.db).ingest_provider(FakeProvider(data=[item]), "US", "en", since, until)
            self.assertEqual(result["provider_status"], "FAIL")
        future = dict(base, mention_count=1, observed_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.assertEqual(TrendSensor(self.db).ingest_provider(FakeProvider(data=[future]), "US", "en", since, until)["provider_status"], "FAIL")

    def test_real_hourly_aggregation_and_fast_signal(self):
        sensor = TrendSensor(self.db, {"fast_signal_min_mentions": 10, "fast_signal_min_velocity": 0, "fast_signal_min_acceleration": 0})
        rows = []
        base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for offset, count in ((-2, 0), (-1, 0), (0, 10)):
            observed = (base + timedelta(hours=offset)).isoformat()
            rows.append({"keyword": "trend", "source": "reddit", "mention_count": count, "unique_authors": count, "engagement": count, "observed_at": observed, "country": "US", "language": "en", "captured_at": datetime.now(timezone.utc).isoformat(), "provider_request_id": "bucket-test"})
        saved = sensor.ingest_provider(FakeProvider(data=rows), "US", "en", (base - timedelta(hours=2)).isoformat(), (base + timedelta(hours=1)).isoformat())
        self.assertEqual(saved["provider_status"], "PASS")
        self.assertEqual(self.db.conn.execute("SELECT velocity_1h FROM signals ORDER BY id DESC LIMIT 1").fetchone()[0], 10)
        self.assertEqual(self.db.conn.execute("SELECT trend_state FROM signals ORDER BY id DESC LIMIT 1").fetchone()[0], "FAST_SIGNAL")

    def test_real_idempotency_and_no_fixture_as_real(self):
        observed = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        item = {"keyword": "same", "source": "reddit", "mention_count": 2, "unique_authors": 1, "engagement": 3, "observed_at": observed, "country": "US", "language": "en", "captured_at": datetime.now(timezone.utc).isoformat(), "provider_request_id": "request-1", "idempotency_key": "request-1:same"}
        sensor = TrendSensor(self.db)
        since = (datetime.fromisoformat(observed) - timedelta(hours=1)).isoformat()
        until = (datetime.fromisoformat(observed) + timedelta(hours=1)).isoformat()
        first = sensor.ingest_provider(FakeProvider(data=[item]), "US", "en", since, until)
        second = sensor.ingest_provider(FakeProvider(data=[item]), "US", "en", since, until)
        self.assertEqual(first["signals"], second["signals"])
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM signals").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT status FROM signal_observations").fetchone()[0], "OBSERVED")


if __name__ == "__main__":
    unittest.main()
