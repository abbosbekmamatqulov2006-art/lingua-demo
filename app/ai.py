"""
Provider layer.

Two vendors sit behind one interface. Gemini is primary because it carries the
project's trial credit; Claude takes over whenever Gemini fails, is rate
limited, or returns something unparseable. Callers never learn which one
answered — they ask for JSON and get JSON.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("lingua.ai")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_DEEP_MODEL = os.getenv("GEMINI_DEEP_MODEL", "gemini-3.1-pro-preview")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

# Model names change under us: a name can be listed by the models endpoint yet
# rejected on call ("no longer available to new users"). When the primary name
# 404s we walk this list rather than falling straight through to the other
# vendor, since a stale name is not a reason to abandon the provider.
GEMINI_FALLBACKS = [
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

TIMEOUT = httpx.Timeout(120.0, connect=15.0)

# Every exercise request fans out into several parallel calls. Without a ceiling,
# a handful of simultaneous users becomes dozens of concurrent requests and the
# provider starts returning 429 to everyone at once. This caps in-flight calls
# across the whole process, so bursts queue instead of failing.
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "12"))
_gate = asyncio.Semaphore(MAX_IN_FLIGHT)


class ProviderError(RuntimeError):
    """Raised when every provider has been tried and none produced an answer."""


@dataclass
class Reply:
    text: str
    provider: str
    model: str
    elapsed: float


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def claude_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def available() -> list[str]:
    names = []
    if gemini_key():
        names.append("gemini")
    if claude_key():
        names.append("claude")
    return names


# --------------------------------------------------------------- JSON repair

_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def _salvage_array(text: str):
    """Recover the complete objects from a truncated JSON array.

    A reply that runs out of output tokens ends mid-object. The whole response
    is then unparseable even though most of it is perfectly good — so rather
    than fail the request, we walk the text and keep every object that closed
    before the cut. The caller tops the set back up.
    """
    start = text.find("[")
    if start == -1:
        return None

    objects = []
    depth = 0
    in_string = False
    escaped = False
    begin = None

    for index in range(start + 1, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                begin = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and begin is not None:
                try:
                    objects.append(json.loads(text[begin:index + 1]))
                except json.JSONDecodeError:
                    pass
                begin = None

    return objects or None


def parse_json(text: str):
    """Pull a JSON value out of a model reply.

    Models wrap payloads in fences, prefix them with a sentence, or trail a
    closing remark. Rather than failing the whole request we strip the wrapper,
    then fall back to the outermost bracketed span, then to a trailing-comma
    fix, and finally to salvaging whatever objects completed before a cut-off.
    """
    cleaned = _FENCE.sub("", text).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
    if match:
        span = match.group(0)
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            repaired = re.sub(r",(\s*[}\]])", r"\1", span)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    salvaged = _salvage_array(cleaned)
    if salvaged:
        log.warning("Recovered %d objects from a truncated reply", len(salvaged))
        return salvaged

    raise ValueError(f"No JSON found in reply: {cleaned[:300]}")


# --------------------------------------------------------------- Gemini


def _gemini_parts(prompt: str, image: bytes | None, mime: str) -> list[dict]:
    parts: list[dict] = []
    if image:
        parts.append({"inline_data": {"mime_type": mime,
                                      "data": base64.standard_b64encode(image).decode()}})
    parts.append({"text": prompt})
    return parts


async def _call_gemini(client: httpx.AsyncClient, prompt: str, *, system: str | None,
                       image: bytes | None, mime: str, model: str,
                       temperature: float, max_tokens: int, want_json: bool) -> str:
    body: dict = {
        "contents": [{"role": "user", "parts": _gemini_parts(prompt, image, mime)}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            # Asking the vendor to constrain output to JSON removes an entire
            # class of parse failure, so we do it whenever the caller wants JSON.
            **({"responseMimeType": "application/json"} if want_json else {}),
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    response = await client.post(
        GEMINI_URL.format(model=model),
        params={"key": gemini_key()},
        json=body,
    )
    response.raise_for_status()
    payload = response.json()

    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = payload.get("promptFeedback", {}).get("blockReason")
        raise ProviderError(f"Gemini returned nothing{f' ({blocked})' if blocked else ''}")

    parts = candidates[0].get("content", {}).get("parts") or []
    # Gemini 3 interleaves reasoning parts with the answer. Those carry
    # "thought": true and must not end up in the payload we try to parse.
    text = "".join(part.get("text", "") for part in parts
                   if not part.get("thought"))
    if not text.strip():
        finish = candidates[0].get("finishReason", "")
        if finish == "MAX_TOKENS":
            raise ProviderError("Gemini ran out of output tokens before answering")
        raise ProviderError(f"Gemini returned an empty reply ({finish or 'no reason'})")
    return text


# --------------------------------------------------------------- Claude


async def _call_claude(client: httpx.AsyncClient, prompt: str, *, system: str | None,
                       image: bytes | None, mime: str, model: str,
                       temperature: float, max_tokens: int, want_json: bool) -> str:
    content: list[dict] = []
    if image:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime,
                       "data": base64.standard_b64encode(image).decode()},
        })
    content.append({"type": "text", "text": prompt})

    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": content}],
    }
    if system:
        body["system"] = system

    response = await client.post(
        CLAUDE_URL,
        headers={"x-api-key": claude_key(),
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body,
    )
    response.raise_for_status()
    payload = response.json()

    text = "".join(block.get("text", "") for block in payload.get("content", [])
                   if block.get("type") == "text")
    if not text.strip():
        raise ProviderError("Claude returned an empty reply")
    return text


# --------------------------------------------------------------- dispatch

_CALLERS = {"gemini": _call_gemini, "claude": _call_claude}


def _model_chain(provider: str, deep: bool) -> list[str]:
    """Model names to try for one provider, best first."""
    if provider != "gemini":
        return [CLAUDE_MODEL]
    first = GEMINI_DEEP_MODEL if deep else GEMINI_MODEL
    return [first] + [name for name in GEMINI_FALLBACKS if name != first]


async def ask(prompt: str, *, system: str | None = None, image: bytes | None = None,
              mime: str = "image/png", temperature: float = 0.4,
              max_tokens: int = 8000, want_json: bool = True,
              deep: bool = False, attempts: int = 2) -> Reply:
    """Send one prompt, falling through models and then providers until one answers.

    Each model gets `attempts` tries with a short backoff, because rate limits
    and transient 5xx are common enough that giving up after one failure would
    send far more traffic to the fallback than necessary. A 404 means the name
    is dead, so we move to the next name immediately without retrying.
    """
    providers = available()
    if not providers:
        raise ProviderError(
            "No API keys configured. Set GEMINI_API_KEY or ANTHROPIC_API_KEY."
        )

    failures: list[str] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for provider in providers:
            for model in _model_chain(provider, deep):
                for attempt in range(attempts):
                    started = time.perf_counter()
                    try:
                        async with _gate:
                            text = await _CALLERS[provider](
                                client, prompt, system=system, image=image, mime=mime,
                                model=model, temperature=temperature,
                                max_tokens=max_tokens, want_json=want_json,
                            )
                        return Reply(text=text, provider=provider, model=model,
                                     elapsed=time.perf_counter() - started)

                    except httpx.HTTPStatusError as error:
                        status = error.response.status_code
                        failures.append(f"{model} {status}")
                        log.warning("%s (%s) failed with %s: %s", provider, model,
                                    status, error.response.text[:200])
                        # 4xx other than rate limiting will fail identically on
                        # retry, so stop retrying this model and move on.
                        if status not in (408, 429, 500, 502, 503, 504):
                            break
                        await _sleep(attempt, long=status == 429)

                    except (httpx.RequestError, ProviderError) as error:
                        failures.append(f"{model}: {error}")
                        log.warning("%s (%s) failed: %s", provider, model, error)
                        await _sleep(attempt)

    raise ProviderError("All providers failed — " + "; ".join(failures[-4:]))


async def _sleep(attempt: int, long: bool = False) -> None:
    """Back off before retrying. Rate limits need a real pause, not a blink."""
    base = 4.0 if long else 0.8
    await asyncio.sleep(base * (attempt + 1))


async def ask_json(prompt: str, **kwargs):
    """Ask for JSON and return the parsed value, retrying once on a parse failure."""
    reply = await ask(prompt, want_json=True, **kwargs)
    try:
        return parse_json(reply.text), reply
    except ValueError:
        log.warning("Unparseable JSON from %s, retrying at temperature 0", reply.provider)
        kwargs["temperature"] = 0.0
        retry = await ask(prompt, want_json=True, **kwargs)
        return parse_json(retry.text), retry


async def health() -> dict:
    """Ping every configured provider. Powers the status endpoint."""
    report = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for provider in available():
            started = time.perf_counter()
            last = ""
            for model in _model_chain(provider, deep=False):
                try:
                    await _CALLERS[provider](
                        client, "Reply with the single word: ok", system=None,
                        image=None, mime="image/png", model=model,
                        temperature=0.0, max_tokens=16, want_json=False,
                    )
                    report[provider] = {
                        "ok": True, "model": model,
                        "ms": round((time.perf_counter() - started) * 1000)}
                    break
                except Exception as error:  # noqa: BLE001 — the report is the product
                    last = f"{model}: {str(error)[:160]}"
            else:
                report[provider] = {"ok": False, "error": last}
    return report
