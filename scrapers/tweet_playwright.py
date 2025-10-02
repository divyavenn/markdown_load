"""Playwright helpers for exporting Twitter threads.

This module complements :mod:`scrapers.tweet` by offering an async helper that
spins up a Playwright browser using caller-provided authentication state. The
Chrome extension (or any API client) can pass the cookies / storage state it
already captured so no persistent browser context has to be reused between
requests.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Mapping

from playwright.async_api import async_playwright


TWEET_DETAIL_RE = re.compile(r"/i/api/graphql/[^/]+/TweetDetail")

cookies_correct = {
    "cookies": [
        {
            "name": "guest_id_marketing",
            "value": "v1%3A175869347095644430",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253481.572654,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "guest_id_ads",
            "value": "v1%3A175869347095644430",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253481.572621,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "guest_id",
            "value": "v1%3A175869347095644430",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253470.991413,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "personalization_id",
            "value": "\"v1_kzGROIanhON2SDRSEuhYXg==\"",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253471.025645,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "gt",
            "value": "1970729370567483624",
            "domain": ".x.com",
            "path": "/",
            "expires": 1758702471.025659,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "__cuid",
            "value": "2e3070e784c04ad19d8eac5ab6f232c2",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253481,
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
        {
            "name": "guest_id_marketing",
            "value": "v1%3A175869347210650690",
            "domain": ".twitter.com",
            "path": "/",
            "expires": 1793253472.111689,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "guest_id_ads",
            "value": "v1%3A175869347210650690",
            "domain": ".twitter.com",
            "path": "/",
            "expires": 1793253472.11175,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "personalization_id",
            "value": "\"v1_IkNrJsdzyZ3q/3q6As/rmw==\"",
            "domain": ".twitter.com",
            "path": "/",
            "expires": 1793253472.11179,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "guest_id",
            "value": "v1%3A175869347210650690",
            "domain": ".twitter.com",
            "path": "/",
            "expires": 1793253472.111882,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "NID",
            "value": "525=W_72drQWyJ0uPDhoebAqx13UvDZwb4BAsn0jwucKmlnkakRHHXV9JB0GEOnEJ78mi5OSTICgzyDEDBAGnS2-RWmqi1mMT0FyAUafx4ffo5vyuR3meH8HUxeIms2GAQ524ZocVAGH2nWT_hljstDQ5ivzU5VYwYtQtOZmjezbHPFdlxMFinGbIYroIORCaTp9jLgGags",
            "domain": ".google.com",
            "path": "/",
            "expires": 1774504677.22375,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "kdt",
            "value": "AcSFh8S0J5XEvpu933COY9KiaNoglYVkF042Vxtu",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253480.373467,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "auth_token",
            "value": "c3d07db6b16132ddd9660845a6464fc3d27654d2",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253480.373736,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "ct0",
            "value": "291eb92fc8a8f6b43a456ae0578beee7202f2ec1b288ea93b18a7b4712f30e81bdfd8604894effe06b4e538e106e46f4014cb39b281890d51ea421074c321aafba2faf106bd922be098bbdf211137299",
            "domain": ".x.com",
            "path": "/",
            "expires": 1793253480.616078,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "att",
            "value": "1-4NR9roduAyylhEZe7NxHBYVE1VxrMUuADFV5sAKw",
            "domain": ".x.com",
            "path": "/",
            "expires": 1758779880.769226,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "lang",
            "value": "en",
            "domain": "x.com",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
        {
            "name": "twid",
            "value": "u%3D1689356162716610560",
            "domain": ".x.com",
            "path": "/",
            "expires": 1790229481.572681,
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "IDE",
            "value": "AHWqTUlN6QIo0GryJZgwYyHlqALmDkM029D3GJD597Zh83okFkj5hWPpg9CV3v4dq6A",
            "domain": ".doubleclick.net",
            "path": "/",
            "expires": 1793253481.651631,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
    ],
    "origins": [
        {
            "origin": "https://x.com",
            "localStorage": [
                {"name": "__cuid", "value": "2e3070e784c04ad19d8eac5ab6f232c2"},
            ],
        },
    ],
}



def cookie_still_valid(state: dict[str, Any]) -> bool:
    import time
    if not isinstance(state, dict):
        return False
    for c in state.get("cookies", []):
        if c.get("name") == 'auth_token':
            return c.get("expires", 0) == 0 or c["expires"] > time.time() + 60
    return False


async def get_browser(cookies : dict[str, str] | None = None) :
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    session = cookies or cookies_correct
    ctx = await browser.new_context(storage_state=session)
    return browser, ctx


async def get_thread(tweet_url: str, root_id: str | None = None, cookies : dict[str, str] | None = None) -> list[str]:
    browser, ctx = await get_browser(cookies=cookies)
    page = await ctx.new_page()

    results: list[str] = []
    root_author_id: str | None = None

    def extract_text(node: Mapping[str, Any]) -> str:
        legacy = node.get("legacy") or {}
        note_text = (
            (node.get("note_tweet") or {})
            
            .get("note_tweet_results", {})
            .get("result", {})
            .get("text")
        )
        if note_text:
            return note_text

        txt = legacy.get("full_text") or legacy.get("text")
        return txt or ""

    async def on_response(resp):
        nonlocal root_author_id
        if not (TWEET_DETAIL_RE.search(resp.url) and resp.ok):
            return
        try:
            data = await resp.json()
        except Exception:
            return

        # Collect instructions from both containers
        instructions = []
        tc_v2 = (data.get("data") or {}).get("threaded_conversation_with_injections_v2") or {}
        instructions.extend(tc_v2.get("instructions", []) or [])
        tc_v1 = (data.get("data") or {}).get("threaded_conversation_with_injections") or {}
        instructions.extend(tc_v1.get("instructions", []) or [])

        for inst in instructions:
            for entry in inst.get("entries", []) or []:
                content = entry.get("content") or {}

                # Candidate shapes containing tweets
                candidates = []
                ic = content.get("itemContent") or {}
                if ic:
                    candidates.append(ic)
                ic2 = (content.get("item") or {}).get("itemContent") or {}
                if ic2:
                    candidates.append(ic2)
                for it in (content.get("items") or content.get("moduleItems") or []):
                    cand = (it.get("item") or {}).get("itemContent") or it.get("itemContent") or {}
                    if cand:
                        candidates.append(cand)

                for cand in candidates:
                    raw = (cand.get("tweet_results") or {}).get("result")
                    if not isinstance(raw, dict):
                        continue
                    node = raw.get("tweet") or raw
                    legacy = node.get("legacy") or {}
                    if not legacy:
                        continue

                    tid = legacy.get("id_str") or str(node.get("rest_id") or "")
                    uid = legacy.get("user_id_str")
                    if not tid or not uid:
                        continue

                    # Resolve root author from the focal tweet if possible
                    if root_author_id is None:
                        if root_id and tid == str(root_id):
                            root_author_id = uid
                        elif not root_id:
                            # No explicit root tweet id provided; infer from first seen item
                            root_author_id = uid

                    # Keep only tweets by the root author that reply **only** to the root author
                    if root_author_id:
                        allow = False
                        # Always allow the focal/root tweet if provided
                        if root_id and tid == str(root_id):
                            allow = True
                        else:
                            reply_to_uid = legacy.get("in_reply_to_user_id_str")
                            mentions = (legacy.get("entities") or {}).get("user_mentions") or []
                            mention_ids = [m.get("id_str") for m in mentions if isinstance(m, dict) and m.get("id_str")]
                            # Only the root author may be mentioned (or none mentioned)
                            only_author_mentioned = (len(mention_ids) == 0) or (len(mention_ids) == 1 and mention_ids[0] == root_author_id)
                            if (uid == root_author_id and reply_to_uid == root_author_id and only_author_mentioned):
                                allow = True
                        if allow:
                            text = extract_text(node)
                            if text:
                                results.append(text)

    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    try:
        await page.goto(tweet_url, wait_until="domcontentloaded")
        # Wait for at least one TweetDetail to arrive
        try:
            await page.wait_for_event(
                "response",
                predicate=lambda r: TWEET_DETAIL_RE.search(r.url),
                timeout=30_000,
            )
        except Exception:
            pass
        # Nudge to load more thread items
        for _ in range(4):
            try:
                await page.mouse.wheel(0, 2200)
            except Exception:
                pass
            await asyncio.sleep(0.2)
    finally:
        await page.close()

    return results

