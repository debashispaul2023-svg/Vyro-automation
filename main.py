"""
main.py

Orchestrates the full Vyro campaign pipeline end-to-end:
  1. Parse campaign requirements (JSON file / JSON string / text prompt)
  2. Render the source video into a compliant 9:16 short
  3. Generate title/description/tags
  4. Run strict pre-upload validation
  5. Upload the video to YouTube via the YouTube Data API
  6. Print/save the resulting video URL for you to manually copy into Vyro
     (Vyro has no public submission API, so that last step stays manual —
     see README for why.)

Usage:
    python main.py --campaign campaign.json --source input_16x9.mp4 \
        --hook "He gave away $100,000 in 60 seconds" \
        --summary "Clipped from the original livestream." \
        --output output/short.mp4 \
        --privacy unlisted
"""

from __future__ import annotations

import argparse
import json
import sys

from requirements_parser import RequirementsParseError, parse_campaign
from renderer import RenderError, render_short
from metadata import MetadataError, generate_metadata
from checker import ValidationError, validate_or_raise
from youtube_uploader import UploadError, upload_video


def load_campaign_source(campaign_arg: str) -> str | dict:
    """If campaign_arg points to a readable file, load its contents; otherwise
    treat campaign_arg itself as a JSON string or free-text prompt."""
    try:
        with open(campaign_arg, "r", encoding="utf-8") as f:
            content = f.read()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    except (FileNotFoundError, OSError):
        return campaign_arg


def run_pipeline(
    campaign_arg: str,
    source_path: str,
    output_path: str,
    hook: str,
    summary: str,
    campaign_audio_path: str | None = None,
    privacy_status: str = "unlisted",
    token_path: str = "token.json",
    url_output_path: str = "video_url.txt",
) -> int:
    try:
        campaign_source = load_campaign_source(campaign_arg)
        req = parse_campaign(campaign_source)
        print(f"[1/4] Parsed campaign '{req.campaign_id}': "
              f"{req.min_seconds}-{req.max_seconds}s, "
              f"hashtags={req.mandatory_hashtags}")

        if campaign_audio_path:
            print("[2/4] NOTE: campaign_audio_path is set but the current "
                  "ffmpeg-based renderer does not mix campaign audio anymore.")
        render_short(
            source_path=source_path,
            output_path=output_path,
            req=req,
            fallback_caption_text=hook,
        )
        print(f"[2/4] Rendered vertical short -> {output_path}")

        meta = generate_metadata(hook=hook, summary=summary, req=req)
        print(f"[3/4] Generated metadata. Title: {meta.title}")

        validate_or_raise(
            video_path=output_path,
            title=meta.title,
            description=meta.description,
            req=req,
        )
        print("[4/4] Pre-upload validation PASSED. Uploading to YouTube...")

        result = upload_video(
            video_path=output_path,
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            privacy_status=privacy_status,
            token_path=token_path,
        )

        with open(url_output_path, "w", encoding="utf-8") as f:
            f.write(result.video_url + "\n")

        print("[5/5] Upload complete.")
        print("=" * 60)
        print(f"VIDEO URL (copy this into Vyro's submission form):")
        print(result.video_url)
        print("=" * 60)
        print(f"(also saved to {url_output_path})")

        return 0

    except RequirementsParseError as exc:
        print(f"Campaign parsing failed: {exc}", file=sys.stderr)
    except RenderError as exc:
        print(f"Rendering failed: {exc}", file=sys.stderr)
    except MetadataError as exc:
        print(f"Metadata generation failed: {exc}", file=sys.stderr)
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
    except UploadError as exc:
        print(f"YouTube upload failed: {exc}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Vyro campaign automation pipeline")
    parser.add_argument("--campaign", required=True, help="Path to campaign JSON file, or raw JSON/text")
    parser.add_argument("--source", required=True, help="Path to raw 16:9 source video")
    parser.add_argument("--output", default="output/short.mp4", help="Output path for rendered short")
    parser.add_argument("--hook", required=True, help="Video hook/title text")
    parser.add_argument("--summary", default="", help="Short description summary")
    parser.add_argument("--campaign-audio", default=None, help="Optional campaign-mandated audio file path")
    parser.add_argument(
        "--privacy",
        default="unlisted",
        choices=["public", "unlisted", "private"],
        help="YouTube privacy status for the uploaded video (default: unlisted)",
    )
    parser.add_argument("--token", default="token.json", help="Path to the YouTube OAuth token.json")
    args = parser.parse_args()

    return run_pipeline(
        campaign_arg=args.campaign,
        source_path=args.source,
        output_path=args.output,
        hook=args.hook,
        summary=args.summary,
        campaign_audio_path=args.campaign_audio,
        privacy_status=args.privacy,
        token_path=args.token,
    )


if __name__ == "__main__":
    sys.exit(main())
