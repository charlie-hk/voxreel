"""The project file: a small JSON document that describes scenes and providers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import SpecError

SCENE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RES = re.compile(r"^(\d{2,4})x(\d{2,4})$")
MAX_TEXT = 2000


@dataclass
class Scene:
    id: str
    text: str
    visual: str
    voice: str


@dataclass
class Spec:
    path: Path
    title: str
    output: Path
    width: int
    height: int
    fps: int
    purpose: str
    tts_provider: str
    tts_config: dict
    video_provider: str
    video_config: dict
    subtitles: str
    label: bool
    label_text: str
    loudnorm: bool
    tail: float
    scenes: list[Scene] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def workdir(self) -> Path:
        return self.dir / ".voxreel"


def load_spec(path: Path) -> Spec:
    path = Path(path)
    if not path.is_file():
        raise SpecError(f"project file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SpecError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError("project file must be a JSON object")

    m = RES.match(str(raw.get("resolution", "1280x720")))
    if not m:
        raise SpecError("resolution must look like 1280x720")
    width, height = int(m.group(1)), int(m.group(2))
    if width % 2 or height % 2 or not (64 <= width <= 3840 and 64 <= height <= 2160):
        raise SpecError("resolution must be even numbers between 64x64 and 3840x2160")
    fps = raw.get("fps", 24)
    if not isinstance(fps, int) or not 1 <= fps <= 60:
        raise SpecError("fps must be an integer from 1 to 60")

    output = (path.parent / raw.get("output", "out/video.mp4")).resolve()
    try:
        output.relative_to(path.parent.resolve())
    except ValueError:
        raise SpecError("output must stay inside the project folder") from None
    if output.suffix.lower() != ".mp4":
        raise SpecError("output must end with .mp4")

    tts = raw.get("tts") or {}
    video = raw.get("video") or {}
    subtitles = raw.get("subtitles", "burn")
    if subtitles not in ("burn", "sidecar", "none"):
        raise SpecError("subtitles must be burn, sidecar or none")
    disclosure = raw.get("disclosure") or {}
    tail = raw.get("scene_tail", 0.4)
    if not isinstance(tail, (int, float)) or not 0 <= tail <= 5:
        raise SpecError("scene_tail must be between 0 and 5 seconds")
    purpose = str(raw.get("purpose", "narration")).strip().lower()
    if not purpose:
        raise SpecError("purpose must not be empty")

    default_voice = raw.get("voice")
    scenes_raw = raw.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise SpecError("scenes must be a non-empty list")
    if len(scenes_raw) > 200:
        raise SpecError("at most 200 scenes per project")
    scenes, seen = [], set()
    for i, s in enumerate(scenes_raw, 1):
        if not isinstance(s, dict):
            raise SpecError(f"scene {i} must be an object")
        sid = str(s.get("id", ""))
        if not SCENE_ID.match(sid):
            raise SpecError(f"scene {i}: id must be lowercase letters, digits, - or _")
        if sid in seen:
            raise SpecError(f"duplicate scene id {sid!r}")
        seen.add(sid)
        text = str(s.get("text", "")).strip()
        if not text or len(text) > MAX_TEXT:
            raise SpecError(f"scene {sid!r}: text is required (max {MAX_TEXT} characters)")
        voice = s.get("voice", default_voice)
        if not voice:
            raise SpecError(f"scene {sid!r}: no voice (set `voice` on the project or the scene)")
        scenes.append(Scene(sid, text, str(s.get("visual") or text).strip(), str(voice)))

    return Spec(
        path=path, title=str(raw.get("title", "Untitled")), output=output, width=width, height=height,
        fps=fps, purpose=purpose, tts_provider=tts.get("provider", "mock"), tts_config=tts.get("config", {}),
        video_provider=video.get("provider", "mock"), video_config=video.get("config", {}),
        subtitles=subtitles, label=bool(disclosure.get("label", True)),
        label_text=str(disclosure.get("text", "AI-generated voice and video"))[:80],
        loudnorm=bool(raw.get("loudnorm", True)), tail=float(tail), scenes=scenes)
