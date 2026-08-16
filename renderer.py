"""
renderer.py

Takes a raw 16:9 source video plus parsed CampaignRequirements and produces a
campaign-compliant 1080x1920 (9:16) YouTube Short:
  - auto-crops/reframes to vertical
  - trims/pads to fit within [min_seconds, max_seconds]
  - burns in karaoke-style word-highlighted captions in the center-safe zone
  - overlays campaign watermark/overlay text if required
  - optionally mixes in a campaign-mandated audio track

Requires: moviepy>=1.0.3, ffmpeg installed on PATH.
Optional: a word-level transcript (list of {word, start, end}) for karaoke
captions. If none is supplied, a basic captions renderer is used instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
)

from requirements_parser import CampaignRequirements

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


class RenderError(Exception):
    """Raised when rendering fails or campaign constraints cannot be met."""


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


def _crop_to_vertical(clip: VideoFileClip) -> VideoFileClip:
    """Center-crop/scale a 16:9 (or any) clip to a 1080x1920 vertical frame."""
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT  # 0.5625
    source_ratio = clip.w / clip.h

    if source_ratio > target_ratio:
        # Source is wider than target -> crop width (sides), then scale
        target_w = int(clip.h * target_ratio)
        x1 = (clip.w - target_w) // 2
        cropped = clip.crop(x1=x1, x2=x1 + target_w, y1=0, y2=clip.h)
        scaled = cropped.resize(newsize=(TARGET_WIDTH, TARGET_HEIGHT))
    else:
        # Source is taller/narrower than target -> crop height, then scale
        target_h = int(clip.w / target_ratio)
        y1 = (clip.h - target_h) // 2
        cropped = clip.crop(x1=0, x2=clip.w, y1=y1, y2=y1 + target_h)
        scaled = cropped.resize(newsize=(TARGET_WIDTH, TARGET_HEIGHT))

    return scaled.set_position("center")


def _enforce_duration(clip: VideoFileClip, req: CampaignRequirements) -> VideoFileClip:
    """Trim clip to max_seconds; raise if it can't reach min_seconds."""
    if clip.duration < req.min_seconds:
        raise RenderError(
            f"Source clip duration {clip.duration:.2f}s is shorter than "
            f"campaign minimum {req.min_seconds}s. Cannot pad video content."
        )
    if clip.duration > req.max_seconds:
        clip = clip.subclip(0, req.max_seconds)
    return clip


def _build_karaoke_captions(
    words: list[WordTiming],
    video_width: int,
    video_height: int,
    base_color: str = "white",
    highlight_color: str = "yellow",
) -> list[TextClip]:
    """
    Build word-level karaoke captions: each word appears highlighted while
    spoken, placed in the center-safe zone (avoids top/bottom UI overlap).
    """
    caption_clips: list[TextClip] = []
    safe_zone_y = int(video_height * 0.45)  # center-safe zone

    # ⚡ Bolt: Cache TextClips by word to avoid expensive redundant ImageMagick subprocess calls.
    # MoviePy methods like .set_start() and .set_duration() return copies, so we can reuse the base clip.
    # Expected impact: >10x speedup for repeated words in transcripts.
    clip_cache: dict[str, TextClip] = {}

    for w in words:
        if w.end <= w.start:
            continue
        try:
            if w.word not in clip_cache:
                clip_cache[w.word] = TextClip(
                    w.word,
                    fontsize=90,
                    color=highlight_color,
                    font="Arial-Bold",
                    stroke_color="black",
                    stroke_width=3,
                    method="label",
                )

            txt_clip = (
                clip_cache[w.word]
                .set_start(w.start)
                .set_duration(w.end - w.start)
                .set_position(("center", safe_zone_y))
            )
            caption_clips.append(txt_clip)
        except Exception as exc:  # noqa: BLE001
            raise RenderError(
                f"Failed to render caption for word '{w.word}': {exc}"
            ) from exc

    return caption_clips


def _build_basic_captions(
    text: str, duration: float, video_height: int
) -> list[TextClip]:
    """Fallback: single static caption block for the whole clip duration."""
    safe_zone_y = int(video_height * 0.45)
    clip = (
        TextClip(
            text,
            fontsize=70,
            color="white",
            font="Arial-Bold",
            stroke_color="black",
            stroke_width=2,
            method="caption",
            size=(int(TARGET_WIDTH * 0.9), None),
        )
        .set_start(0)
        .set_duration(duration)
        .set_position(("center", safe_zone_y))
    )
    return [clip]


def _build_watermark(req: CampaignRequirements, duration: float) -> Optional[TextClip]:
    if not req.watermark_text:
        return None
    try:
        return (
            TextClip(
                req.watermark_text,
                fontsize=48,
                color="white",
                font="Arial-Bold",
                stroke_color="black",
                stroke_width=2,
                method="label",
            )
            .set_start(0)
            .set_duration(duration)
            .set_position(("center", TARGET_HEIGHT * 0.08))
        )
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Failed to render watermark: {exc}") from exc


def _build_overlay(req: CampaignRequirements, duration: float) -> Optional[TextClip]:
    if not req.overlay_text:
        return None
    try:
        return (
            TextClip(
                req.overlay_text,
                fontsize=60,
                color="yellow",
                font="Arial-Bold",
                stroke_color="black",
                stroke_width=2,
                method="label",
            )
            .set_start(0)
            .set_duration(duration)
            .set_position(("center", TARGET_HEIGHT * 0.85))
        )
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Failed to render overlay text: {exc}") from exc


def _mix_campaign_audio(
    video: CompositeVideoClip, audio_path: Optional[str]
) -> CompositeVideoClip:
    """Mix in a campaign-mandated audio track (ducking original audio to 30%)."""
    if not audio_path:
        return video
    if not os.path.isfile(audio_path):
        raise RenderError(f"Campaign audio file not found: {audio_path}")

    try:
        campaign_audio = AudioFileClip(audio_path).subclip(0, video.duration)
        original_audio = video.audio.volumex(0.3) if video.audio else None
        campaign_audio = campaign_audio.volumex(1.0)

        mixed = (
            CompositeAudioClip([original_audio, campaign_audio])
            if original_audio is not None
            else campaign_audio
        )
        return video.set_audio(mixed)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Failed to mix campaign audio: {exc}") from exc


def render_short(
    source_path: str,
    output_path: str,
    req: CampaignRequirements,
    words: Optional[list[WordTiming]] = None,
    fallback_caption_text: Optional[str] = None,
    campaign_audio_path: Optional[str] = None,
    fps: int = 30,
) -> str:
    """
    Full render pipeline. Returns the output_path on success.
    Raises RenderError on any failure or campaign-constraint violation.
    """
    if not os.path.isfile(source_path):
        raise RenderError(f"Source video not found: {source_path}")

    clip: Optional[VideoFileClip] = None
    try:
        clip = VideoFileClip(source_path)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Failed to open source video: {exc}") from exc

    try:
        clip = _enforce_duration(clip, req)
        vertical = _crop_to_vertical(clip)

        layers = [vertical]

        if words:
            layers.extend(_build_karaoke_captions(words, TARGET_WIDTH, TARGET_HEIGHT))
        elif fallback_caption_text:
            layers.extend(
                _build_basic_captions(
                    fallback_caption_text, vertical.duration, TARGET_HEIGHT
                )
            )

        watermark = _build_watermark(req, vertical.duration)
        if watermark:
            layers.append(watermark)

        overlay = _build_overlay(req, vertical.duration)
        if overlay:
            layers.append(overlay)

        composite = CompositeVideoClip(layers, size=(TARGET_WIDTH, TARGET_HEIGHT))
        composite = composite.set_duration(vertical.duration)
        composite = _mix_campaign_audio(composite, campaign_audio_path)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        composite.write_videofile(
            output_path,
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=4,
            verbose=False,
            logger=None,
        )
        return output_path

    except RenderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Rendering pipeline failed: {exc}") from exc
    finally:
        if clip is not None:
            clip.close()


if __name__ == "__main__":
    from requirements_parser import parse_campaign

    demo_req = parse_campaign(
        {
            "campaign_id": "demo",
            "mandatory_hashtags": ["#shorts", "#demo"],
            "required_links": ["https://vyro.ai/c/demo"],
            "watermark_text": "Use code 'DEMO'",
            "overlay_text": "Follow for more!",
            "min_seconds": 5,
            "max_seconds": 30,
        }
    )
    try:
        render_short(
            source_path="input_16x9.mp4",
            output_path="output/short_demo.mp4",
            req=demo_req,
            fallback_caption_text="This is a demo caption",
        )
        print("Render complete.")
    except RenderError as e:
        print(f"Render failed: {e}")
