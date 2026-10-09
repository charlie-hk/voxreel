"""Provider interfaces and registry. Providers are small classes; write your own and register them."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import ProviderError


@dataclass(frozen=True)
class VoiceRef:
    id: str
    kind: str                      # "stock" or "cloned"
    provider_voice: str | None = None
    sample_path: Path | None = None
    sample_sha256: str | None = None


@dataclass(frozen=True)
class VideoRequest:
    prompt: str
    duration: float
    width: int
    height: int
    fps: int


class TTSProvider:
    name = "base"

    def __init__(self, config: dict):
        self.config = config or {}

    def synthesize(self, text: str, voice: VoiceRef, out_path: Path) -> None:
        """Write speech for `text` to `out_path` (any format ffmpeg can read)."""
        raise NotImplementedError


class VideoProvider:
    name = "base"

    def __init__(self, config: dict):
        self.config = config or {}

    def generate(self, request: VideoRequest, out_path: Path) -> None:
        """Write a silent clip for `request` to `out_path` (any format ffmpeg can read)."""
        raise NotImplementedError


_TTS: dict[str, type[TTSProvider]] = {}
_VIDEO: dict[str, type[VideoProvider]] = {}


def register_tts(name: str):
    def deco(cls):
        cls.name = name
        _TTS[name] = cls
        return cls
    return deco


def register_video(name: str):
    def deco(cls):
        cls.name = name
        _VIDEO[name] = cls
        return cls
    return deco


def get_tts(name: str, config: dict) -> TTSProvider:
    if name not in _TTS:
        raise ProviderError(f"unknown tts provider {name!r}; available: {', '.join(sorted(_TTS))}")
    return _TTS[name](config)


def get_video(name: str, config: dict) -> VideoProvider:
    if name not in _VIDEO:
        raise ProviderError(f"unknown video provider {name!r}; available: {', '.join(sorted(_VIDEO))}")
    return _VIDEO[name](config)


def available() -> dict:
    return {"tts": sorted(_TTS), "video": sorted(_VIDEO)}
