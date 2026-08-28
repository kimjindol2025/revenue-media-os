"""Small, dependency-free provider boundaries.

These adapters intentionally do not scrape around access controls. Credentials
and endpoints must be supplied by the operator; otherwise the result is
NOT_CONFIGURED and no database PASS is emitted.
"""
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class ProviderResult:
    status: str
    data: object = None
    error: str = None


def _request(url, method="GET", payload=None, headers=None, timeout=15):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", **(headers or {})})
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return ProviderResult("PASS", json.loads(raw) if raw else {})
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return ProviderResult("FAIL", error=str(exc))


class RSSFeedSource:
    """Allowed public RSS/Atom source; each item is one observed signal unit."""
    def __init__(self, feed_url): self.feed_url = feed_url
    def collect(self, keyword=None):
        if not self.feed_url: return ProviderResult("NOT_CONFIGURED")
        try:
            with urllib.request.urlopen(self.feed_url, timeout=15) as response: root = ET.fromstring(response.read())
            items = root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry")
            data = [{"title": x.findtext("title", default="").strip(), "source": self.feed_url} for x in items]
            if keyword: data = [x for x in data if keyword.lower() in x["title"].lower()]
            return ProviderResult("PASS", data)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
            return ProviderResult("FAIL", error=str(exc))


class OpenSERPProvider:
    def __init__(self, endpoint=None, token=None): self.endpoint = endpoint or os.getenv("OPEN_SERP_ENDPOINT"); self.token = token or os.getenv("OPEN_SERP_TOKEN")
    def search(self, query, engine="google", country="US", language="en"):
        if not self.endpoint: return ProviderResult("NOT_CONFIGURED")
        q = urllib.parse.urlencode({"q": query, "engine": engine, "country": country, "language": language})
        result = _request(self.endpoint.rstrip("/") + "/search?" + q, headers={"Authorization": f"Bearer {self.token}"} if self.token else None)
        if result.status != "PASS": return result
        rows = result.data.get("results", result.data if isinstance(result.data, list) else [])
        return ProviderResult("PASS", [{"title": x.get("title", ""), "url": x.get("url", x.get("link", "")), "snippet": x.get("snippet", ""), "features": x.get("features", {})} for x in rows[:10]])


class WordPressPublisher:
    def __init__(self, base_url=None, token=None): self.base_url = (base_url or os.getenv("WORDPRESS_URL", "")).rstrip("/"); self.token = token or os.getenv("WORDPRESS_TOKEN")
    def _configured(self): return bool(self.base_url and self.token)
    @staticmethod
    def validate_post_response(result):
        if result.status != "PASS": return result
        data = result.data if isinstance(result.data, dict) else {}
        if not isinstance(data.get("id"), int) or not isinstance(data.get("link"), str) or not data.get("link") or not isinstance(data.get("status"), str):
            return ProviderResult("FAIL", error="WordPress response missing validated id/link/status")
        return ProviderResult("PASS", {"id": data["id"], "link": data["link"], "status": data["status"]})
    def publish(self, title, body, status="draft"):
        if not self._configured(): return ProviderResult("NOT_CONFIGURED")
        return self.validate_post_response(_request(self.base_url + "/wp-json/wp/v2/posts", "POST", {"title": title, "content": body, "status": status}, {"Authorization": f"Bearer {self.token}"}))
    def update(self, post_id, title, body, status="draft"):
        if not self._configured(): return ProviderResult("NOT_CONFIGURED")
        return self.validate_post_response(_request(self.base_url + f"/wp-json/wp/v2/posts/{post_id}", "POST", {"title": title, "content": body, "status": status}, {"Authorization": f"Bearer {self.token}"}))
    def get_status(self, post_id): return "NOT_CONFIGURED" if not self._configured() else "UNVERIFIED"
    def get_url(self, post_id): return None if not self._configured() else self.base_url + f"/?p={post_id}"


class GoogleAPIProvider:
    """Generic authenticated JSON boundary for GSC, GA4, and AdSense."""
    def __init__(self, api_url, token=None): self.api_url=api_url.rstrip("/") if api_url else None; self.token=token or os.getenv("GOOGLE_ACCESS_TOKEN")
    def query(self, path, payload=None):
        if not self.api_url or not self.token: return ProviderResult("NOT_CONFIGURED")
        return _request(self.api_url + "/" + path.lstrip("/"), "POST" if payload else "GET", payload, {"Authorization": f"Bearer {self.token}"})


class PublisherRouter:
    def __init__(self, db, adapters=None): self.db=db; self.adapters=adapters or {"local": None}
    def select_site_result(self, keyword, country, language, topic=None, authority_tags=None):
        rows=self.db.conn.execute("SELECT * FROM sites WHERE country=? AND language=? AND health_status != 'BLOCKED' AND policy_status != 'BLOCKED'",(country,language)).fetchall()
        if not rows: return None
        keyword_terms=set((keyword or "").lower().split()); candidates=[]
        for row in rows:
            topic_text=(row["topic"] or "").lower(); authority_text=(row["authority_tags"] or "").lower(); topic_fit=1.0 if not topic or topic.lower() in topic_text or any(t in topic_text for t in keyword_terms) else .15; authority_fit=1.0 if not authority_tags else min(1.0,sum(a.lower() in authority_text for a in authority_tags)/len(authority_tags)); keyword_fit=1.0 if any(t in topic_text or t in authority_text for t in keyword_terms) else .1; historical=min(1.0,float(row["average_revenue"])/100); policy_fit=1.0 if row["policy_status"] in {"OK","UNKNOWN"} else 0; health_fit=1.0 if row["health_status"] in {"OK","UNKNOWN"} else 0; score=.25*topic_fit+.2*authority_fit+.15*keyword_fit+.15*historical+.15*policy_fit+.1*health_fit; candidates.append((score,row,{"topic_fit":topic_fit,"authority_fit":authority_fit,"country_fit":1.0,"language_fit":1.0,"historical_revenue_fit":historical,"policy_fit":policy_fit,"health_fit":health_fit}))
        score,row,components=max(candidates,key=lambda item:item[0]); reason="; ".join(f"{k}={v:.2f}" for k,v in components.items()); return {"site":row,"site_fit_score":score,"candidate_scores":[{"site_id":r["id"],"score":s,"components":c} for s,r,c in candidates],"selection_reason":reason,"router_version":"SiteFit-v2",**components}
    def select_site(self, keyword, country, language, topic=None):
        result=self.select_site_result(keyword,country,language,topic)
        return result["site"] if result else None
    def adapter_for(self, site):
        return self.adapters.get(site["platform"]) if site else None
