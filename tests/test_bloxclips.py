"""Offline tests for the footage hub resolver (fixture = real folder tree + sample files)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bloxclips_footage as B

M = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "manifest_sample.json")))


def test_footage_url():
    h = B.resolve_campaign(link="https://footage.bloxclips.com/scrub-or-die-campaign#/footage/Clips", manifest=M)
    assert h and h.name == "Scrub or Die Campaign" and h.footage_url.endswith("/scrub-or-die-campaign")
    assert len(h.clips) == 5, [c["name"] for c in h.clips]
    assert all(c["url"].startswith("https://files.bloxclips.com/o/") for c in h.clips)
    assert not any("Music" in c["folder"] or "Sound" in c["folder"] for c in h.clips)
    assert [e["name"] for e in h.examples] == ["Scrub or Die SD16.mp4"]
    assert len(h.music) == 1 and len(h.sfx) == 1
    print("PASS test_footage_url")


def test_content_rewards_link_alone():
    titles = {"https://contentrewards.com/c/campaigns/38bf82b8-47f0-4956-a0f8-9a4c73ba0a1f": "Scrub or Die [Horror Game] | Content Rewards"}
    h = B.resolve_campaign(link="https://contentrewards.com/c/campaigns/38bf82b8-47f0-4956-a0f8-9a4c73ba0a1f",
                           manifest=M, title_fn=lambda u: titles.get(u, ""))
    assert h and h.slug == "scrub-or-die-campaign" and "page-title" in h.matched_by
    print("PASS test_content_rewards_link_alone")


def test_name_only_and_no_mixing():
    h = B.resolve_campaign(name="Scrub or Die", manifest=M)
    assert h and not any("Seed" in c["name"] or "Disaster" in c["name"] for c in h.clips)
    r = B.resolve_campaign(name="Build a Ring Farm 2", manifest=M)
    assert r and [c["name"] for c in r.clips] == ["Seeds 1.mp4"]
    assert B.resolve_campaign(name="How to Fisch", manifest=M) is None
    assert B.resolve_campaign(link="https://contentrewards.com/c/x", manifest=M, title_fn=lambda u: "") is None
    print("PASS test_name_only_and_no_mixing")


def test_event_hints():
    assert B.event_hint("Clip 14 - Cleaning slime + jumpscare.mp4") == "jumpscare"
    assert B.event_hint("Clip 42 - Monster chase.mp4") == "monster_chase"
    assert B.event_hint("Clip 5 - Shop.mp4") == "menu"
    assert B.event_hint("Clip 99.mp4") == "unknown"
    print("PASS test_event_hints")


if __name__ == "__main__":
    test_footage_url(); test_content_rewards_link_alone(); test_name_only_and_no_mixing(); test_event_hints()


def test_resolver_bloxclips_branch_and_drive_branch_intact():
    import google_doc_reader as G
    got = {}
    B.list_clips = lambda url, name="": [
        {"clip_id": "a", "name": "Clip 1.mp4", "url": "https://files.bloxclips.com/o/a/Clip 1.mp4", "duration_ms": "9000"},
        {"clip_id": "b", "name": "Clip 2.mp4", "url": "https://files.bloxclips.com/o/b/Clip 2.mp4", "duration_ms": "26000"}]
    url = G.resolve_and_download_footage(dest_path="work/x.mp4", source_clip_url="https://footage.bloxclips.com/scrub-or-die-campaign",
                                         download_url_fn=lambda u, d: got.update(url=u))
    assert got["url"].endswith("Clip 2.mp4"), got            # longest clip, downloaded as a direct file
    called = {}
    G.resolve_and_download_footage(dest_path="work/y.mp4", source_clip_url="https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
                                   pick_drive_folder_fn=lambda u, d: called.update(u=u))
    assert "drive.google.com" in called["u"], called         # old Drive-folder path still works
    print("PASS test_resolver_bloxclips_branch_and_drive_branch_intact")


def test_generic_rules_any_campaign():
    import campaign_rules as R
    t = R.rules_text({"name_hint": "Some New Game [Horror Game]", "platform": "whop", "requirements": ["Show the logo."]}, "")
    assert t.startswith("Some New Game\n") and "Show the logo." in t and "Game is called Some New Game on Roblox" in t
    print("PASS test_generic_rules_any_campaign")


if __name__ == "__main__":
    test_resolver_bloxclips_branch_and_drive_branch_intact(); test_generic_rules_any_campaign()
