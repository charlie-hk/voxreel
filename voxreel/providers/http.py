"""Call any JSON/HTTP API as a provider, with optional job polling. Standard library only.

Config (TTS example):
  {"url": "https://api.example.com/v1/speech",
   "headers": {"Authorization": "Bearer ${ENV:EXAMPLE_API_KEY}"},
   "body": {"text": "{text}", "voice": "{voice}"},
   "response": "binary"}

`response` is one of:
  "binary"    the HTTP body is the media file
  "json_b64"  `result_path` points at a base64 string in the JSON reply
  "json_url"  `result_path` points at a URL to download

For asynchronous video APIs add a `poll` block:
  {"job_path": "id", "url": "https://api.example.com/v1/jobs/{job_id}",
   "status_path": "status", "done": ["succeeded"], "failed": ["failed"],
   "result_path": "output.0", "result": "json_url", "interval": 3, "timeout": 900}

Secrets are only ever read from environment variables via ${ENV:NAME}; they are never written to
the project file, the cache key or the provenance record.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..errors import ProviderError
from .base import TTSProvider, VideoProvider, VideoRequest, VoiceRef, register_tts, register_video

_ENV = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")
_KNOWN = re.compile(r"\{(text|voice|sample_b64|prompt|duration|width|height|fps|job_id)\}")
MAX_BYTES = 500 * 1024 * 1024


def _subst(value, ctx: dict):
    if isinstance(value, str):
        def env(m):
            name = m.group(1)
            if name not in os.environ:
                raise ProviderError(f"environment variable {name} is not set")
            return os.environ[name]
        value = _ENV.sub(env, value)
        return _KNOWN.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), value)
    if isinstance(value, dict):
        return {k: _subst(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, ctx) for v in value]
    return value


def _dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise ProviderError(f"response has no element {path!r}") from None
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise ProviderError(f"response has no field {path!r}")
    return cur


def _check_url(url: str) -> None:
    parts = urllib.parse.urlparse(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ProviderError("provider URLs must use https (plain http is allowed for localhost only)")


def _request(method: str, url: str, headers: dict, body, timeout: int, retries: int = 3):
    _check_url(url)
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ProviderError("response is larger than 500 MB")
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            detail = exc.read(300).decode("utf-8", "replace")
            raise ProviderError(f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            raise ProviderError(f"network error: {exc}") from exc
    raise ProviderError("request failed")  # pragma: no cover


def _extract(mode: str, raw: bytes, path: str | None, out_path: Path, timeout: int) -> None:
    if mode == "binary":
        out_path.write_bytes(raw)
        return
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("expected a JSON reply") from exc
    if not path:
        raise ProviderError(f"response mode {mode!r} needs result_path")
    value = _dig(doc, path)
    if not isinstance(value, str):
        raise ProviderError(f"{path!r} is not a string")
    if mode == "json_b64":
        try:
            out_path.write_bytes(base64.b64decode(value, validate=True))
        except ValueError as exc:
            raise ProviderError("result is not valid base64") from exc
    elif mode == "json_url":
        out_path.write_bytes(_request("GET", value, {}, None, timeout))
    else:
        raise ProviderError(f"unknown response mode {mode!r}")


def _call(config: dict, ctx: dict, out_path: Path, label: str) -> None:
    if "url" not in config:
        raise ProviderError(f"{label}: config.url is required")
    timeout = int(config.get("timeout", 120))
    headers = _subst(config.get("headers", {}), ctx)
    body = _subst(config.get("body"), ctx)
    method = config.get("method", "POST").upper()
    raw = _request(method, _subst(config["url"], ctx), headers, body, timeout)
    mode = config.get("response", "binary")
    poll = config.get("poll")
    if poll:
        job_id = str(_dig(json.loads(raw), poll["job_path"]))
        ctx = {**ctx, "job_id": job_id}
        deadline = time.monotonic() + int(poll.get("timeout", 900))
        interval = float(poll.get("interval", 3))
        while True:
            state = json.loads(_request("GET", _subst(poll["url"], ctx), headers, None, timeout))
            status = str(_dig(state, poll["status_path"]))
            if status in poll.get("failed", ["failed"]):
                raise ProviderError(f"{label}: job {job_id} ended with status {status!r}")
            if status in poll.get("done", ["succeeded"]):
                raw = json.dumps(state).encode()
                mode = poll.get("result", "json_url")
                path = poll.get("result_path")
                break
            if time.monotonic() > deadline:
                raise ProviderError(f"{label}: job {job_id} did not finish in time")
            time.sleep(interval)
    else:
        path = config.get("result_path")
    _extract(mode, raw, path, out_path, timeout)
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ProviderError(f"{label}: empty result")


@register_tts("http")
class HttpTTS(TTSProvider):
    def synthesize(self, text: str, voice: VoiceRef, out_path: Path) -> None:
        ctx = {"text": text, "voice": voice.provider_voice or voice.id}
        if voice.sample_path and "sample_b64" in json.dumps(self.config):
            ctx["sample_b64"] = base64.b64encode(voice.sample_path.read_bytes()).decode()
        _call(self.config, ctx, out_path, "tts http")


@register_video("http")
class HttpVideo(VideoProvider):
    def generate(self, request: VideoRequest, out_path: Path) -> None:
        ctx = {"prompt": request.prompt, "duration": f"{request.duration:.2f}", "width": request.width,
               "height": request.height, "fps": request.fps}
        _call(self.config, ctx, out_path, "video http")
