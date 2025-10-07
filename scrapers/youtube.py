#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import sys
from dataclasses import dataclass
import tempfile
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _create_cookie_file(cookies: Dict[str, str], url: str) -> Path:
    """Create a Netscape cookie file for yt-dlp from a dictionary of cookies."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = parsed.hostname or "youtube.com"
    # Ensure domain starts with a dot for wildcard matching
    domain = f".{hostname}" if not hostname.startswith(".") else hostname

    # Create temporary cookie file in Netscape format
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix="yt_cookies_", delete=False) as tmp:
        cookie_file = Path(tmp.name)
        tmp.write("# Netscape HTTP Cookie File\n")
        tmp.write("# This file was generated for yt-dlp\n")
        tmp.write("# https://curl.haxx.se/rfc/cookie_spec.html\n")
        tmp.write("# This is a generated file! Do not edit.\n\n")

        for name, value in cookies.items():
            # Skip cookie metadata fields (like _same_site, _expires)
            if name.endswith("_same_site") or name.endswith("_expires"):
                continue

            # Netscape format has 7 tab-separated fields:
            # domain | flag | path | secure | expiration | name | value
            # Example: .youtube.com	TRUE	/	TRUE	2147483647	CONSENT	YES+1

            # Use a far future expiration (year 2038)
            expiration = "2147483647"

            # For YouTube cookies, use HTTPS
            secure = "TRUE"

            # Write the cookie line with proper tab separation
            tmp.write(f"{domain}\tTRUE\t/\t{secure}\t{expiration}\t{name}\t{value}\n")

    return cookie_file


@dataclass
class VideoSelection:
    video_id: str
    title: str
    chosen_lang: Optional[str]


def extract_video_info(url: str, cookies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:
        raise SystemExit(
            "yt-dlp is required. Install with: pip install yt-dlp"
        ) from exc

    opts: Dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    # Add cookie support
    cookie_file = None
    if cookies:
        cookie_file = _create_cookie_file(cookies, url)
        opts["cookiefile"] = str(cookie_file)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return info
    finally:
        if cookie_file and cookie_file.exists():
            cookie_file.unlink()


def select_human_subtitle_lang(info: Dict[str, Any], preferred_lang: Optional[str]) -> Optional[str]:
    human_subs: Dict[str, Any] = info.get("subtitles") or {}
    if not human_subs:
        return None

    if preferred_lang and preferred_lang in human_subs:
        return preferred_lang

    # Fallback to first available human subtitle language
    for lang in human_subs.keys():
        return lang

    return None


def download_human_subtitles(url: str, out_dir: Path, video_id: str, lang: str, cookies: Optional[Dict[str, str]] = None) -> Path:
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:
        raise SystemExit(
            "yt-dlp is required. Install with: pip install yt-dlp"
        ) from exc

    ensure_directory(out_dir)
    outtmpl = str(out_dir / f"{video_id}.%(subtitle_lang)s.%(ext)s")
    opts: Dict[str, Any] = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
    }

    # Add cookie support
    cookie_file = None
    if cookies:
        cookie_file = _create_cookie_file(cookies, url)
        opts["cookiefile"] = str(cookie_file)

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    finally:
        if cookie_file and cookie_file.exists():
            cookie_file.unlink()

    vtt_path = out_dir / f"{video_id}.NA.{lang}.vtt"
    return vtt_path


def vtt_to_text(vtt_path: Path) -> str:
    try:
        import webvtt  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "webvtt-py is required to parse VTT. Install with: pip install webvtt-py"
        ) from exc

    lines: list[str] = []
    for caption in webvtt.read(str(vtt_path)):
        text = caption.text.replace("\n", " ").strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def download_audio(url: str, out_dir: Path, video_id: str, cookies: Optional[Dict[str, str]] = None) -> Path:
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:
        raise RuntimeError(
            "yt-dlp is required. Install with: pip install yt-dlp"
        ) from exc

    ensure_directory(out_dir)
    # Download best audio as-is (container like m4a/webm). Whisper will decode via ffmpeg.
    outtmpl = str(out_dir / f"{video_id}.%(ext)s")
    opts: Dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": False,  # Enable output for debugging
        "no_warnings": False,
    }

    # Add cookie support
    cookie_file = None
    if cookies:
        cookie_file = _create_cookie_file(cookies, url)
        opts["cookiefile"] = str(cookie_file)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Build the expected filename from the info and template
            filename = ydl.prepare_filename(info)
    except Exception as exc:
        raise RuntimeError(f"Failed to download audio from YouTube: {exc}") from exc
    finally:
        if cookie_file and cookie_file.exists():
            cookie_file.unlink()

    # If a video container was downloaded (e.g., .webm), prefer the produced file path
    audio_path = Path(filename)
    if not audio_path.exists():
        # Try common alternatives
        for ext in ("m4a", "webm", "mp3", "aac", "wav"):
            candidate = out_dir / f"{video_id}.{ext}"
            if candidate.exists():
                audio_path = candidate
                break
    if not audio_path.exists():
        raise RuntimeError("Failed to locate downloaded audio file.")
    return audio_path


def transcribe_with_openai_whisper_api(audio_path: Path, openai_api_key: str, language: Optional[str] = None) -> str:
    print("Using OpenAI Whisper API for transcription")
    try:
        from openai import OpenAI
    except Exception as exc:
        raise SystemExit(
            "openai package is required. Install with: pip install openai"
        ) from exc

    if not openai_api_key:
        raise ValueError("OpenAI API key is required for Whisper API transcription")

    client = OpenAI(api_key=openai_api_key)

    try:
        with open(audio_path, "rb") as audio_file:
            transcript_params: Dict[str, Any] = {
                "model": "whisper-1",
                "file": audio_file,
            }

            if language:
                transcript_params["language"] = language

            transcript = client.audio.transcriptions.create(**transcript_params)

        return transcript.text.strip()
    except Exception as exc:
        raise RuntimeError(f"OpenAI Whisper API transcription failed: {exc}") from exc


def transcribe_with_whisper(audio_path: Path, model_name: str = "small", language: Optional[str] = None) -> str:
    """
    Transcribe audio using faster-whisper (lightweight, quantized Whisper implementation).
    Install with: pip install faster-whisper
    """
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise SystemExit(
            "faster-whisper is required. Install with: pip install faster-whisper\n"
            "Note: ffmpeg must also be installed and on PATH."
        ) from exc

    model = None
    try:
        # load quantized model to reduce RAM usage
        model = WhisperModel("small", device="cpu", compute_type="int8")

        segments, info = model.transcribe(str(audio_path), language=language)
        text_lines = [segment.text.strip() for segment in segments if segment.text.strip()]
        result = "\n".join(text_lines).strip()
        return result

    except Exception as exc:
        raise RuntimeError(f"Faster-Whisper transcription failed: {exc}") from exc
    finally:
        # Explicitly clean up the model to prevent semaphore leaks
        if model is not None:
            del model


def build_markdown_transcript(
    title: str,
    source_url: str,
    language: Optional[str],
    body: str,
) -> str:
    clean_title = (title or "Transcript").strip() or "Transcript"
    parts: list[str] = [f"# {clean_title}"]

    metadata: list[str] = []
    if source_url:
        metadata.append(f"- Source: {source_url}")
    if language:
        metadata.append(f"- Language: `{language}`")

    if metadata:
        parts.append("")
        parts.extend(metadata)

    body_text = body.strip()
    if body_text:
        parts.append("")
        parts.append(body_text)

    parts.append("")
    return "\n".join(parts)


def _locate_vtt(out_dir: Path, video_id: str) -> Path:
    candidates = sorted(out_dir.glob(f"{video_id}*.vtt"))
    if not candidates:
        raise RuntimeError("Failed to locate downloaded subtitle file.")
    return candidates[0]


def fetch_youtube_markdown(
    url: str,
    *,
    preferred_lang: str = "en",
    whisper_model: str = "small",
    openai_api_key: str | None = None,
    cookies: Optional[Dict[str, str]] = None
) -> str:
    print(f"[YouTube] Starting conversion for: {url}")
    print(f"[YouTube] OpenAI API key provided: {bool(openai_api_key)}")
    print(f"[YouTube] Cookies provided: {bool(cookies)}")

    with tempfile.TemporaryDirectory(prefix="yt_", suffix="_extract") as tmp:
        out_dir = Path(tmp)
        ensure_directory(out_dir)

        print("[YouTube] Extracting video info...")
        info = extract_video_info(url, cookies=cookies)
        video_id = info.get("id") or "video"
        title = info.get("title") or video_id
        print(f"[YouTube] Video: {title} ({video_id})")

        chosen_lang = select_human_subtitle_lang(info, preferred_lang)

        if chosen_lang:
            print(f"[YouTube] Found human subtitles in language: {chosen_lang}")
            vtt_path = download_human_subtitles(url, out_dir, video_id, chosen_lang, cookies=cookies)
            try:
                subtitle_path = vtt_path if vtt_path.exists() else _locate_vtt(out_dir, video_id)
            except RuntimeError:
                subtitle_path = _locate_vtt(out_dir, video_id)
            plain_text = vtt_to_text(subtitle_path)
            print("[YouTube] Subtitles converted successfully")
            return build_markdown_transcript(title, url, chosen_lang, plain_text)

        print("[YouTube] No subtitles found, will need transcription")
        print(f"[YouTube] Checking openai_api_key: {openai_api_key is not None and openai_api_key != ''}")
        # If no subtitles available, we need transcription
        # Use OpenAI Whisper API if API key is provided, otherwise use local Whisper
        if openai_api_key and openai_api_key.strip():
            print("[YouTube] Using OpenAI Whisper API for transcription")
            print("[YouTube] Downloading audio...")
            audio_path = download_audio(url, out_dir, video_id, cookies=cookies)
            print(f"[YouTube] Audio downloaded to: {audio_path}")
            try:
                text = transcribe_with_openai_whisper_api(audio_path, openai_api_key, preferred_lang)
            except Exception:
                print("[YouTube] OpenAI did not work, starting local Whisper transcription (this may take a while)...")
                text = transcribe_with_whisper(audio_path, whisper_model, preferred_lang)
        else:
            print("[YouTube] Checking for local Whisper installation...")
            # Check if faster-whisper is available before downloading audio
            try:
                import importlib.util
                whisper_spec = importlib.util.find_spec("faster_whisper")
                if whisper_spec is not None:
                    print("[YouTube] Local Whisper (faster-whisper) found, using it for transcription")
                else:
                    raise ImportError("faster_whisper not found")
            except ImportError:
                raise RuntimeError(
                    "No subtitles found for this video. "
                    "Either provide an OpenAI API key in settings, or install faster-whisper locally: "
                    "pip install faster-whisper"
                )

            print("[YouTube] Downloading audio...")
            audio_path = download_audio(url, out_dir, video_id, cookies=cookies)
            print(f"[YouTube] Audio downloaded to: {audio_path}")
            print("[YouTube] Starting local Whisper transcription (this may take a while)...")
            text = transcribe_with_whisper(audio_path, whisper_model, preferred_lang)

        if not text:
            raise RuntimeError("Transcription produced empty output.")
        print("[YouTube] Conversion complete")
        return build_markdown_transcript(title, url, preferred_lang, text)


async def convert_youtube(url: str, openai_api_key: str | None = None, cookies: Optional[Dict[str, str]] = None) -> str:
    return await asyncio.to_thread(fetch_youtube_markdown, url, openai_api_key=openai_api_key, cookies=cookies)


def main(url) -> None:
    # Hard coded arguments
    out = "./documents"
    lang = "en"
    model = "small"

    out_dir = Path(out).absolute()
    ensure_directory(out_dir)

    info = extract_video_info(url)
    video_id = info.get("id") or "video"
    title = info.get("title") or video_id

    chosen_lang = select_human_subtitle_lang(info, lang)
    transcript_path = out_dir / f"{video_id}.transcript.md"

    if chosen_lang:
        print(f"Found human subtitles in language '{chosen_lang}'. Downloading…")
        vtt_path = download_human_subtitles(url, out_dir, video_id, chosen_lang)
        plain_text = vtt_to_text(vtt_path)
        markdown = build_markdown_transcript(title, url, chosen_lang, plain_text)
        transcript_path.write_text(markdown, encoding="utf-8")
        print(f"Transcript written to: {transcript_path}")
        return

    print("No human subtitles found. Downloading audio and transcribing with Whisper…")
    audio_path = download_audio(url, out_dir, video_id)
    text = transcribe_with_whisper(audio_path, model, lang)
    if not text:
        raise SystemExit("Transcription produced empty output.")
    markdown = build_markdown_transcript(title, url, lang, text)
    transcript_path.write_text(markdown, encoding="utf-8")
    print(f"Transcript written to: {transcript_path}")


if __name__ == "__main__":
    try:
        main("https://www.youtube.com/shorts/0-CwgMETPBc")
    except KeyboardInterrupt:
        print("Cancelled.")
        sys.exit(130)
