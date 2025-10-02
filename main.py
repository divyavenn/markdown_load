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
    filename: str | None = None
    cookies: dict[str, Any] = {}


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
    storage_state = payload.cookies
    
    if not storage_state.get("auth_token") or not storage_state.get("ct0"):
        raise HTTPException(status_code=400, detail="Both twitter_auth_token and twitter_ct0 cookies are required")

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
    storage_state = payload.cookies
    print (storage_state)

    if not storage_state.get("substack_sid"):
        raise HTTPException(status_code=400, detail="substack_sid cookie is required")


    try:
        html = fetch_html(url, cookies=storage_state)
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
