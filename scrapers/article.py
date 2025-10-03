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
    """Retrieve an article and return a cleaned Markdown document.

    Parameters
    ----------
    url: str
        The URL of the article to fetch.
    include_comments: bool, optional
        Whether to keep user comments when extracting content. Defaults to ``False``.

    Returns
    -------
    str
        The article rendered as Markdown.

    Raises
    ------
    ValueError
        If the URL cannot be fetched or no meaningful content is extracted.
    """

    if not url or not isinstance(url, str):
        raise ValueError("A non-empty URL string is required")

    if html:
        markdown: Optional[str] = trafilatura.extract(
            html,
            include_comments=include_comments,
            output_format="markdown",
            include_images=False,
            favor_recall=True,
            input_format="html",
            url=url,
        )
    else:
        downloaded: Optional[str] = trafilatura.fetch_url(url)
        if not downloaded:
            raise ValueError(f"Failed to download article at {url}")

        markdown = trafilatura.extract(
            downloaded,
            include_comments=include_comments,
            output_format="markdown",
            include_images=False,
            favor_recall=True,
        )

    if not markdown:
        raise ValueError(f"No content could be extracted from {url}")

    return markdown.strip()


__all__ = ["fetch_article_markdown"]
