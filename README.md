# Vyro-automation

Daily GitHub Actions bot that finds a **BloxClips / Whop** campaign, reads the brief, merges Drive footage, burns captions + ElevenLabs voice + end stickers, uploads **Instagram first** then YouTube backup, and auto-submits both links on Whop.

Instagram is the main platform. YouTube is backup / second submit.

Repo: `debashispaul2023-svg/Vyro-automation`

---

## What a Daily run does

1. Open BloxClips board  
   `https://whop.com/bloxclips/exp_EfN9ClEYDL8Bh9/app/`
2. Pick one allowed campaign (never ForgeGUI).
3. Read campaign requirements from the page + Google Doc.
4. List videos in that campaign’s Drive folder only.
5. Merge unused clips (max ~30s; 1.2x speed if needed).
6. AI decides tools from **this campaign’s rules** (voice, CTA, icon, captions).
7. Render 1080×1920 short.
8. Upload Instagram Reels (main) + YouTube unlisted (backup).
9. Auto-submit **both** URLs on that same campaign’s Submit form.
10. Mark used clips in `processed_clips.json` so they are not reused.

One run = one campaign = one clip pack. Never submit campaign A’s video to campaign B.

---

## Hard rules

- Skip **ForgeGUI**. Never join or submit it.
- Allowed names: **+1 Tongue Escape**, **Steal A Seed**, **World Athletics**, **How to Fisch**.
- How to Fisch footage folder is **only** for How to Fisch:  
  `https://drive.google.com/drive/folders/15GpPGJAMvG5ooypQMrhr5aSUZsJo2jxD`
- Do not mix Drive folders across campaigns.
- Close / skip a campaign when **≥90% budget used**.
- Follow the campaign brief. If the brief does not ask for a code, do not talk about codes.
- Spoken game name + CTA come from the brief.
- End of video only: **SAVE** sticker, then **HIT FOLLOW** sticker, plus large game-icon thumbnail.
- Karaoke captions: white text, dark navy outline, word-by-word. Not a sentence stuck on screen the whole time.
- Voice = **ElevenLabs only**. No espeak fallback. Energetic. Body first, “Save. Hit follow.” pinned to the last ~2.5s.
- Max length ~30 seconds.
- Auto-upload + auto-submit stay **ON** in Daily. Test workflow keeps uploads off.

---

## Current campaigns (`whop_campaigns.json`)

Board (all cards live here):  
`https://whop.com/bloxclips/exp_EfN9ClEYDL8Bh9/app/`

| Campaign | UUID | Footage |
|---|---|---|
| +1 Tongue Escape | `ce2f887e-f54d-43b0-a2b9-e8da505f7b7a` | Drive folder in JSON |
| How to Fisch | `b59bb70c-58bf-44c1-9e44-0b54c59d90f4` | `15GpPGJAMvG5ooypQMrhr5aSUZsJo2jxD` |
| Steal A Seed | (open from board; add UUID + Drive when known) | — |
| World Athletics | (open from board; add UUID + Drive when known) | — |

When a new BloxClips card appears: bot should open it from the board, read the Doc, find the Drive/MediaSilo link, and work it. Do **not** ask the user for the Doc URL every time.

---

## Files

| File | Job |
|---|---|
| `daily_runner.py` | Orchestrator: pick campaign → merge → pack → upload → submit |
| `whop_client.py` | Playwright login (cookie), board harvest, campaign detail, **Submit clip** form |
| `whop_campaigns.json` | Allowed names, UUIDs, Drive / Doc / game links |
| `google_doc_reader.py` | Doc text + Drive / file / MediaSilo link resolver |
| `ai_brain.py` | Requirements parse + clip rank (Gemini, multi-key) |
| `tts_engine.py` | ElevenLabs voice + word timings |
| `renderer.py` | Vertical crop / quality helpers |
| `instagram_uploader.py` | Graph Reels upload via public GitHub-release asset URL |
| `processed_clips.json` | Used clip IDs per campaign |
| `.github/workflows/vyro_daily.yml` | Daily job, 3× US peak |
| `.github/workflows/test_campaign_flow.yml` | Test: generate video, **no upload** |

---

## Whop submit UI (must match this)

Campaign detail → orange **Submit clip** → sheet **Submit video link**:

1. Paste IG or YouTube URL in the box (`https://www.tiktok.com/@username/...` placeholder).
2. Tick:
   - Posted from one of your linked accounts
   - Not already submitted to this campaign
   - Posted within the last 30 minutes
3. Tick: I’ve read the requirements…
4. Orange **Submit clip** (not Cancel).

Submit must open `/campaigns/<uuid>`, not the grid `/campaigns`. Ignore hCaptcha iframes.

If submit still fails, log will say `SUBMIT THIS URL MANUALLY:` plus the IG/YouTube links.

---

## Video pack (end of short)

- Karaoke words during gameplay (white + `#001033` outline).
- Last ~2.6s: lime **SAVE** box + spoken “Save.”
- Last ~1.3s: yellow **HIT FOLLOW** box + spoken “Hit follow.”
- Last ~3s: large game-icon thumbnail (~820px, white pad) at the bottom.
- Gameplay audio stays low under the voice.

---

## Workflows

### Daily — `vyro_daily.yml`

Runs automatically (UTC cron, US Eastern):

| UTC | US Eastern | Why |
|---|---|---|
| `0 16 * * *` | 12:00 PM ET | Lunch Reels |
| `0 21 * * *` | 5:00 PM ET | After school / work |
| `0 0 * * *` | 8:00 PM ET | Prime Reels / Shorts |

Also: Actions → Vyro Daily Full Automation → Run workflow.

Timeout: 70 minutes.

### Test — `test_campaign_flow.yml`

Same harvest + render. Uploads and Whop submit **off**. Artifact should be raw `short.mp4` (GitHub Release `test-flow-latest`), not a zip.

---

## GitHub secrets

| Secret | Use |
|---|---|
| `WHOP_COOKIE_HEADER` | Full Whop `Cookie:` header |
| `GOOGLE_DRIVE_API_KEY` | List / download Drive clips |
| `GEMINI_API_KEY` | Requirements + clip rank (`key1,key2,key3` ok) |
| `ELEVENLABS_API_KEY` | Voice |
| `IG_ACCESS_TOKEN` | Instagram Graph |
| `IG_BUSINESS_ACCOUNT_ID` | IG account |
| `ASSET_HOST_REPO` | `owner/public-repo` for public MP4 URL |
| `ASSET_HOST_TOKEN` | PAT that can create releases on that repo |
| `YOUTUBE_TOKEN_JSON` | OAuth token.json (Desktop or TV client) |

Optional: `VYRO_SESSION_COOKIE` only if Vyro.com path is used.

---

## Local / Actions command

```bash
python daily_runner.py
```

Test (no upload):

```bash
python test_campaign_flow.py
```

---

## Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Brief is only “Search / Campaigns / $0.00” | Bot read Whop chrome, not iframe | Open `apps.whop.com` iframe, then `/campaigns/<uuid>` |
| Always opens How to Fisch | Grid click by name failed | Use UUID map in `whop_client.py` |
| Submit: no URL box | Still on campaigns **grid** | `_goto_campaign_detail` then Submit clip |
| Voice ends before Save/Follow | CTA not mixed at video end | Body + delayed CTA audio, no `-shortest` |
| Same clip again | Log not written | `processed_clips.json` commit in Daily |
| Gemini 429 / broken JSON | Quota / truncated JSON | Rotate keys + salvage parser in `ai_brain.py` |
| IG 403 on release | PAT cannot create releases | `ASSET_HOST_TOKEN` needs `contents: write` on public repo |
| Job cancelled ~45m | Render + 3 submit retries | Timeout is 70m now; keep submit retries short |

---

## Do not

- Mix How to Fisch clips into Tongue Escape / Steal A Seed / World Athletics.
- Submit a clip to a different campaign than the one that produced it.
- Enable Discover auto-join unless `WHOP_ENABLE_DISCOVER=1`.
- Put client secrets, tokens, or cookies in the repo.
- Use ForgeGUI.

---

## Owner notes

- Phone-first operator. Prefer 3–4 changed files, not a full zip, unless asked.
- Main goal: Instagram views / Whop CPM. Requirements beat clever edits.
- When a new BloxClips card is green/allowed, bot should pick it up from the board without a pasted Doc link.
