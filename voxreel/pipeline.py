"""The pipeline: per scene text -> speech -> video clip -> muxed scene, then one final render.

Every step is cached by a hash of everything that influences it, so a re-run only redoes what changed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, media, srt
from .consent import Registry, sha256_file
from .errors import VoxError
from .project import Spec, load_spec
from .providers import VideoRequest, get_tts, get_video


def _key(**parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class State:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            self.data = {}

    def fresh(self, name: str, key: str, *outputs: Path) -> bool:
        return self.data.get(name) == key and all(o.is_file() and o.stat().st_size > 0 for o in outputs)

    def set(self, name: str, key: str) -> None:
        self.data[name] = key
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def run(spec_path: Path, force: bool = False, only: str | None = None, log=print) -> dict:
    spec = load_spec(spec_path)
    if only and only not in {s.id for s in spec.scenes}:
        raise VoxError(f"no scene named {only!r}")
    registry = Registry(spec.dir)

    # Consent is checked for every scene before any provider is called or any work is done.
    resolved = {}
    for scene in spec.scenes:
        resolved[scene.id] = registry.resolve(scene.voice, spec.purpose)

    tts = get_tts(spec.tts_provider, spec.tts_config)
    video = get_video(spec.video_provider, spec.video_config)
    media.need("ffmpeg")
    media.need("ffprobe")
    spec.workdir.mkdir(exist_ok=True)
    state = State(spec.workdir / "state.json")
    stats = {"done": 0, "cached": 0}
    scene_files, durations, provenance_scenes = [], [], []

    for scene in spec.scenes:
        voice, record = resolved[scene.id]
        sdir = spec.workdir / scene.id
        sdir.mkdir(exist_ok=True)
        audio, clip, muxed = sdir / "speech.wav", sdir / "clip.mp4", sdir / "scene.mp4"
        skip = bool(only and only != scene.id)
        redo = force and not skip

        tts_key = _key(step="tts", text=scene.text, voice=voice.id, pv=voice.provider_voice,
                       sample=voice.sample_sha256, provider=spec.tts_provider, cfg=spec.tts_config)
        if state.fresh(f"{scene.id}:tts", tts_key, audio) and not redo:
            stats["cached"] += 1
        elif skip:
            raise VoxError(f"scene {scene.id!r} has no cached speech; run without --only first")
        else:
            log(f"[{scene.id}] speech ({spec.tts_provider}, voice {voice.id})")
            tts.synthesize(scene.text, voice, audio)
            state.set(f"{scene.id}:tts", tts_key)
            stats["done"] += 1
        speech_seconds = media.probe_duration(audio)
        total = round(speech_seconds + spec.tail, 3)

        vid_key = _key(step="video", prompt=scene.visual, dur=total, size=(spec.width, spec.height),
                       fps=spec.fps, provider=spec.video_provider, cfg=spec.video_config)
        if state.fresh(f"{scene.id}:video", vid_key, clip) and not redo:
            stats["cached"] += 1
        elif skip:
            raise VoxError(f"scene {scene.id!r} has no cached video; run without --only first")
        else:
            log(f"[{scene.id}] video ({spec.video_provider})")
            video.generate(VideoRequest(scene.visual, total, spec.width, spec.height, spec.fps), clip)
            state.set(f"{scene.id}:video", vid_key)
            stats["done"] += 1

        mux_key = _key(step="mux", tts=tts_key, video=vid_key, tail=spec.tail)
        if state.fresh(f"{scene.id}:mux", mux_key, muxed) and not redo:
            stats["cached"] += 1
        else:
            log(f"[{scene.id}] mux")
            media.make_scene(clip, audio, muxed, total, spec.width, spec.height, spec.fps)
            state.set(f"{scene.id}:mux", mux_key)
            stats["done"] += 1

        scene_files.append(muxed)
        durations.append(total)
        provenance_scenes.append({
            "id": scene.id, "text_sha256": hashlib.sha256(scene.text.encode()).hexdigest(),
            "voice": voice.id, "voice_kind": voice.kind,
            "consent_id": record["id"] if record else None,
            "tts_provider": spec.tts_provider, "video_provider": spec.video_provider,
            "audio_sha256": sha256_file(audio), "clip_sha256": sha256_file(clip)})

    if only:
        log("scene refreshed; rendering the full video from cache")

    log("final render")
    spec.output.parent.mkdir(parents=True, exist_ok=True)
    joined = spec.workdir / "joined.mp4"
    media.concat(scene_files, joined, spec.workdir / "scenes.txt")

    srt_text = srt.build([(s.text, d) for s, d in zip(spec.scenes, durations)])
    srt_path = spec.output.with_suffix(".srt")
    work_srt = spec.workdir / "subs.srt"
    work_srt.write_text(srt_text, encoding="utf-8")
    if spec.subtitles == "sidecar":
        shutil.copyfile(work_srt, srt_path)
    label_name = None
    if spec.label:
        (spec.workdir / "label.txt").write_text(spec.label_text, encoding="utf-8")
        label_name = "label.txt"
    media.finalize(
        joined, spec.output, spec.workdir,
        srt_name="subs.srt" if spec.subtitles == "burn" else None,
        label_name=label_name, font=media.find_font(), height=spec.height, loudnorm=spec.loudnorm,
        metadata={"title": spec.title, "comment": f"{spec.label_text} | made with voxreel {__version__}",
                  "description": "AI-generated content. See the .provenance.json file next to this video."})

    provenance = {
        "title": spec.title, "ai_generated": True, "purpose": spec.purpose,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": f"voxreel {__version__}", "duration_seconds": round(media.probe_duration(spec.output), 2),
        "output_sha256": sha256_file(spec.output), "disclosure_label": spec.label_text if spec.label else None,
        "scenes": provenance_scenes}
    prov_path = spec.output.with_suffix(".provenance.json")
    prov_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return {"output": spec.output, "provenance": prov_path, "scenes": len(spec.scenes), **stats}
