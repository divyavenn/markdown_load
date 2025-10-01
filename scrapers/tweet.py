"""Export Twitter threads to Markdown using a logged-in browser session.

The script mirrors the ergonomics of ``scrapers.substack``: it reads the
session cookies required to access subscriber-only content, fetches the
requested page, and converts the returned payload into a Markdown document.

Usage (CLI):
    python -m scrapers.tweet --url https://x.com/user/status/12345

First run prompts for the ``auth_token`` and ``ct0`` cookies that Twitter/X uses
to authenticate browser requests. Paste the values exactly as reported in your
DevTools. The cookies are written to ``twitter_session.json`` so subsequent
runs stay non-interactive.

Implementation notes:
    * The thread is resolved from the ``__NEXT_DATA__`` payload embedded in the
      initial HTML. We stay on the HTML surface (instead of calling private
      GraphQL endpoints) so the only requirement is a valid session cookie.
    * Only tweets authored by the same account as the thread starter are
      exported; quote-tweets or replies by other users are not included.
    * Links, mentions, cashtags, and hashtags are rewritten into Markdown.
      Media assets are surfaced as Markdown images (photos) or links (video /
      GIF variants).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


SESSION_FILE = Path("twitter_session.json")
OUTPUT_DIR = Path("tweet_exports")
DEBUG_SAVE_RESPONSES = True

_SESSION_CACHE: dict[str, str] | None = None

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://x.com/",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MediaAsset:
    url: str
    type: str
    alt_text: str | None = None


@dataclass
class ThreadTweet:
    id_str: str
    created_at: datetime
    author_screen_name: str
    author_display_name: str
    permalink: str
    body_markdown: str
    media: list[MediaAsset]


@dataclass
class ThreadExport:
    filename: str
    markdown: str
    tweet_count: int
    author: str


# ---------------------------------------------------------------------------
# Cookie / session helpers
# ---------------------------------------------------------------------------


def load_session_cookies() -> dict[str, str]:
    """Retrieve the stored Twitter session cookies (prompting if absent)."""

    if SESSION_FILE.exists():
        try:
            payload = json.loads(SESSION_FILE.read_text())
        except json.JSONDecodeError as exc:
            print(f"Existing {SESSION_FILE} is not valid JSON ({exc}).")
        else:
            cookies = payload.get("cookies") if isinstance(payload, dict) else None
            if isinstance(cookies, dict):
                auth = str(cookies.get("auth_token", "")).strip()
                ct0 = str(cookies.get("ct0", "")).strip()
                if auth and ct0:
                    return {"auth_token": auth, "ct0": ct0}
            print(f"Existing {SESSION_FILE} does not contain auth_token + ct0.")

    print(
        textwrap.dedent(
            """
            Twitter session cookies required.

            1. Log in to https://x.com in your browser.
            2. Open DevTools → Application → Storage → Cookies.
            3. Copy the `auth_token` and `ct0` values.
            4. Paste them when prompted below (they are stored locally).
            """
        ).strip()
    )

    while True:
        try:
            auth_token = input("auth_token cookie value: ").strip()
            ct0 = input("ct0 cookie value: ").strip()
        except EOFError:
            raise SystemExit("No cookies provided; aborting.")

        if not auth_token or not ct0:
            print("Both auth_token and ct0 must be provided. Let's try again.")
            continue

        if " " in auth_token or " " in ct0:
            print("Cookie values should not contain spaces. Please re-check and retry.")
            continue

        break

    cookies = {"auth_token": auth_token, "ct0": ct0}
    SESSION_FILE.write_text(
        json.dumps({"cookies": cookies, "updated": datetime.utcnow().isoformat() + "Z"}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved cookies to {SESSION_FILE}. Keep this file private.")
    return cookies


def ensure_session_cookies() -> dict[str, str]:
    """Return the cached cookie jar, prompting for values if necessary."""

    global _SESSION_CACHE
    if _SESSION_CACHE is None:
        _SESSION_CACHE = load_session_cookies()
    return _SESSION_CACHE


def normalize_session_cookies(raw: dict[str, str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise TypeError("Session cookies must be provided as a mapping.")

    auth_token = str(raw.get("auth_token", "")).strip()
    ct0 = str(raw.get("ct0", "")).strip()

    if not auth_token or not ct0:
        raise ValueError("Both 'auth_token' and 'ct0' cookies are required to fetch threads.")

    return {"auth_token": auth_token, "ct0": ct0}


# ---------------------------------------------------------------------------
# Fetch & parse
# ---------------------------------------------------------------------------


def fetch_html(url: str, cookies: dict[str, str]) -> str:
    """Download the HTML backing the tweet URL."""

    jar = {"auth_token": cookies["auth_token"], "ct0": cookies["ct0"]}
    headers = REQUEST_HEADERS | {
        "x-csrf-token": cookies["ct0"],
    }

    response = requests.get(url, headers=headers, cookies=jar, timeout=30)
    response.raise_for_status()
    html_text = response.text

    if DEBUG_SAVE_RESPONSES:
        debug_dir = OUTPUT_DIR / "_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        slug = slugify(urlparse(url).path or "tweet")[:80]
        (debug_dir / f"{slug or 'tweet'}.html").write_text(html_text, encoding="utf-8")

    return html_text


def extract_next_data(html_text: str) -> dict[str, Any]:
    """Return the JSON payload from the <script id="__NEXT_DATA__"> tag."""

    soup = BeautifulSoup(html_text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        raise ValueError("Could not locate __NEXT_DATA__ payload in the response.")
    try:
        return json.loads(script.string)
    except json.JSONDecodeError as exc:
        raise ValueError("Embedded __NEXT_DATA__ JSON is malformed.") from exc


def extract_apollo_state(next_data: dict[str, Any]) -> dict[str, Any]:
    """Twitter threads live in the apolloState tree within __NEXT_DATA__."""

    queue: list[Any] = [next_data]

    while queue:
        current = queue.pop()
        if isinstance(current, dict):
            apollo = current.get("apolloState")
            if isinstance(apollo, dict):
                return apollo
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)

    raise ValueError("Could not locate apolloState in __NEXT_DATA__ payload.")


def parse_thread(apollo_state: dict[str, Any], tweet_id: str) -> list[ThreadTweet]:
    """Extract tweets authored by the thread owner from the normalized store."""

    key, node = locate_tweet_entry(apollo_state, tweet_id)
    legacy = resolve_legacy_tweet(node)
    if not legacy:
        raise ValueError("Root tweet did not contain legacy payload.")

    root_user_id = legacy.get("user_id_str")
    conversation_id = legacy.get("conversation_id_str") or legacy.get("id_str")
    if not root_user_id or not conversation_id:
        raise ValueError("Root tweet missing conversation metadata.")

    tweets: list[ThreadTweet] = []
    seen_ids: set[str] = set()

    for entry in iter_tweet_nodes(apollo_state.values()):
        legacy_data = resolve_legacy_tweet(entry)
        if not legacy_data:
            continue
        if legacy_data.get("conversation_id_str") != conversation_id:
            continue
        if legacy_data.get("user_id_str") != root_user_id:
            continue

        tweet_id_str = legacy_data.get("id_str")
        if not tweet_id_str or tweet_id_str in seen_ids:
            continue

        user_info = resolve_user_legacy(entry, apollo_state, root_user_id)
        if not user_info:
            continue

        tweet = build_thread_tweet(legacy_data, user_info)
        tweets.append(tweet)
        seen_ids.add(tweet_id_str)

    tweets.sort(key=lambda item: (item.created_at, item.id_str))
    return tweets


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def iter_tweet_nodes(entries: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for value in entries:
        if not isinstance(value, dict):
            continue
        if value.get("__typename") == "Tweet":
            yield value
            continue
        candidate = value.get("result")
        if isinstance(candidate, dict) and candidate.get("__typename") == "Tweet":
            yield candidate
            continue
        nested = value.get("tweet_results")
        if isinstance(nested, dict):
            result = nested.get("result")
            if isinstance(result, dict) and result.get("__typename") == "Tweet":
                yield result


def locate_tweet_entry(apollo_state: dict[str, Any], tweet_id: str) -> Tuple[str, dict[str, Any]]:
    candidates: list[Tuple[str, dict[str, Any]]] = []

    for key, value in apollo_state.items():
        if not isinstance(value, dict):
            continue
        rest_id = value.get("rest_id") or value.get("id_str") or value.get("id")
        if rest_id == tweet_id:
            candidates.append((key, value))
            continue
        legacy = value.get("legacy")
        if isinstance(legacy, dict) and legacy.get("id_str") == tweet_id:
            candidates.append((key, value))

    if candidates:
        # Prefer a canonical Tweet:<id> entry if present.
        for key, value in candidates:
            if key.startswith("Tweet:"):
                return key, value
        return candidates[0]

    raise ValueError(f"Tweet {tweet_id} not found in apolloState store.")


def resolve_legacy_tweet(entry: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the 'legacy' payload regardless of nesting shape."""

    if not isinstance(entry, dict):
        return None

    if "legacy" in entry and isinstance(entry["legacy"], dict):
        return entry["legacy"]

    # Newer payloads wrap the tweet under tweet_results/result
    if "tweet_results" in entry:
        return resolve_legacy_tweet(entry["tweet_results"].get("result", {}))

    result = entry.get("result")
    if isinstance(result, dict):
        return resolve_legacy_tweet(result)

    return None


def resolve_user_legacy(
    entry: dict[str, Any],
    apollo_state: dict[str, Any],
    user_id: str,
) -> Optional[dict[str, Any]]:
    """Locate the author details accompanying *entry*."""

    def _extract(user_candidate: Any) -> Optional[dict[str, Any]]:
        if not isinstance(user_candidate, dict):
            return None
        if user_candidate.get("__typename") == "User" and isinstance(user_candidate.get("legacy"), dict):
            return user_candidate["legacy"]
        if "user" in user_candidate:
            return _extract(user_candidate["user"])
        if "result" in user_candidate:
            return _extract(user_candidate["result"])
        return None

    core = entry.get("core")
    if isinstance(core, dict):
        user_result = core.get("user_results")
        if isinstance(user_result, dict):
            legacy = _extract(user_result.get("result"))
            if legacy:
                return legacy

    author = entry.get("author_results")
    if isinstance(author, dict):
        legacy = _extract(author.get("result"))
        if legacy:
            return legacy

    fallback = apollo_state.get(f"User:{user_id}")
    if isinstance(fallback, dict) and isinstance(fallback.get("legacy"), dict):
        return fallback["legacy"]

    return None


def build_thread_tweet(legacy: dict[str, Any], user_legacy: dict[str, Any]) -> ThreadTweet:
    created_at = parse_twitter_datetime(legacy.get("created_at", ""))
    screen_name = user_legacy.get("screen_name") or "unknown"
    display_name = user_legacy.get("name") or screen_name
    tweet_id = legacy.get("id_str", "")
    permalink = f"https://x.com/{screen_name}/status/{tweet_id}"

    body_markdown = render_tweet_markdown(legacy)
    media = extract_media_assets(legacy)

    return ThreadTweet(
        id_str=tweet_id,
        created_at=created_at,
        author_screen_name=screen_name,
        author_display_name=display_name,
        permalink=permalink,
        body_markdown=body_markdown.strip(),
        media=media,
    )


def parse_twitter_datetime(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return datetime.utcnow().replace(microsecond=0)


def render_tweet_markdown(legacy: dict[str, Any]) -> str:
    text = legacy.get("full_text") or legacy.get("text") or ""
    text = html.unescape(text)

    entities = legacy.get("entities", {})
    replacements: list[tuple[int, int, str]] = []

    replacements.extend(url_replacements(entities.get("urls", [])))
    replacements.extend(media_replacements(entities.get("media", [])))
    replacements.extend(mention_replacements(entities.get("user_mentions", [])))
    replacements.extend(hashtag_replacements(entities.get("hashtags", [])))
    replacements.extend(symbol_replacements(entities.get("symbols", [])))

    for start, end, value in sorted(replacements, key=lambda item: item[0], reverse=True):
        if 0 <= start <= end <= len(text):
            text = text[:start] + value + text[end:]

    return text.strip()


def url_replacements(urls: Sequence[dict[str, Any]]) -> list[tuple[int, int, str]]:
    items = []
    for item in urls or []:
        try:
            start, end = item.get("indices", [])[0:2]
        except Exception:
            continue
        expanded = item.get("expanded_url") or item.get("url")
        display = item.get("display_url") or expanded
        if not expanded or display is None:
            continue
        items.append((start, end, f"[{display}]({expanded})"))
    return items


def media_replacements(media_items: Sequence[dict[str, Any]]) -> list[tuple[int, int, str]]:
    items = []
    for media in media_items or []:
        indices = media.get("indices") or []
        if len(indices) != 2:
            continue
        expanded = media.get("expanded_url") or media.get("url")
        display = media.get("display_url") or expanded
        if not expanded or display is None:
            continue
        items.append((indices[0], indices[1], f"[{display}]({expanded})"))
    return items


def mention_replacements(mentions: Sequence[dict[str, Any]]) -> list[tuple[int, int, str]]:
    items = []
    for mention in mentions or []:
        indices = mention.get("indices") or []
        if len(indices) != 2:
            continue
        screen = mention.get("screen_name")
        if not screen:
            continue
        items.append((indices[0], indices[1], f"[@{screen}](https://x.com/{screen})"))
    return items


def hashtag_replacements(tags: Sequence[dict[str, Any]]) -> list[tuple[int, int, str]]:
    items = []
    for tag in tags or []:
        indices = tag.get("indices") or []
        if len(indices) != 2:
            continue
        text_value = tag.get("text")
        if not text_value:
            continue
        link = f"https://x.com/hashtag/{text_value}"
        items.append((indices[0], indices[1], f"[#{text_value}]({link})"))
    return items


def symbol_replacements(symbols: Sequence[dict[str, Any]]) -> list[tuple[int, int, str]]:
    items = []
    for symbol in symbols or []:
        indices = symbol.get("indices") or []
        if len(indices) != 2:
            continue
        text_value = symbol.get("text")
        if not text_value:
            continue
        link = f"https://x.com/search?q=%24{text_value}"
        items.append((indices[0], indices[1], f"[${text_value}]({link})"))
    return items


def extract_media_assets(legacy: dict[str, Any]) -> list[MediaAsset]:
    assets: list[MediaAsset] = []
    extended = legacy.get("extended_entities", {})
    for media in extended.get("media", []) or []:
        media_type = media.get("type") or "photo"
        if media_type == "photo":
            url = media.get("media_url_https") or media.get("media_url")
        else:
            url = highest_bitrate_variant(media.get("video_info", {}))
        if not url:
            continue
        assets.append(
            MediaAsset(
                url=url,
                type=media_type,
                alt_text=media.get("ext_alt_text"),
            )
        )
    return assets


def highest_bitrate_variant(video_info: dict[str, Any]) -> Optional[str]:
    variants = video_info.get("variants") if isinstance(video_info, dict) else None
    if not variants:
        return None
    mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4:
        return None
    mp4.sort(key=lambda item: item.get("bitrate", 0))
    return mp4[-1]["url"].split("?")[0]


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "thread"


def derive_filename(thread: Sequence[ThreadTweet]) -> str:
    if not thread:
        return "thread.md"
    root = thread[0]
    base = f"{root.author_screen_name}-{root.id_str}"
    return f"{slugify(base)}.md"


def render_thread_markdown(thread: Sequence[ThreadTweet], source_url: str) -> str:
    if not thread:
        raise ValueError("Thread is empty; nothing to render.")

    author = thread[0].author_screen_name
    title = f"Thread by @{author}"

    lines: list[str] = [f"# {title}", "", f"- Source: [{source_url}]({source_url})", f"- Tweets captured: {len(thread)}", ""]

    for index, tweet in enumerate(thread, start=1):
        lines.append(f"## Tweet {index}")
        lines.append("")
        lines.append(f"**Author:** {tweet.author_display_name} (@{tweet.author_screen_name})")
        lines.append(
            f"**Posted:** {tweet.created_at.strftime('%Y-%m-%d %H:%M %Z') if tweet.created_at.tzinfo else tweet.created_at.isoformat()}"
        )
        lines.append(f"**Link:** [{tweet.permalink}]({tweet.permalink})")
        lines.append("")
        lines.append(tweet.body_markdown)

        if tweet.media:
            lines.append("")
            for asset in tweet.media:
                if asset.type == "photo":
                    alt = (asset.alt_text or "").replace("\n", " ").strip()
                    lines.append(f"![{alt}]({asset.url})")
                else:
                    label = asset.alt_text or asset.type.title()
                    lines.append(f"[{label}]({asset.url})")

        if index != len(thread):
            lines.extend(["", "---", ""])
        else:
            lines.append("")

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_tweet_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path or ""
    match = re.search(r"/status/(\d+)", path)
    if not match:
        raise ValueError("URL does not contain a tweet status identifier.")
    tweet_id = match.group(1)
    segments = [segment for segment in path.split("/") if segment]
    username = segments[0] if segments else ""
    return username, tweet_id


def convert_thread(url: str, *, session: dict[str, str]) -> ThreadExport:
    """Produce a Markdown export for *url* without writing to disk."""

    cookies = normalize_session_cookies(session)
    _, tweet_id = parse_tweet_url(url)

    html_text = fetch_html(url, cookies)
    next_data = extract_next_data(html_text)
    apollo_state = extract_apollo_state(next_data)
    thread = parse_thread(apollo_state, tweet_id)

    if not thread:
        raise ValueError("No tweets found in the thread (is it private or deleted?).")

    markdown = render_thread_markdown(thread, source_url=url)
    filename = derive_filename(thread)

    return ThreadExport(
        filename=filename,
        markdown=markdown,
        tweet_count=len(thread),
        author=thread[0].author_screen_name,
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Export a Twitter/X thread to Markdown.")
    parser.add_argument("--url", required=True, help="Tweet URL that anchors the thread (any bookmark URL).")
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory where the Markdown file will be written (default: tweet_exports).",
    )
    args = parser.parse_args(argv)

    url = args.url.strip()

    cookies = ensure_session_cookies()
    try:
        export = convert_thread(url, session=cookies)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / export.filename
    output_path.write_text(export.markdown, encoding="utf-8")

    print(f"Saved thread with {export.tweet_count} tweets to {output_path}")


if __name__ == "__main__":
    file = convert_thread(url="https://x.com/divya_venn/status/1973475113824858546", 
                   session={"auth_token": "70f13c5e0eff68a5644137ae5233fbee0cb5d92c", 
                            "ct0": "b03cc2f2b76a331374ec3b658c10b05b2463249f97a319669e9edb0a8ed42727d880649b46fed518cb23165c278d8350ec80e17eed6d63b6dab7c80117f77371441a624cb4d68aa9a85b73ebe853d4aa"})
    
    output_dir = Path('./tweet_exports')
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / file.filename
    output_path.write_text(file.markdown, encoding="utf-8")