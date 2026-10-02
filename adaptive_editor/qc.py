"""QC before accept. Does not upload."""
from __future__ import annotations

import os


def qc(plan: dict, rendered: str = "") -> dict:
    fails = []
    clips = plan.get("clips") or []
    dur = float(plan.get("duration_target") or 0)
    if dur < 6:
        fails.append(f"too short {dur}")
    if dur > 32:
        fails.append(f"too long {dur}")
    if not clips:
        fails.append("no clips")
    if not plan.get("hook"):
        fails.append("no hook")
    keys = [(c.get("clip"), c.get("start")) for c in clips]
    if len(keys) != len(set(keys)):
        fails.append("duplicate sequence")
    script = " ".join(v.get("text", "") for v in plan.get("voice") or [])
    reasons = {c.get("reason") for c in clips}
    if "rare" in script.lower() and "RARE_REVEAL" not in reasons and "CHARACTER_REVEAL" not in reasons:
        fails.append("fabricated rare claim")
    if rendered and (not os.path.isfile(rendered) or os.path.getsize(rendered) < 500):
        fails.append("render missing")
    report = {"ok": not fails, "fails": fails, "duration": dur, "shots": len(clips)}
    print(f"[adaptive] qc: {report}")
    return report
