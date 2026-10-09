"""Run any local program as a provider (Coqui XTTS, Piper, ComfyUI scripts, ...).

Config:
  {"cmd": ["python", "tts.py", "--text-file", "{text_file}", "--voice", "{voice}", "--out", "{out}"],
   "timeout": 900}

The command is an argument list and is never passed through a shell. Placeholders:
  TTS:   {text} {text_file} {voice} {sample} {out}
  video: {prompt} {duration} {width} {height} {fps} {out}
  both:  {python} (the interpreter running voxreel)

voxreel's own helper tools (python -m voxreel.tools.xtts) work from any folder because the package
location is added to PYTHONPATH for the command.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from ..errors import ProviderError
from .base import TTSProvider, VideoProvider, VideoRequest, VoiceRef, register_tts, register_video

_KNOWN = re.compile(r"\{(text|text_file|voice|sample|out|prompt|duration|width|height|fps|python)\}")


def _render(arg: str, ctx: dict) -> str:
    return _KNOWN.sub(lambda m: str(ctx[m.group(1)]), arg)


def _run(config: dict, ctx: dict, out_path: Path, label: str) -> None:
    cmd = config.get("cmd")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
        raise ProviderError(f"{label}: config.cmd must be a non-empty list of strings")
    ctx = {**ctx, "python": sys.executable}
    argv = [_render(a, ctx) for a in cmd]
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    timeout = int(config.get("timeout", 900))
    if out_path.exists():
        out_path.unlink()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=out_path.parent, env=env)
    except FileNotFoundError as exc:
        raise ProviderError(f"{label}: cannot run {argv[0]!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"{label}: command timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise ProviderError(f"{label}: command failed ({proc.returncode}): {(proc.stderr or '').strip()[-400:]}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ProviderError(f"{label}: command finished but wrote no output to {out_path.name}")


@register_tts("command")
class CommandTTS(TTSProvider):
    def synthesize(self, text: str, voice: VoiceRef, out_path: Path) -> None:
        text_file = out_path.with_suffix(".txt")
        text_file.write_text(text, encoding="utf-8")
        ctx = {"text": text, "text_file": text_file, "out": out_path,
               "voice": voice.provider_voice or voice.id, "sample": voice.sample_path or ""}
        _run(self.config, ctx, out_path, "tts command")


@register_video("command")
class CommandVideo(VideoProvider):
    def generate(self, request: VideoRequest, out_path: Path) -> None:
        ctx = {"prompt": request.prompt, "duration": f"{request.duration:.2f}", "width": request.width,
               "height": request.height, "fps": request.fps, "out": out_path}
        _run(self.config, ctx, out_path, "video command")
