#!/usr/bin/env python3
"""Build daily government/nonprofit ethics story tiles from English RSS results."""

from __future__ import annotations

import json
import re
import ssl
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "data" / "stories.json"

GOVERNMENT_QUERIES = [
    "government ethics",
    "public corruption",
    "public sector graft",
    "ethics commission investigation",
    "city council bribery",
    "federal corruption case",
]

NONPROFIT_QUERIES = [
    "nonprofit ethics",
    "charity corruption",
    "charity fraud case",
    "ngo corruption",
    "foundation embezzlement",
    "nonprofit governance reform",
]

POSITIVE_KEYWORDS = {
    "reform",
    "transparency",
    "oversight",
    "accountability",
    "improve",
    "improved",
    "improves",
    "cleaned up",
    "cleared",
    "acquitted",
    "new ethics rules",
    "adopts ethics",
}

NEGATIVE_KEYWORDS = {
    "corruption",
    "graft",
    "bribery",
    "bribe",
    "fraud",
    "embezzlement",
    "kickback",
    "money laundering",
    "scandal",
    "probe",
    "investigation",
    "charged",
    "indicted",
    "convicted",
    "arrested",
    "misuse",
    "misconduct",
}

GOVERNMENT_TERMS = {
    "government",
    "public",
    "municipal",
    "city",
    "state",
    "federal",
    "minister",
    "senate",
    "congress",
    "parliament",
    "mayor",
    "governor",
    "agency",
    "department",
    "county",
}

NONPROFIT_TERMS = {
    "nonprofit",
    "non-profit",
    "charity",
    "foundation",
    "ngo",
    "not-for-profit",
    "philanthropy",
}

BUSINESS_TERMS = {
    "earnings",
    "quarterly results",
    "stock",
    "share price",
    "ipo",
    "merger",
    "acquisition",
    "ceo",
    "investor",
    "wall street",
}

MAX_STORIES_PER_COLUMN = 60
USER_AGENT = "ethics-news-board/1.0 (+https://github.com/rkchristensen/ethics_news_test)"


@dataclass
class Story:
    title: str
    short_title: str
    url: str
    source: str
    published_at: str
    sentiment: str
    government: bool
    nonprofit: bool


def google_news_rss_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            # Local Python setups occasionally miss trusted cert bundles.
            insecure = ssl._create_unverified_context()
            with urllib.request.urlopen(request, timeout=20, context=insecure) as response:
                return response.read()
        raise


def parse_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def short_title(title: str, limit: int = 95) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def classify_sentiment(text: str) -> str | None:
    if contains_any(text, NEGATIVE_KEYWORDS):
        return "negative"
    if contains_any(text, POSITIVE_KEYWORDS):
        return "positive"
    return None


def parse_rss_items(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = root.findall(".//item")
    parsed = []
    for item in items:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()

        source_name = ""
        source_el = item.find("source")
        if source_el is not None and source_el.text:
            source_name = source_el.text.strip()
        if not source_name and " - " in title:
            source_name = title.rsplit(" - ", 1)[-1].strip()

        parsed.append(
            {
                "title": title,
                "url": link,
                "published_at": parse_date(pub_date),
                "source": source_name or "Unknown source",
            }
        )
    return parsed


def should_skip_business_only(text: str) -> bool:
    has_business = contains_any(text, BUSINESS_TERMS)
    has_relevant_domain = contains_any(text, GOVERNMENT_TERMS) or contains_any(text, NONPROFIT_TERMS)
    return has_business and not has_relevant_domain


def normalize_story(raw: dict, default_government: bool, default_nonprofit: bool) -> Story | None:
    if not raw["title"] or not raw["url"]:
        return None

    text = f'{raw["title"]} {raw["source"]}'.lower()
    if should_skip_business_only(text):
        return None

    sentiment = classify_sentiment(text)
    if sentiment is None:
        return None

    is_government = contains_any(text, GOVERNMENT_TERMS)
    is_nonprofit = contains_any(text, NONPROFIT_TERMS)

    # If query selection caught a story without explicit terms, keep broad category coverage.
    if not is_government and not is_nonprofit:
        is_government = default_government
        is_nonprofit = default_nonprofit

    return Story(
        title=raw["title"],
        short_title=short_title(raw["title"]),
        url=raw["url"],
        source=raw["source"],
        published_at=raw["published_at"].isoformat(),
        sentiment=sentiment,
        government=is_government,
        nonprofit=is_nonprofit,
    )


def collect_stories() -> list[Story]:
    collected: list[Story] = []
    seen_urls: set[str] = set()
    grouped_queries = (
        [(query, True, False) for query in GOVERNMENT_QUERIES]
        + [(query, False, True) for query in NONPROFIT_QUERIES]
    )

    for query, default_government, default_nonprofit in grouped_queries:
        try:
            feed_xml = fetch(google_news_rss_url(query))
        except Exception:
            continue

        for item in parse_rss_items(feed_xml):
            url = item["url"]
            if url in seen_urls:
                continue
            story = normalize_story(item, default_government, default_nonprofit)
            if story is None:
                continue
            seen_urls.add(url)
            collected.append(story)

    return sorted(
        collected,
        key=lambda s: s.published_at,
        reverse=True,
    )


def build_output(stories: list[Story]) -> dict:
    government = [s for s in stories if s.government][:MAX_STORIES_PER_COLUMN]
    nonprofit = [s for s in stories if s.nonprofit][:MAX_STORIES_PER_COLUMN]

    def to_dict(story: Story) -> dict:
        return {
            "title": story.title,
            "short_title": story.short_title,
            "url": story.url,
            "source": story.source,
            "published_at": story.published_at,
            "sentiment": story.sentiment,
        }

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "government": [to_dict(s) for s in government],
        "nonprofit": [to_dict(s) for s in nonprofit],
    }


def main() -> None:
    stories = collect_stories()
    payload = build_output(stories)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(payload['government'])} government and {len(payload['nonprofit'])} nonprofit stories.")


if __name__ == "__main__":
    main()
