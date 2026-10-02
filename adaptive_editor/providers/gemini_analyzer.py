"""Gemini reviewer. Optional. Failure never stops the editor."""
from __future__ import annotations

import os


class GeminiReviewProvider:
    name = "gemini"

    def analyze(self, shots: list, frames: list | None = None) -> dict:
        key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not key or not shots:
            print("[adaptive] Gemini unavailable")
            return {"ok": False, "provider": self.name, "reason": "no key or no shots"}
        try:
            import google.generativeai as genai
        except Exception as exc:
            print(f"[adaptive] Gemini unavailable ({exc})")
            return {"ok": False, "provider": self.name, "reason": str(exc)}
        summary = "; ".join(f"{s.clip} {s.event} {s.start}-{s.end}" for s in shots[:8])
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel("gemini-3.5-flash")
            resp = model.generate_content(
                "Return one sentence naming the strongest hook event from this list. Do not invent events. " + summary
            )
            text = (getattr(resp, "text", None) or "").strip()
            print(f"[adaptive] Gemini review: {text[:160]}")
            return {"ok": bool(text), "provider": self.name, "notes": text[:240], "confidence": 0.6}
        except Exception as exc:
            print(f"[adaptive] Gemini unavailable ({exc})")
            return {"ok": False, "provider": self.name, "reason": str(exc)[:180]}
