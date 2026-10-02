"""Shared Gemini model health for metadata and vision. Current run only."""
from __future__ import annotations

MODELS = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash")


class GeminiRuntime:
    def __init__(self) -> None:
        self.quarantined: set[str] = set()
        self.successful: set[str] = set()
        self.failed: set[str] = set()
        self.preferred: str | None = None
        self.attempts: dict[str, int] = {}
        self.quota_errors = 0
        self.retries = 0
        self.transient: dict[str, int] = {}

    def is_available(self, model: str) -> bool:
        return model not in self.quarantined

    def choose_model(self, task_type: str = "vision") -> str | None:
        if self.preferred and self.is_available(self.preferred):
            return self.preferred
        for model in MODELS:
            if self.is_available(model):
                return model
        return None

    def quarantine(self, model: str, reason: str) -> None:
        self.quarantined.add(model)
        self.failed.add(model)
        if self.preferred == model:
            self.preferred = None
        print(f"[gemini] model={model} status={reason}")
        print(f"[gemini] quarantine model={model} scope=current_run")

    def mark_success(self, model: str) -> None:
        self.successful.add(model)
        self.preferred = model
        print(f"[gemini] fallback model={model}" if model != MODELS[0] else f"[gemini] model={model} ok")

    def mark_failure(self, model: str, reason: str) -> None:
        self.failed.add(model)
        print(f"[gemini] model={model} failed status={reason}")

    def classify(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "429" in msg and ("quota" in msg or "resource_exhausted" in msg):
            return "QUOTA_EXHAUSTED"
        if "429" in msg or "rate" in msg:
            return "RATE_LIMITED"
        if "401" in msg or "403" in msg or "api key" in msg:
            return "AUTH_ERROR"
        if "500" in msg or "503" in msg or "unavailable" in msg:
            return "TRANSIENT_ERROR"
        if "invalid" in msg or "400" in msg:
            return "INVALID_REQUEST"
        return "TRANSIENT_ERROR"

    def note_attempt(self, model: str) -> None:
        self.attempts[model] = self.attempts.get(model, 0) + 1

    def summary(self) -> str:
        return (
            "[gemini] run summary\n"
            f"    models_attempted: {sorted(self.attempts)}\n"
            f"    models_successful: {sorted(self.successful)}\n"
            f"    models_quarantined: {sorted(self.quarantined)}\n"
            f"    quota_errors: {self.quota_errors}\n"
            f"    fallback_model: {self.preferred}\n"
            f"    total_retries: {self.retries}"
        )


RUNTIME = GeminiRuntime()


def reset() -> GeminiRuntime:
    global RUNTIME
    RUNTIME = GeminiRuntime()
    return RUNTIME
