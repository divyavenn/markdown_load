from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
import requests

from typing import Any

from scrapers.substack import convert_html_to_markdown, derive_filename, fetch_html
from scrapers.tweet import convert_tweet, slugify


class ConvertRequest(BaseModel):
    url: HttpUrl
    filename: str | None = None
    cookies: list[dict[str, Any]] = Field(default_factory=list)


def cookies_to_lookup(cookies: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for cookie in cookies:
        name = cookie.get('name')
        value = cookie.get('value')
        if name and value is not None:
            lookup[name] = str(value)
    return lookup


def cookies_to_storage_state(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    same_site_map = {
        'no_restriction': 'None',
        'none': 'None',
        'unspecified': 'None',
        'lax': 'Lax',
        'strict': 'Strict',
    }

    playwright_cookies: list[dict[str, Any]] = []
    for cookie in cookies:
        name = cookie.get('name')
        value = cookie.get('value')
        domain = cookie.get('domain')
        if not name or value is None or not domain:
            continue

        same_site_raw = cookie.get('sameSite')
        same_site = None
        if same_site_raw is not None:
            same_site = same_site_map.get(str(same_site_raw).lower())

        expires = cookie.get('expirationDate') or cookie.get('expires')

        state_cookie: dict[str, Any] = {
            'name': name,
            'value': str(value),
            'domain': domain,
            'path': cookie.get('path', '/'),
            'secure': bool(cookie.get('secure')),
            'httpOnly': bool(cookie.get('httpOnly')),
        }

        if same_site:
            state_cookie['sameSite'] = same_site
        if expires:
            state_cookie['expires'] = expires

        playwright_cookies.append(state_cookie)

    return {'cookies': playwright_cookies, 'origins': []}


app = FastAPI(title="Markdown.load API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)



@app.post("/convert-tweet", response_class=Response)
async def download_tweet(payload: ConvertRequest) -> Response:
    url = str(payload.url)
    cookie_lookup = cookies_to_lookup(payload.cookies)
    storage_state = cookies_to_storage_state(payload.cookies)

    if not cookie_lookup.get("auth_token") or not cookie_lookup.get("ct0"):
        raise HTTPException(status_code=400, detail="Both auth_token and ct0 cookies are required to export this thread.")

    try:
        markdown, handle, root_id = await convert_tweet(url=url, cookies=storage_state)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    default_filename = f"{slugify(handle+'-'+root_id) or root_id}.md"
    filename = payload.filename.strip() if payload.filename else default_filename
    if not filename.lower().endswith(".md"):
        filename += ".md"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    return Response(content=markdown, media_type="text/markdown", headers=headers)


@app.post("/convert-substack", response_class=Response)
async def download_substack(payload: ConvertRequest) -> Response:
    url = str(payload.url)
    cookie_lookup = cookies_to_lookup(payload.cookies)


    try:
        html = fetch_html(url, cookies=cookie_lookup)
        markdown, metadata = convert_html_to_markdown(html, url)
    except requests.HTTPError as exc:  # network / Substack failure
        raise HTTPException(status_code=exc.response.status_code if exc.response else 502,
                            detail=f"Substack request failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = payload.filename.strip() if payload.filename else derive_filename(url, metadata.get("title", ""))
    if not filename.lower().endswith(".md"):
        filename += ".md"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return Response(content=markdown, media_type="text/markdown", headers=headers)
