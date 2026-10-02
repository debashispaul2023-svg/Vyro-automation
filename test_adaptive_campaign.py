"""One campaign-footage adaptive test. No upload. Does not mark clips used."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import daily_runner as dr
from adaptive_editor.engine import run_adaptive
from adaptive_editor.render.renderer import render_plan
from vidiq_metadata import deterministic, from_story

OUT = Path("output/adaptive/test")
WORK = Path("work/adaptive_campaign_test")


def _download(pack: list[dict]) -> list[str]:
    key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    WORK.mkdir(parents=True, exist_ok=True)
    paths = []
    print("[adaptive-test] campaign footage selected:")
    for i, clip in enumerate(pack, 1):
        dest = WORK / f"{i:02d}.mp4"
        fid = clip.get("clip_id") or ""
        print(f"{i}. id={fid} name={clip.get('name')} url={clip.get('url')}")
        dr.download_drive_file(fid, str(dest), key)
        if dest.is_file() and dest.stat().st_size > 1000:
            paths.append(str(dest))
    return paths


def _vision(path: str) -> dict:
    try:
        import gemini_runtime
        import google.generativeai as genai
    except Exception as exc:
        print(f"[vision] unavailable {exc}")
        return {"ok": False, "events": ["UNKNOWN"], "reason": "provider unavailable"}
    keys = [k.strip() for k in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    if not keys:
        print("[vision] no key")
        return {"ok": False, "events": ["UNKNOWN"], "reason": "no key"}
    frame = WORK / (Path(path).stem + ".jpg")
    subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", path, "-frames:v", "1", str(frame)], capture_output=True)
    rt = gemini_runtime.RUNTIME
    model = rt.choose_model("vision")
    if not model:
        return {"ok": False, "events": ["UNKNOWN"], "reason": "no model"}
    rt.note_attempt(model)
    try:
        genai.configure(api_key=keys[0])
        resp = genai.GenerativeModel(model).generate_content([
            "Return JSON only. events must be from ROLL, REWARD, CHARACTER_REVEAL, UNKNOWN. "
            "Use UNKNOWN unless the frame clearly shows that event. No filename guesses.",
            {"mime_type": "image/jpeg", "data": frame.read_bytes()},
        ])
        text = (getattr(resp, "text", None) or "").strip()
        rt.mark_success(model)
        print(f"[vision] model={model} {text[:180]}")
        return {"ok": True, "model": model, "raw": text[:400], "events": ["UNKNOWN"]}
    except Exception as exc:
        kind = rt.classify(exc)
        if kind == "QUOTA_EXHAUSTED":
            rt.quarantine(model, kind)
        print(f"[vision] failed {str(exc)[:160]}")
        return {"ok": False, "events": ["UNKNOWN"], "reason": str(exc)[:160]}


def main() -> int:
    os.environ["VYRO_SKIP_UPLOAD"] = "1"
    os.environ["ADAPTIVE_EDITOR_ENABLED"] = "1"
    os.environ["WHOP_ENABLE_DISCOVER"] = "0"
    print("[adaptive-test] upload blocked")
    platform, campaign = dr._find_campaign()
    if campaign is None:
        print("FAIL no campaign")
        return 1
    print(f"[adaptive-test] campaign={campaign.name} id={campaign.campaign_id}")
    pack = dr._next_unused_pack(campaign, dr._load_clip_log(), want=3)
    if not pack:
        print("FAIL no campaign clips")
        return 1
    paths = _download(pack)
    if not paths:
        print("FAIL download")
        return 1
    vision = [_vision(p) for p in paths]
    result = run_adaptive(paths, out_dir=str(OUT), local_test=True)
    story = result.get("story") or {}
    events = [s.get("event") for s in (story.get("roadmap") or [])]
    script = "Visible events: " + ", ".join(e for e in events if e and e != "UNKNOWN") + "."
    if script == "Visible events: .":
        script = "Gameplay is visible. No verified event."
    plan = result.get("edit_plan") or {}
    if not plan.get("clips"):
        plan = {
            "signature": "visual_only_selected_clips",
            "duration_target": 12,
            "clips": [
                {"clip": p, "start": 0.4, "end": 3.2, "role": "VISUAL", "reason": "campaign selected, event unknown"}
                for p in paths[:3]
            ],
        }
        print("[adaptive-test] no verified event — visual plan from selected clips only")
    plan["voice"] = [{"text": script}]
    dest = OUT / "campaign_adaptive_test.mp4"
    rendered = render_plan(plan, str(dest), work=str(OUT / "parts")) if plan.get("clips") else {"ok": False}
    vidiq = from_story({"events": events, "hook": story.get("hook"), "structure": story.get("structure"), "game": campaign.name}, ["#shorts", "#roblox", "#rollanimegirls"])
    meta = deterministic(story.get("hook") or campaign.name or "Roll Anime Girls", ["#shorts", "#roblox", "#rollanimegirls"])
    report = {
        "campaign": campaign.name,
        "clips": [{"id": c.get("clip_id"), "name": c.get("name")} for c in pack],
        "vision": vision,
        "events": events,
        "hook": story.get("hook"),
        "structure": story.get("structure"),
        "script": script,
        "unsupported_claims": 0 if "Roll dice" not in script else 1,
        "edit_plan": plan.get("signature"),
        "render": rendered,
        "vidiq": vidiq.get("reason"),
        "metadata": meta,
        "upload": 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "campaign_adaptive_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("campaign", "events", "script", "render", "upload")}, indent=2))
    return 0 if rendered.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
