"""Gemini vision review. Optional. Uses shared runtime. Failure never stops the editor."""
from __future__ import annotations

import os

import gemini_runtime


class GeminiReviewProvider:
    name = "gemini"

    def analyze(self, shots: list, frames: list | None = None) -> dict:
        keys = [k.strip() for k in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
        one = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if one and one not in keys:
            keys.insert(0, one)
        if not keys or not shots:
            print("[adaptive] Gemini unavailable")
            return {"ok": False, "provider": self.name, "reason": "no key or no shots"}
        try:
            import google.generativeai as genai
        except Exception as exc:
            print(f"[adaptive] Gemini unavailable ({exc})")
            return {"ok": False, "provider": self.name, "reason": str(exc)}
        rt = gemini_runtime.RUNTIME
        model_name = rt.choose_model("vision")
        if not model_name:
            print("[adaptive] skip quarantined models")
            return {"ok": False, "provider": self.name, "reason": "no model"}
        if model_name in rt.quarantined:
            print(f"[story] skip quarantined model={model_name}")
        evidence = "; ".join(
            f"{s.event} {s.start}-{s.end} supported={s.supported}" for s in shots[:8] if s.supported
        )
        rt.note_attempt(model_name)
        try:
            genai.configure(api_key=keys[0])
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(
                "Name only events already marked supported. Do not invent events. " + evidence
            )
            text = (getattr(resp, "text", None) or "").strip()
            rt.mark_success(model_name)
            print(f"[adaptive] vision model={model_name}")
            return {"ok": bool(text), "provider": self.name, "notes": text[:240], "model": model_name}
        except Exception as exc:
            kind = rt.classify(exc)
            if kind == "QUOTA_EXHAUSTED":
                rt.quota_errors += 1
                rt.quarantine(model_name, kind)
            else:
                rt.mark_failure(model_name, kind)
            print(f"[adaptive] Gemini unavailable ({exc})")
            return {"ok": False, "provider": self.name, "reason": str(exc)[:180]}
