from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import requests

from scrapers.substack import convert_html_to_markdown, derive_filename, fetch_html


class ConvertRequest(BaseModel):
    url: HttpUrl
    substack_sid: str | None = None
    filename: str | None = None


app = FastAPI(title="Markdown.load API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)




@app.post("/convert", response_class=Response)
async def convert_post(payload: ConvertRequest) -> Response:
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
