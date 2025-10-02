from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import requests

from typing import Any

from scrapers.substack import convert_html_to_markdown, derive_filename, fetch_html
from scrapers.tweet import convert_tweet, slugify


class ConvertRequest(BaseModel):
    url: HttpUrl
    substack_sid: str | None = None
    filename: str | None = None
    auth_token: str | None = None
    ct0: str | None = None


app = FastAPI(title="Markdown.load API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)



def format_cookies_twitter(cookies: dict[str, str]) -> dict[str, Any]:
    formatted: list[dict[str, Any]] = []

    for name, value in cookies.items():
        if not value:
            continue

        entry = {
            "name": name,
            "value": value,
            "domain": ".x.com",
            "path": "/",
            "secure": True,
        }

        if name == "auth_token":
            entry["httpOnly"] = True
            entry["sameSite"] = "None"
        elif name == "ct0":
            entry["httpOnly"] = False
            entry["sameSite"] = "Lax"
        else:
            entry["httpOnly"] = False
            entry["sameSite"] = "Lax"

        formatted.append(entry)

    return {
        "cookies": formatted,
        "origins": [
        {
            "origin": "https://x.com",
            "localStorage": [
                {"name": "__cuid", "value": "2e3070e784c04ad19d8eac5ab6f232c2"},
            ],
        },
    ]
    }


@app.post("/convert-tweet", response_class=Response)
async def download_tweet(payload: ConvertRequest) -> Response:
    url = str(payload.url)
    auth_token = (payload.auth_token or "").strip()
    ct0 = (payload.ct0 or "").strip()

    if not auth_token or not ct0:
        raise HTTPException(status_code=400, detail="Both twitter_auth_token and twitter_ct0 cookies are required")

    storage_state = format_cookies_twitter({"auth_token": auth_token, "ct0": ct0})

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
    cookie_value = (payload.substack_sid or "").strip()

    cookies = {"substack.sid": cookie_value} if cookie_value else {}

    try:
        html = fetch_html(url, cookies=cookies)
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
