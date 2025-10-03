"""Utilities for fetching article content and converting it to Markdown."""

from __future__ import annotations

from typing import Optional

import trafilatura


def fetch_article_markdown(
    url: str,
    *,
    html: str | None = None,
    include_comments: bool = False,
) -> str:

    if not url or not isinstance(url, str):
        raise ValueError("A non-empty URL string is required")

    downloaded: Optional[str]
    if html:
        downloaded = html
    else:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise ValueError(f"Failed to download article at {url}")

    metadata = trafilatura.extract_metadata(downloaded, default_url=url)

    markdown = trafilatura.extract(
        downloaded,
        include_comments=include_comments,
        output_format="markdown",
        include_images=False,
        favor_recall=True,
    )

    if not markdown:
        raise ValueError(f"No content could be extracted from {url}")

    cleaned = markdown.strip()
    title = (metadata.title.strip() if metadata and metadata.title else None)
    if title:
        return f"# {title}\n\n{cleaned}" if cleaned else f"# {title}"
    return cleaned


__all__ = ["fetch_article_markdown"]