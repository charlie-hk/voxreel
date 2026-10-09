"""Thin, explicit wrappers around ffmpeg/ffprobe. Every call is an argument list, never a shell string."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import MediaError

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise MediaError(f"{tool} not found on PATH; install ffmpeg (https://ffmpeg.org)")
    return path


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError(f"cannot run {cmd[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{cmd[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-600:]
        raise MediaError(f"{Path(cmd[0]).name} failed ({proc.returncode}): {tail}")
    return proc.stdout


def ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    run([need("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args], cwd=cwd)


def probe_duration(path: Path) -> float:
    out = run([need("ffprobe"), "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)], timeout=60).strip()
    try:
        value = float(out)
    except ValueError as exc:
        raise MediaError(f"cannot read duration of {path.name}") from exc
    if value <= 0:
        raise MediaError(f"{path.name} has no playable duration")
    return value


def has_stream(path: Path, kind: str) -> bool:
    out = run([need("ffprobe"), "-v", "error", "-select_streams", kind[0], "-show_entries",
               "stream=codec_type", "-of", "default=nw=1:nk=1", str(path)], timeout=60)
    return bool(out.strip())


def find_font() -> str | None:
    env = os.environ.get("VOXREEL_FONT")
    if env and Path(env).is_file():
        return env
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    if shutil.which("fc-match"):
        try:
            out = run(["fc-match", "-f", "%{file}", "sans"], timeout=10).strip()
            if out and Path(out).is_file():
                return out
        except MediaError:
            pass
    return None


def filter_escape(value: str) -> str:
    """Escape a value placed inside an ffmpeg filter option."""
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def make_scene(video: Path, audio: Path, out: Path, duration: float, width: int, height: int, fps: int) -> None:
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,setsar=1")
    ffmpeg(["-stream_loop", "-1", "-i", str(video), "-i", str(audio), "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-af", "apad,aresample=48000",
            "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])


def concat(parts: list[Path], out: Path, list_file: Path) -> None:
    lines = []
    for p in parts:
        escaped = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)])


def finalize(src: Path, out: Path, workdir: Path, *, srt_name: str | None, label_name: str | None,
             font: str | None, height: int, loudnorm: bool, metadata: dict[str, str]) -> None:
    vfilters: list[str] = []
    if srt_name:
        vfilters.append(f"subtitles={srt_name}:force_style='FontSize=10,Outline=1,Shadow=0,MarginV=18'")
    if label_name:
        if not font:
            raise MediaError("no font found for the AI-disclosure label; set VOXREEL_FONT to a .ttf file")
        size = max(14, height // 34)
        vfilters.append(f"drawtext=fontfile='{filter_escape(font)}':textfile={label_name}:fontcolor=white:"
                        f"fontsize={size}:box=1:boxcolor=black@0.5:boxborderw=8:x=w-tw-24:y=24")
    args = ["-i", str(src.resolve())]
    if vfilters:
        args += ["-vf", ",".join(vfilters), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]
    if loudnorm:
        args += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c:a", "copy"]
    for key, value in metadata.items():
        args += ["-metadata", f"{key}={value}"]
    args += ["-movflags", "+faststart", str(out.resolve())]
    ffmpeg(args, cwd=workdir)
