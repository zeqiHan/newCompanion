"""Cloud LLM entry — same contract as JudgmentOS / docs/newCompanion-llm-cloud-guide.md.

All product code should call get_llm_settings() / chat() / stream_chat().
Do not hardcode apiKey or baseURL in business modules.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

import aiohttp

DEFAULT_BASE_URL = "https://space.ai-builders.com/backend/v1"
DEFAULT_MODEL = "grok-4-fast"


@dataclass(frozen=True)
class LlmSettings:
    api_key: str
    base_url: str
    model: str

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


class LlmConfigError(RuntimeError):
    """Missing or invalid cloud LLM configuration."""


def get_llm_settings() -> LlmSettings:
    """Read LLM_* / AI_BUILDER_TOKEN. Raises if no key (guide §3 / §6)."""
    api_key = (
        (os.environ.get("LLM_API_KEY") or "").strip()
        or (os.environ.get("AI_BUILDER_TOKEN") or "").strip()
    )
    if not api_key:
        raise LlmConfigError(
            "未配置模型密钥。本地设 LLM_API_KEY（.env 或 .env.local）；"
            "AI Builders 部署用平台注入的 AI_BUILDER_TOKEN。"
        )
    base_url = (os.environ.get("LLM_BASE_URL") or "").strip() or DEFAULT_BASE_URL
    model = (os.environ.get("LLM_MODEL") or "").strip() or DEFAULT_MODEL
    return LlmSettings(api_key=api_key, base_url=base_url, model=model)


def settings_from_cfg(cfg: dict[str, Any] | None = None) -> LlmSettings:
    """Prefer env (guide); fall back to config.yaml llm block."""
    try:
        return get_llm_settings()
    except LlmConfigError:
        pass
    cfg = cfg or {}
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        raise LlmConfigError(
            "未配置模型密钥。本地设 LLM_API_KEY（.env 或 .env.local）；"
            "AI Builders 部署用平台注入的 AI_BUILDER_TOKEN。"
        )
    return LlmSettings(
        api_key=api_key,
        base_url=str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL,
        model=str(cfg.get("model") or DEFAULT_MODEL) or DEFAULT_MODEL,
    )


def _headers(settings: LlmSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }


async def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    json_mode: bool = False,
    settings: LlmSettings | None = None,
) -> str:
    """Non-streaming chat.completions — guide §3 / §4."""
    s = settings or get_llm_settings()
    payload: dict[str, Any] = {
        "model": s.model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(s.chat_url, json=payload, headers=_headers(s)) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"LLM HTTP {resp.status}: {body[:240]}")
            data = json.loads(body)
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            if not content:
                raise RuntimeError("模型返回空响应")
            return str(content).strip()


async def stream_chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    settings: LlmSettings | None = None,
) -> AsyncIterator[str]:
    """SSE token stream; yields text deltas."""
    s = settings or get_llm_settings()
    payload: dict[str, Any] = {
        "model": s.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(s.chat_url, json=payload, headers=_headers(s)) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(f"LLM HTTP {resp.status}: {body[:240]}")
            async for raw in resp.content:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (
                    (((chunk.get("choices") or [{}])[0]).get("delta") or {}).get("content") or ""
                )
                if delta:
                    yield delta


_SPEAKABLE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def speakable_piece(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 1:
        return False
    if s in {"//", "/", "\\", "...", "…", "-", "—", "*", "#"}:
        return False
    if re.fullmatch(r"[\s/\\|\-_=·•#*]+", s):
        return False
    return bool(_SPEAKABLE.search(s))


async def stream_sentences(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    settings: LlmSettings | None = None,
) -> AsyncIterator[str]:
    """Stream and split on sentence boundaries for TTS."""
    buf = ""
    async for delta in stream_chat(
        messages, temperature=temperature, max_tokens=max_tokens, settings=settings
    ):
        buf += delta
        while True:
            m = re.search(r"[。！？!?\n]", buf)
            if not m:
                if len(buf) >= 28:
                    cut = -1
                    for sep in ("，", ",", "；", ";", " "):
                        cut = max(cut, buf.rfind(sep, 0, 28))
                    if cut < 8:
                        cut = 24
                    piece = buf[: cut + 1].strip()
                    buf = buf[cut + 1 :]
                    if speakable_piece(piece):
                        yield piece
                    continue
                break
            piece = buf[: m.end()].strip()
            buf = buf[m.end() :]
            if speakable_piece(piece):
                yield piece
    if speakable_piece(buf):
        yield buf.strip()
