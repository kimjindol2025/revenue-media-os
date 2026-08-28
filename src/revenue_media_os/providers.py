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
    def publish(self, title, body, status="draft"):
        if not self._configured(): return ProviderResult("NOT_CONFIGURED")
        return _request(self.base_url + "/wp-json/wp/v2/posts", "POST", {"title": title, "content": body, "status": status}, {"Authorization": f"Bearer {self.token}"})
    def update(self, post_id, title, body, status="draft"):
        if not self._configured(): return ProviderResult("NOT_CONFIGURED")
        return _request(self.base_url + f"/wp-json/wp/v2/posts/{post_id}", "POST", {"title": title, "content": body, "status": status}, {"Authorization": f"Bearer {self.token}"})
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
    def select_site(self, keyword, country, language, topic=None):
        clauses=["country=?", "language=?", "health_status != 'BLOCKED'", "policy_status != 'BLOCKED'"]; args=[country,language]
        if topic: clauses.append("topic LIKE ?"); args.append("%"+topic+"%")
        row=self.db.conn.execute("SELECT * FROM sites WHERE " + " AND ".join(clauses) + " ORDER BY average_rpm DESC, average_revenue DESC LIMIT 1",args).fetchone()
        return row
    def adapter_for(self, site):
        return self.adapters.get(site["platform"]) if site else None
