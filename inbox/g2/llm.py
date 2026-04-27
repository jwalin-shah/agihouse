"""Thin OpenRouter wrapper used by the transcript agent and arbitrator."""

from __future__ import annotations

import re

import httpx
from loguru import logger

from .config import settings

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def strip_code_fence(text: str) -> str:
    """Remove leading/trailing ``` or ```json fences from an LLM response."""
    return _FENCE_RE.sub("", text).strip()


async def call_llm(
    prompt: str,
    system: str,
    model: str | None = None,
    max_tokens: int = 200,
    temperature: float = 0.2,
) -> str | None:
    if not settings.openrouter_key:
        logger.warning("[llm] OPENROUTER_API_KEY not set")
        return None

    chosen_model = model or settings.model_fast
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                settings.openrouter_url,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_key}",
                    "HTTP-Referer": "https://github.com/even-realities/everything-evenhub",
                    "X-Title": "G2 Ambient Agent",
                },
                json={
                    "model": chosen_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            choices = payload.get("choices") or []
            if not choices:
                logger.warning(f"[llm] {chosen_model} empty choices: {payload}")
                return None
            content = (choices[0].get("message") or {}).get("content")
            if not content or not content.strip():
                # Some reasoning models stash the answer in reasoning_content.
                content = (choices[0].get("message") or {}).get("reasoning_content") or ""
            return content.strip() or None
    except Exception as e:
        logger.warning(f"[llm] {chosen_model} failed: {e}")
        return None
