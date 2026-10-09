from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, media, pipeline
from .consent import Registry
from .errors import VoxError
from .project import load_spec
from .providers import available

EXAMPLE = {
    "title": "voxreel demo",
    "output": "out/demo.mp4",
    "resolution": "1280x720",
    "fps": 24,
    "purpose": "narration",
    "voice": "narrator",
    "tts": {"provider": "mock", "config": {}},
    "video": {"provider": "mock", "config": {}},
    "subtitles": "burn",
    "disclosure": {"label": True, "text": "AI-generated voice and video"},
    "scenes": [
        {"id": "intro", "text": "Welcome. This short film was assembled by a script, scene by scene.",
         "visual": "A calm title card with soft colours"},
        {"id": "middle", "text": "Each step is cached, so changing one scene only renders that scene again.",
         "visual": "Gears turning slowly over a blue background"},
        {"id": "outro", "text": "Swap the mock providers for real ones in the project file. That is all.",
         "visual": "A sunrise over a quiet city"},
    ],
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voxreel", description="Consent-first AI voice and video pipeline.")
    p.add_argument("--version", action="version", version=f"voxreel {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create an example project")
    s.add_argument("folder")

    s = sub.add_parser("run", help="render the video")
    s.add_argument("project", help="path to project.json")
    s.add_argument("--force", action="store_true", help="ignore the cache")
    s.add_argument("--only", metavar="SCENE", help="refresh one scene, then re-render the whole video")

    s = sub.add_parser("status", help="show scenes and voices")
    s.add_argument("project")

    sub.add_parser("doctor", help="check ffmpeg, fonts and providers")

    v = sub.add_parser("voice", help="manage voices and consent")
    vsub = v.add_subparsers(dest="vcmd", required=True)
    a = vsub.add_parser("add-stock", help="register a provider's built-in voice (no consent needed)")
    a.add_argument("folder"); a.add_argument("id"); a.add_argument("--provider-voice")
    c = vsub.add_parser("clone", help="register a cloned voice together with its consent record")
    c.add_argument("folder"); c.add_argument("id")
    c.add_argument("--sample", required=True, help="audio sample of the speaker")
    c.add_argument("--speaker", required=True, help="the speaker's name")
    c.add_argument("--statement", required=True, help="what the speaker agreed to (must name them)")
    c.add_argument("--scope", required=True, help="comma-separated uses, e.g. narration,education")
    c.add_argument("--expires", help="YYYY-MM-DD")
    c.add_argument("--recording", help="optional recording of the speaker giving consent")
    c.add_argument("--provider-voice")
    c.add_argument("--confirm", action="store_true", help="I have the speaker's explicit permission")
    l = vsub.add_parser("list"); l.add_argument("folder")
    r = vsub.add_parser("revoke", help="withdraw consent; the voice stops working immediately")
    r.add_argument("folder"); r.add_argument("id"); r.add_argument("--reason", default="")
    return p


def _project_file(arg: str) -> Path:
    p = Path(arg)
    return p / "project.json" if p.is_dir() else p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except VoxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.cmd == "init":
        folder = Path(args.folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "project.json"
        if target.exists():
            raise VoxError(f"{target} already exists")
        target.write_text(json.dumps(EXAMPLE, indent=2) + "\n", encoding="utf-8")
        Registry(folder).add_stock("narrator")
        print(f"created {target}\nnext: voxreel run {target}")
        return 0
    if args.cmd == "run":
        result = pipeline.run(_project_file(args.project), force=args.force, only=args.only)
        print(f"done: {result['output']}  ({result['scenes']} scenes, {result['done']} steps run, "
              f"{result['cached']} cached)\nprovenance: {result['provenance']}")
        return 0
    if args.cmd == "status":
        spec = load_spec(_project_file(args.project))
        print(f"{spec.title}  {spec.width}x{spec.height}@{spec.fps}  purpose={spec.purpose}")
        for sc in spec.scenes:
            print(f"  {sc.id:<14} voice={sc.voice:<12} {sc.text[:60]}")
        return 0
    if args.cmd == "doctor":
        ok = True
        for tool in ("ffmpeg", "ffprobe"):
            try:
                print(f"ok   {tool}: {media.need(tool)}")
            except VoxError as exc:
                ok = False
                print(f"FAIL {exc}")
        font = media.find_font()
        print(f"ok   font: {font}" if font else "warn no font found (set VOXREEL_FONT); labels and mock text need one")
        print("providers:", json.dumps(available()))
        return 0 if ok else 1
    if args.cmd == "voice":
        reg = Registry(Path(args.folder))
        if args.vcmd == "add-stock":
            reg.add_stock(args.id, args.provider_voice)
            print(f"added stock voice {args.id}")
        elif args.vcmd == "clone":
            rec = reg.add_cloned(args.id, Path(args.sample), args.speaker, args.statement,
                                 args.scope.split(","), args.expires,
                                 Path(args.recording) if args.recording else None,
                                 args.confirm, args.provider_voice)
            print(f"added cloned voice {args.id} with consent record {rec['id']}")
        elif args.vcmd == "revoke":
            reg.revoke(args.id, args.reason)
            print(f"revoked {args.id}")
        else:
            consents = {c["id"]: c for c in reg.consents()}
            for vid, v in reg.voices().items():
                if v["kind"] == "stock":
                    print(f"{vid:<14} stock")
                    continue
                c = consents.get(v.get("consent_id"), {})
                state = "REVOKED" if c.get("revoked_at") else f"expires {c.get('expires_at') or 'never'}"
                print(f"{vid:<14} cloned  speaker={c.get('speaker')}  scope={','.join(c.get('scope', []))}  {state}")
        return 0
    return 2
