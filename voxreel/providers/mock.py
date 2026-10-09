"""Offline providers for tests, demos and CI. They generate placeholder media, no models involved."""
from __future__ import annotations

import hashlib
import math
import struct
import textwrap
import wave
from pathlib import Path

from .. import media
from .base import TTSProvider, VideoProvider, VideoRequest, VoiceRef, register_tts, register_video


@register_tts("mock")
class MockTTS(TTSProvider):
    """A soft tone whose length follows the text (about 2.6 words per second)."""

    def synthesize(self, text: str, voice: VoiceRef, out_path: Path) -> None:
        words = max(1, len(text.split()))
        seconds = max(0.8, words / 2.6)
        seed = int(hashlib.sha256((voice.id + (voice.sample_sha256 or "")).encode()).hexdigest()[:6], 16)
        freq = 180 + seed % 200
        rate = 24000
        frames = bytearray()
        for i in range(int(seconds * rate)):
            t = i / rate
            envelope = min(1.0, t / 0.05, (seconds - t) / 0.05)
            sample = 0.25 * envelope * math.sin(2 * math.pi * freq * t)
            frames += struct.pack("<h", int(sample * 32767))
        with wave.open(str(out_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(bytes(frames))


@register_video("mock")
class MockVideo(VideoProvider):
    """A solid coloured clip with the prompt written on it."""

    def generate(self, request: VideoRequest, out_path: Path) -> None:
        digest = hashlib.sha256(request.prompt.encode()).hexdigest()
        color = "0x" + "".join(f"{40 + int(digest[i:i + 2], 16) % 90:02x}" for i in (0, 2, 4))
        vf = "format=yuv420p"
        font = media.find_font()
        if font:
            note = out_path.with_suffix(".prompt.txt")
            note.write_text(textwrap.fill(request.prompt, 38), encoding="utf-8")
            size = max(16, request.height // 18)
            vf = (f"drawtext=fontfile='{media.filter_escape(font)}':textfile={note.name}:fontcolor=white:"
                  f"fontsize={size}:x=(w-tw)/2:y=(h-th)/2,format=yuv420p")
        media.ffmpeg(["-f", "lavfi", "-i",
                      f"color=c={color}:s={request.width}x{request.height}:r={request.fps}:d={request.duration:.3f}",
                      "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(out_path.name)],
                     cwd=out_path.parent)
