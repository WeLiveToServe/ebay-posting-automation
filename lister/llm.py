"""Thin wrapper around the Anthropic SDK shared by the identify/price/describe steps."""

from __future__ import annotations

import base64
from functools import cache
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallback: if a model declines, the API reruns the request on
# a suitable model inside the same call instead of returning an empty answer.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_IMAGES = 12


class LLMError(RuntimeError):
    pass


@cache
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4)


def image_blocks(paths: list[str]) -> list[dict]:
    blocks = []
    for i, path in enumerate(paths[:MAX_IMAGES], start=1):
        data = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
        blocks.append({"type": "text", "text": f"Photo {i}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    return blocks


def _check(response) -> None:
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None) or "no detail"
        raise LLMError(f"Model declined the request ({detail})")
    if response.stop_reason == "max_tokens":
        raise LLMError("Model output was cut off (max_tokens)")


def parse(model: str, system: str, content: list[dict] | str, schema: type[T], effort: str = "medium") -> T:
    """One structured-output call; returns a validated instance of `schema`."""
    response = client().beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=schema,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    _check(response)
    if response.parsed_output is None:
        raise LLMError("Model returned no structured output")
    return response.parsed_output


def research(model: str, system: str, prompt: str, max_searches: int = 8, max_fetches: int = 0,
             effort: str = "medium") -> str:
    """Free-text answer using Claude's web search (and optionally web fetch) tools.

    Returns the final text with source URLs. Fetch lets the model read dealer search-result
    pages (e.g. AbeBooks filtered by year), which carry far more prices than search snippets.
    """
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
    if max_fetches:
        tools.append({"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_fetches})
    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(5):  # resume server-tool loops that pause
        response = client().beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    _check(response)

    text: list[str] = []
    sources: dict[str, str] = {}
    for block in response.content:
        if block.type == "text":
            text.append(block.text)
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if url:
                    sources[url] = getattr(citation, "title", "") or url
    result = "".join(text).strip()
    if sources:
        result += "\n\nSources:\n" + "\n".join(f"- {title}: {url}" for url, title in sources.items())
    return result
