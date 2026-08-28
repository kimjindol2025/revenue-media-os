"""Provider boundaries and canonical publisher routing.

Adapters do not bypass access controls. Credentials and endpoints are supplied
by the operator. Missing configuration remains NOT_CONFIGURED.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProviderResult:
    status: str
    data: object = None
    error: str = None


def _request(url, method="GET", payload=None, headers=None, timeout=15):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", **(headers or {})},
    )
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return ProviderResult("PASS", json.loads(raw) if raw else {})
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return ProviderResult("FAIL", error=str(exc))


class RSSFeedSource:
    def __init__(self, feed_url):
        self.feed_url = feed_url

    def collect(self, keyword=None):
        if not self.feed_url:
            return ProviderResult("NOT_CONFIGURED")
        try:
            with urllib.request.urlopen(self.feed_url, timeout=15) as response:
                root = ET.fromstring(response.read())
            items = root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry")
            data = [{"title": x.findtext("title", default="").strip(), "source": self.feed_url} for x in items]
            if keyword:
                data = [x for x in data if keyword.lower() in x["title"].lower()]
            return ProviderResult("PASS", data)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
            return ProviderResult("FAIL", error=str(exc))


class OpenSERPProvider:
    def __init__(self, endpoint=None, token=None):
        self.endpoint = endpoint or os.getenv("OPEN_SERP_ENDPOINT")
        self.token = token or os.getenv("OPEN_SERP_TOKEN")

    def search(self, query, engine="google", country="US", language="en"):
        if not self.endpoint:
            return ProviderResult("NOT_CONFIGURED")
        q = urllib.parse.urlencode(
            {"q": query, "engine": engine, "country": country, "language": language}
        )
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        result = _request(self.endpoint.rstrip("/") + "/search?" + q, headers=headers)
        if result.status != "PASS":
            return result
        rows = result.data.get("results", result.data if isinstance(result.data, list) else [])
        return ProviderResult(
            "PASS",
            [
                {
                    "title": x.get("title", ""),
                    "url": x.get("url", x.get("link", "")),
                    "snippet": x.get("snippet", ""),
                    "features": x.get("features", {}),
                }
                for x in rows[:10]
            ],
        )


class WordPressPublisher:
    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or os.getenv("WORDPRESS_URL", "")).rstrip("/")
        self.token = token or os.getenv("WORDPRESS_TOKEN")

    def _configured(self):
        return bool(self.base_url and self.token)

    @staticmethod
    def validate_post_response(result):
        if result.status != "PASS":
            return result
        data = result.data if isinstance(result.data, dict) else {}
        if (
            not isinstance(data.get("id"), int)
            or not isinstance(data.get("link"), str)
            or not data.get("link")
            or not isinstance(data.get("status"), str)
        ):
            return ProviderResult("FAIL", error="WordPress response missing validated id/link/status")
        return ProviderResult("PASS", {"id": data["id"], "link": data["link"], "status": data["status"]})

    def publish(self, title, body, status="draft"):
        if not self._configured():
            return ProviderResult("NOT_CONFIGURED")
        return self.validate_post_response(
            _request(
                self.base_url + "/wp-json/wp/v2/posts",
                "POST",
                {"title": title, "content": body, "status": status},
                {"Authorization": f"Bearer {self.token}"},
            )
        )

    def update(self, post_id, title, body, status="draft"):
        if not self._configured():
            return ProviderResult("NOT_CONFIGURED")
        return self.validate_post_response(
            _request(
                self.base_url + f"/wp-json/wp/v2/posts/{post_id}",
                "POST",
                {"title": title, "content": body, "status": status},
                {"Authorization": f"Bearer {self.token}"},
            )
        )

    def get_status(self, post_id):
        return "NOT_CONFIGURED" if not self._configured() else "UNVERIFIED"

    def get_url(self, post_id):
        return None if not self._configured() else self.base_url + f"/?p={post_id}"


class GoogleAPIProvider:
    def __init__(self, api_url, token=None):
        self.api_url = api_url.rstrip("/") if api_url else None
        self.token = token or os.getenv("GOOGLE_ACCESS_TOKEN")

    def query(self, path, payload=None):
        if not self.api_url or not self.token:
            return ProviderResult("NOT_CONFIGURED")
        return _request(
            self.api_url + "/" + path.lstrip("/"),
            "POST" if payload else "GET",
            payload,
            {"Authorization": f"Bearer {self.token}"},
        )


class PublisherRouter:
    """Canonical Site Portfolio router.

    Every eligible candidate is scored before a winner is selected. High RPM
    alone cannot override topic/authority fit. Results may be persisted for
    auditability through `opportunity_id`.
    """

    def __init__(self, db, adapters=None, version="PublisherRouter-v2"):
        self.db = db
        self.adapters = adapters or {}
        self.version = version

    @staticmethod
    def _tokens(text):
        return {x for x in re.findall(r"[\w+-]+", (text or "").lower()) if len(x) > 1}

    @staticmethod
    def _status_fit(status):
        value = (status or "UNKNOWN").upper()
        if value in {"BLOCKED", "FAIL"}:
            return 0.0
        if value in {"GOOD", "CLEAN", "PASS", "ACTIVE"}:
            return 1.0
        return 0.8

    def _score(self, site, keyword, topic=None, authority_tags=None):
        keyword_tokens = self._tokens(keyword)
        topic_tokens = self._tokens(site["topic"])
        authority_tokens = self._tokens(site["authority_tags"])
        requested_topic = self._tokens(topic)
        requested_authority = {x.lower() for x in (authority_tags or [])}
        target = keyword_tokens | requested_topic
        topic_fit = 1.0 if not target else len(target & topic_tokens) / max(1, len(target))
        authority_target = keyword_tokens | requested_authority
        authority_fit = 1.0 if not authority_target else len(authority_target & authority_tokens) / max(1, len(authority_target))
        historical = min(1.0, max(0.0, float(site["average_revenue"] or 0)) / 100.0)
        rpm_fit = min(1.0, max(0.0, float(site["average_rpm"] or 0)) / 20.0)
        health_fit = self._status_fit(site["health_status"])
        policy_fit = self._status_fit(site["policy_status"])
        score = (
            0.28 * topic_fit
            + 0.25 * authority_fit
            + 0.12 * historical
            + 0.08 * rpm_fit
            + 0.12 * health_fit
            + 0.15 * policy_fit
        )
        return {
            "site_id": site["id"],
            "site_fit_score": round(score, 6),
            "topic_fit": round(topic_fit, 6),
            "authority_fit": round(authority_fit, 6),
            "historical_revenue_fit": round(historical, 6),
            "rpm_fit": round(rpm_fit, 6),
            "health_fit": round(health_fit, 6),
            "policy_fit": round(policy_fit, 6),
        }

    def select_site_result(
        self,
        keyword,
        country,
        language,
        topic=None,
        authority_tags=None,
        opportunity_id=None,
    ):
        rows = self.db.conn.execute(
            "SELECT * FROM sites WHERE country=? AND language=? "
            "AND health_status != 'BLOCKED' AND policy_status != 'BLOCKED'",
            (country, language),
        ).fetchall()
        if not rows:
            return None
        scored = [(self._score(site, keyword, topic, authority_tags), site) for site in rows]
        scored.sort(key=lambda item: (-item[0]["site_fit_score"], item[1]["id"]))
        winner_score, winner = scored[0]
        candidate_scores = [item[0] for item in scored]
        reason = (
            f"site={winner['id']}; score={winner_score['site_fit_score']:.3f}; "
            f"topic={winner_score['topic_fit']:.3f}; authority={winner_score['authority_fit']:.3f}; "
            f"history={winner_score['historical_revenue_fit']:.3f}; rpm={winner_score['rpm_fit']:.3f}"
        )
        if opportunity_id is not None:
            self.db.conn.execute(
                "INSERT INTO site_selections(opportunity_id,selected_site_id,keyword,router_version,candidate_scores,selection_reason,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    opportunity_id,
                    winner["id"],
                    keyword,
                    self.version,
                    json.dumps(candidate_scores, sort_keys=True),
                    reason,
                    _now(),
                ),
            )
            self.db.conn.commit()
        return {
            "site": winner,
            "site_fit_score": winner_score["site_fit_score"],
            "selection_reason": reason,
            "candidate_scores": candidate_scores,
            "router_version": self.version,
        }

    def select_site(self, keyword, country, language, topic=None):
        result = self.select_site_result(keyword, country, language, topic)
        return result["site"] if result else None

    def adapter_for(self, site):
        return self.adapters.get(site["platform"]) if site else None
