#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ingest YouTube content by preferring human captions; if unavailable, fallback to
downloading audio and transcribing locally with Whisper.

Requirements (install into your environment):
  pip install yt-dlp webvtt-py
  pip install openai-whisper   # or: pip install faster-whisper

Usage:
  python ingest_youtube.py --url "https://www.youtube.com/watch?v=VIDEO_ID" \
      --out ./documents --lang en --model small

Outputs:
  - Writes a Markdown transcript to <out>/<video_id>.transcript.md
  - If subtitles are present (human), exports them from VTT
  - Otherwise downloads audio and runs Whisper
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
import tempfile
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


@dataclass
class VideoSelection:
    video_id: str
    title: str
    chosen_lang: Optional[str]


def extract_video_info(url: str) -> Dict[str, Any]:
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

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info


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


def download_human_subtitles(url: str, out_dir: Path, video_id: str, lang: str) -> Path:
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
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

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


def download_audio(url: str, out_dir: Path, video_id: str) -> Path:
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
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Build the expected filename from the info and template
            filename = ydl.prepare_filename(info)
    except Exception as exc:
        raise RuntimeError(f"Failed to download audio from YouTube: {exc}") from exc

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


def transcribe_with_whisper(audio_path: Path, model_name: str, language: Optional[str]) -> str:
    # Prefer openai-whisper for simplicity; can be replaced with faster-whisper if desired
    try:
        import whisper  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "openai-whisper is required. Install with: pip install openai-whisper\n"
            "Note: ffmpeg must also be installed and on PATH."
        ) from exc

    try:
        model = whisper.load_model(model_name)
        result = model.transcribe(str(audio_path), language=language)
        text = result.get("text", "").strip()
        return text
    except Exception as exc:
        # Common failure: ffmpeg missing
        if "ffmpeg" in str(exc).lower():
            raise SystemExit(
                "ffmpeg is required by Whisper to decode audio.\n"
                "Install it (e.g., brew install ffmpeg) and retry."
            ) from exc
        raise


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
    openai_api_key: str | None = None
) -> str:
    print(f"[YouTube] Starting conversion for: {url}")
    with tempfile.TemporaryDirectory(prefix="yt_", suffix="_extract") as tmp:
        out_dir = Path(tmp)
        ensure_directory(out_dir)

        print(f"[YouTube] Extracting video info...")
        info = extract_video_info(url)
        video_id = info.get("id") or "video"
        title = info.get("title") or video_id
        print(f"[YouTube] Video: {title} ({video_id})")

        chosen_lang = select_human_subtitle_lang(info, preferred_lang)

        if chosen_lang:
            print(f"[YouTube] Found human subtitles in language: {chosen_lang}")
            vtt_path = download_human_subtitles(url, out_dir, video_id, chosen_lang)
            try:
                subtitle_path = vtt_path if vtt_path.exists() else _locate_vtt(out_dir, video_id)
            except RuntimeError:
                subtitle_path = _locate_vtt(out_dir, video_id)
            plain_text = vtt_to_text(subtitle_path)
            print(f"[YouTube] Subtitles converted successfully")
            return build_markdown_transcript(title, url, chosen_lang, plain_text)

        print(f"[YouTube] No subtitles found, will need transcription")
        # If no subtitles available, we need transcription
        # Use OpenAI Whisper API if API key is provided, otherwise use local Whisper
        if openai_api_key:
            print(f"[YouTube] Using OpenAI Whisper API for transcription")
            print(f"[YouTube] Downloading audio...")
            audio_path = download_audio(url, out_dir, video_id)
            print(f"[YouTube] Audio downloaded to: {audio_path}")
            try: 
                text = transcribe_with_openai_whisper_api(audio_path, openai_api_key, preferred_lang)
            except Exception as exc:
                print(f"[YouTube] OpenAI did not work, starting local Whisper transcription (this may take a while)...")
                text = transcribe_with_whisper(audio_path, whisper_model, preferred_lang)
        else:
            print(f"[YouTube] Checking for local Whisper installation...")
            # Check if local Whisper is available before downloading audio
            try:
                import whisper  # type: ignore
                print(f"[YouTube] Local Whisper found, using it for transcription")
            except ImportError:
                raise RuntimeError(
                    "No subtitles found for this video. "
                    "Either provide an OpenAI API key in settings, or install openai-whisper locally: "
                    "pip install openai-whisper"
                )

            print(f"[YouTube] Downloading audio...")
            audio_path = download_audio(url, out_dir, video_id)
            print(f"[YouTube] Audio downloaded to: {audio_path}")
            print(f"[YouTube] Starting local Whisper transcription (this may take a while)...")
            text = transcribe_with_whisper(audio_path, whisper_model, preferred_lang)

        if not text:
            raise RuntimeError("Transcription produced empty output.")
        print(f"[YouTube] Conversion complete")
        return build_markdown_transcript(title, url, preferred_lang, text)


async def convert_youtube(url: str, openai_api_key: str | None = None) -> str:
    return await asyncio.to_thread(fetch_youtube_markdown, url, openai_api_key=openai_api_key)


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
