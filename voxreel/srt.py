"""Subtitle cues from scene text and scene durations."""
from __future__ import annotations

import re

MAX_CHARS = 84


def chunks(text: str, limit: int = MAX_CHARS) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    for sentence in sentences:
        cur = ""
        for word in sentence.split():
            if cur and len(cur) + 1 + len(word) > limit:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur:
            out.append(cur)
    return out


def stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def build(scenes: list[tuple[str, float]]) -> str:
    """scenes: [(text, speech_seconds)] in order. Returns SRT text. Time is shared by character count."""
    lines, index, offset = [], 1, 0.0
    for text, seconds in scenes:
        parts = chunks(text)
        total = sum(len(p) for p in parts) or 1
        t = offset
        for part in parts:
            span = seconds * len(part) / total
            lines += [str(index), f"{stamp(t)} --> {stamp(t + span)}", part, ""]
            index += 1
            t += span
        offset += seconds
    return "\n".join(lines)
