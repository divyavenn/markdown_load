from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
import requests

from typing import Any, Awaitable, Callable, Dict

from scrapers.substack import convert_html_to_markdown, derive_filename, fetch_html
from scrapers.tweet import convert_tweet, slugify
from scrapers.pdf import convert_pdf_path, convert_pdf_bytes
from scrapers.article import fetch_article_markdown
from scrapers.youtube import convert_youtube


class ConvertRequest(BaseModel):
    url: HttpUrl
    filename: str | None = None
    cookies: dict[str, Any] = Field(default_factory=dict)
    html: str | None = None


def cookies_to_lookup(cookies: dict[str, Any]) -> dict[str, str]:
    return {
        str(name): str(value)
        for name, value in cookies.items()
        if value is not None
    }


def cookies_to_storage_state(cookies: dict[str, str]) -> dict[str, Any]:
    same_site_map = {
        'no_restriction': 'None',
        'none': 'None',
        'unspecified': 'None',
        'lax': 'Lax',
        'strict': 'Strict',
    }

    def build_cookie(name: str, http_only: bool) -> dict[str, Any]:
        state_cookie: dict[str, Any] = {
            'name': name,
            'value': cookies[name],
            'domain': '.x.com',
            'path': '/',
            'secure': True,
            'httpOnly': http_only,
        }
        same_site_raw = cookies.get(f'{name}_same_site')
        if same_site_raw:
            same_site = same_site_map.get(str(same_site_raw).lower())
            if same_site:
                state_cookie['sameSite'] = same_site
        expires_raw = cookies.get(f'{name}_expires')
        if expires_raw:
            state_cookie['expires'] = expires_raw
        return state_cookie

    playwright_cookies: list[dict[str, Any]] = []
    if 'auth_token' in cookies:
        playwright_cookies.append(build_cookie('auth_token', http_only=True))
    if 'ct0' in cookies:
        playwright_cookies.append(build_cookie('ct0', http_only=False))

    return {'cookies': playwright_cookies, 'origins': []}


def derive_article_filename(url: str) -> str:
    parsed = urlparse(url)
    stem = Path(parsed.path).stem
    candidate = stem or (parsed.hostname or 'article')
    safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '-' for ch in candidate)
    safe = safe.strip('-_') or 'article'
    return f"{safe}.md"


def derive_youtube_filename(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    video_id = query.get('v', [None])[0]
    if not video_id:
        video_id = parsed.path.rstrip('/').split('/')[-1] or 'youtube-video'
    safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '-' for ch in video_id)
    safe = safe.strip('-_') or 'youtube-video'
    return f"{safe}.md"


def choose_filename(provided: str | None, fallback: str) -> str:
    base = (provided or '').strip()
    if not base:
        base = fallback.strip()
    if not base:
        base = 'document'
    return base if base.lower().endswith('.md') else f"{base}.md"


jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = asyncio.Lock()


async def set_job_status(job_id: str, status: str, result: Dict[str, str] | None = None, error: str | None = None) -> None:
    async with jobs_lock:
        record = jobs.get(job_id)
        if record is None:
            return
        record['status'] = status
        record['result'] = result
        record['error'] = error
        record['updated_at'] = time.time()


async def enqueue_job(task: Callable[[], Awaitable[Dict[str, str]]]) -> Dict[str, str]:
    job_id = uuid4().hex
    now = time.time()
    async with jobs_lock:
        jobs[job_id] = {
            'status': 'processing',
            'result': None,
            'error': None,
            'created_at': now,
            'updated_at': now,
        }

    async def runner() -> None:
        try:
            result = await task()
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            await set_job_status(job_id, 'error', error=detail)
        except Exception as exc:
            await set_job_status(job_id, 'error', error=str(exc))
        else:
            await set_job_status(job_id, 'ready', result=result)

    asyncio.create_task(runner())
    return {'jobId': job_id, 'status': 'processing'}


def convert_remote_pdf_sync(url: str, provided_filename: str | None, cookies: Dict[str, str]) -> Dict[str, str]:
    response = None
    temp_path: str | None = None
    try:
        response = requests.get(url, stream=True, timeout=60, cookies=cookies or None)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            temp_path = tmp.name
            for chunk in response.iter_content(chunk_size=1 << 20):
                if chunk:
                    tmp.write(chunk)

        if temp_path is None or os.path.getsize(temp_path) == 0:
            raise HTTPException(status_code=400, detail="Fetched PDF is empty.")

        markdown = convert_pdf_path(temp_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF conversion failed: {exc}") from exc
    finally:
        if response is not None:
            response.close()
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    fallback = Path(urlparse(url).path).stem or "document"
    filename = choose_filename(provided_filename, fallback)
    return {'markdown': markdown, 'filename': filename}


def convert_pdf_stream_sync(data: bytes, provided_filename: str | None, original_name: str | None) -> Dict[str, str]:
    if not data:
        raise HTTPException(status_code=400, detail="No PDF content received.")

    try:
        markdown = convert_pdf_bytes(data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF conversion failed: {exc}") from exc

    source_name = original_name or "document.pdf"
    fallback = Path(source_name).stem or "document"
    filename = choose_filename(provided_filename, fallback)
    return {'markdown': markdown, 'filename': filename}


def convert_article_sync(url: str, html: str | None, provided_filename: str | None) -> Dict[str, str]:
    try:
        markdown = fetch_article_markdown(url=url, html=html)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Article conversion failed: {exc}") from exc

    fallback = derive_article_filename(url)
    filename = choose_filename(provided_filename, fallback)
    return {'markdown': markdown, 'filename': filename}


async def convert_youtube_async(url: str, provided_filename: str | None) -> Dict[str, str]:
    try:
        markdown = await convert_youtube(url)
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"YouTube conversion failed: {exc}") from exc

    fallback = derive_youtube_filename(url)
    filename = choose_filename(provided_filename, fallback)
    return {'markdown': markdown, 'filename': filename}


async def convert_tweet_async(url: str, provided_filename: str | None, storage_state: Dict[str, Any]) -> Dict[str, str]:
    try:
        markdown, handle, root_id = await convert_tweet(url=url, cookies=storage_state)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    base = slugify(handle + '-' + root_id) or root_id or 'tweet'
    filename = choose_filename(provided_filename, base)
    return {'markdown': markdown, 'filename': filename}


def convert_substack_sync(url: str, provided_filename: str | None, cookies: Dict[str, str], html: str | None) -> Dict[str, str]:
    try:
        html_source = html or fetch_html(url, cookies=cookies)
        markdown, metadata = convert_html_to_markdown(html_source, url)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else 502
        raise HTTPException(status_code=status_code, detail=f"Substack request failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    fallback = derive_filename(url, metadata.get('title', ''))
    filename = choose_filename(provided_filename, fallback)
    return {'markdown': markdown, 'filename': filename}
app = FastAPI(title="Markdown.load API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    async with jobs_lock:
        record = jobs.get(job_id)

    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    payload: Dict[str, Any] = {
        'jobId': job_id,
        'status': record['status'],
    }

    if record['status'] == 'ready' and record['result']:
        payload['markdown'] = record['result']['markdown']
        payload['filename'] = record['result']['filename']
    elif record['status'] == 'error':
        payload['error'] = record['error'] or 'Conversion failed'

    return payload


@app.post("/convert-pdf", status_code=status.HTTP_202_ACCEPTED)
async def download_pdf(payload: ConvertRequest) -> Dict[str, str]:
    url = str(payload.url)
    cookie_lookup = cookies_to_lookup(payload.cookies)
    provided_filename = payload.filename

    async def task() -> Dict[str, str]:
        return await asyncio.to_thread(
            convert_remote_pdf_sync,
            url,
            provided_filename,
            cookie_lookup,
        )

    return await enqueue_job(task)


@app.post("/convert-pdf/stream", status_code=status.HTTP_202_ACCEPTED)
async def upload_pdf(file: UploadFile = File(...), filename: str | None = Form(None)) -> Dict[str, str]:
    original_name = file.filename
    try:
        data = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read uploaded PDF: {exc}") from exc
    finally:
        await file.close()

    if not data:
        raise HTTPException(status_code=400, detail="No PDF content received.")

    provided_filename = filename

    async def task() -> Dict[str, str]:
        return await asyncio.to_thread(
            convert_pdf_stream_sync,
            data,
            provided_filename,
            original_name,
        )

    return await enqueue_job(task)


@app.post("/convert-article", status_code=status.HTTP_202_ACCEPTED)
async def download_article(payload: ConvertRequest) -> Dict[str, str]:
    url = str(payload.url)
    provided_html = payload.html
    provided_filename = payload.filename

    async def task() -> Dict[str, str]:
        return await asyncio.to_thread(
            convert_article_sync,
            url,
            provided_html,
            provided_filename,
        )

    return await enqueue_job(task)


@app.post("/convert-youtube", status_code=status.HTTP_202_ACCEPTED)
async def download_youtube(payload: ConvertRequest) -> Dict[str, str]:
    url = str(payload.url)
    provided_filename = payload.filename

    async def task() -> Dict[str, str]:
        return await convert_youtube_async(url, provided_filename)

    return await enqueue_job(task)


@app.post("/convert-tweet", status_code=status.HTTP_202_ACCEPTED)
async def download_tweet(payload: ConvertRequest) -> Dict[str, str]:
    url = str(payload.url)
    cookie_lookup = cookies_to_lookup(payload.cookies)
    storage_state = cookies_to_storage_state(cookie_lookup)

    if not cookie_lookup.get("auth_token") or not cookie_lookup.get("ct0"):
        raise HTTPException(status_code=400, detail="Both auth_token and ct0 cookies are required to export this thread.")

    provided_filename = payload.filename

    async def task() -> Dict[str, str]:
        return await convert_tweet_async(url, provided_filename, storage_state)

    return await enqueue_job(task)


@app.post("/convert-substack", status_code=status.HTTP_202_ACCEPTED)
async def download_substack(payload: ConvertRequest) -> Dict[str, str]:
    url = str(payload.url)
    cookie_lookup = cookies_to_lookup(payload.cookies)
    provided_html = payload.html
    provided_filename = payload.filename

    async def task() -> Dict[str, str]:
        return await asyncio.to_thread(
            convert_substack_sync,
            url,
            provided_filename,
            cookie_lookup,
            provided_html,
        )

    return await enqueue_job(task)
