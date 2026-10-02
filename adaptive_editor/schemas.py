from __future__ import annotations

from dataclasses import asdict, dataclass, field


EVENTS = (
    "HOOK",
    "RARE_REVEAL",
    "CHARACTER_REVEAL",
    "LUCKY_RESULT",
    "FAIL",
    "WIN",
    "LOSS",
    "SPIN",
    "ROLL",
    "REWARD",
    "SURPRISE",
    "FAST_ACTION",
    "FUNNY_MOMENT",
    "REACTION",
    "PROGRESSION",
    "WAITING",
    "MENU",
    "STATIC",
    "DUPLICATE",
    "LOW_INFORMATION",
)


@dataclass
class Shot:
    clip: str
    start: float
    end: float
    event: str = "LOW_INFORMATION"
    hook_score: float = 0.0
    visual_interest: float = 0.0
    motion_score: float = 0.0
    novelty_score: float = 0.0
    story_value: float = 0.0
    payoff_score: float = 0.0
    clarity_score: float = 0.0
    duplicate_penalty: float = 0.0
    static_penalty: float = 0.0
    reason: str = ""
    supported: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        row = asdict(self)
        row["duration"] = round(self.duration, 2)
        return row


@dataclass
class StoryDoc:
    source: str
    concept: str
    hook: dict
    roadmap: list
    estimated_duration: float
    confidence: float
    structure: str
    script: str
    unsupported_claims: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
