"""Which game is the CURRENT campaign about. Set once per campaign by daily_runner-2.process_campaign."""
import os


def set_game(name: str) -> None:
    os.environ["VYRO_GAME_NAME"] = (name or "").strip()


def current_game(default: str = "this game") -> str:
    return (os.environ.get("VYRO_GAME_NAME") or "").strip() or default
