# Step 1 — title / description / tags fixes

Root causes found
1. daily_runner.py `_force_upload_title` replaced EVERY campaign's title+description with a hardcoded
   "Roll Anime Girls" text before validation -> the checker never validated the real title.
2. vidiq_metadata.deterministic always appended "#rollanimegirls" + "Roll Anime Girls is a Roblox RNG tycoon"
   to the description of every campaign (vidIQ is never configured, so this fallback ALWAYS ran).
3. youtube_uploader SHARED_TAGS added "Roll Anime Girls / Roblox RNG / tycoon / offline money" to every upload.
4. ai_generate_metadata had no idea which game the campaign was (generic prompt, anime-only name patch).

Fixes
- daily_runner.py: guard now validates the REAL title/description (only fixes '#shorts' spacing).
- vidiq_metadata.py: deterministic(hook, tags, game=...) uses only the current campaign's game.
- daily_runner-2.py: passes campaign.name as `game` to metadata + fallback.
- ai_brain.py: ai_generate_metadata(game=...) -> strict prompt, official 'Game is called <game>.' line, no other game names.
- youtube_uploader.py: SHARED_TAGS = Roblox, shorts; per-game tags from CAMPAIGN_LOCK; added Scrub or Die entry.

Still hardcoded to Roll Anime Girls (Step 2, editing): adaptive_bridge.py, adaptive_editor/{edit_planner,caption_planner,voice_planner}.py,
story_engine.py, campaign_pack.py (anime-only branch).

# Step 1b — voice
- tts_engine.py: DEFAULT_VOICE = mI8xLTBNjMXAf31I4xlB ("Loyal - Viral Shortform Content"), model eleven_v4
  (fallback eleven_multilingual_v2, then eleven_turbo_v2_5). eleven_v4 is sent WITHOUT custom voice_settings
  (same as the Shorts were generated); old models keep the tuned settings.
- GitHub secret ELEVENLABS_VOICE_ID still overrides the default -> delete or update it.

# Step 2 - editing (studio_editor.py)
New module `studio_editor.py` (on by default, `STUDIO_EDITOR=0` turns it off -> old renderer):
- VIDEO LENGTH IS DERIVED FROM THE VOICE: length = voice_end / 1.08 + 0.35s. No freeze-frame, no 16s minimum padding
  (the old `_fit_video_to_voice` padded video to >=16s, so the voice ended early). Logged as `voice_covers=NN%`.
- Source is cut into motion-picked, speed-ramped windows that fill exactly that length.
- One-word captions (word timings from ElevenLabs alignment), game name/keywords in red, pop animation.
- 9:16 blur-fill layout; works for landscape/portrait sources, with or without source audio.
- Audio: voice + ducked game audio + ducked Wii Party music bed + SFX (intro, pop, riser, bang), loudnorm -14 LUFS.
- Scripts per game in `game_profiles.json` (Scrub or Die, Roll Anime Girls, How to Fisch, +1 Tongue Escape, generic).
  Only true statements; add a game by adding a block.
- Returns ok=False (never raises): daily_runner-2 falls back to the old renderer.
Hardcoded "Roll Anime Girls" removed from: adaptive_bridge.py, adaptive_editor planners, memory/schemas.py, story_engine.script_from_shots
(now read the current game via game_context.py, set once per campaign).
Tests: `python3 tests/test_studio_editor.py` (6 pass). The 3 failures in adaptive_editor/tests also fail on the ORIGINAL code.
Assets: assets/sfx/*.mp3 (your SFX + Wii Party), assets/fonts/Inter-SemiBold.otf.
