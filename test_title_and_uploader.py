import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import tempfile

from title_tag_engine import (
    generate_smart_title_and_hashtags,
    extract_topical_entities,
    find_punchy_quote,
    generate_candidate_titles,
    clean_transcript_text,
    score_and_rank_titles_with_jev,
)
from title_seo import validate_title, ALL_PATTERNS
from uploader import (
    load_upload_config,
    save_upload_config,
    upload_clip_to_platforms,
    get_youtube_auth_url,
    exchange_youtube_code
)
import app

class TestTitleTagAndUploader(unittest.TestCase):

    def setUp(self):
        self.sample_transcript = (
            "So basically we are testing this brand new autonomous AI decision engine called Jev. "
            "It processes state transitions in under two hundred milliseconds without any hallucinations. "
            "Most people don't realize how insane this actually is for real-time video workflows."
        )

    def test_01_clean_transcript_text(self):
        """Transcript cleaning removes bracket markers and normalizes whitespace."""
        raw = "[Applause] So basically we are testing this! (cheering)"
        cleaned = clean_transcript_text(raw)
        self.assertNotIn("[Applause]", cleaned)
        self.assertNotIn("(cheering)", cleaned)
        self.assertTrue(len(cleaned) > 0)

    def test_02_entity_and_quote_extraction(self):
        """Topical entities and punchy quotes are extracted from transcript."""
        entities = extract_topical_entities(self.sample_transcript)
        entity_names = [e[0] for e in entities]
        self.assertTrue(any("Jev" in n or "Ai" in n or "Decision" in n for n in entity_names))

        quote = find_punchy_quote(self.sample_transcript)
        # Quote can be up to ~120 chars depending on sentence splitting
        self.assertIsNotNone(quote)
        self.assertGreater(len(quote), 10)

    def test_03_title_candidates_generation(self):
        """
        Candidates are diverse, and every one is a validatable SEO title with a keyword.

        This previously asserted the presence of two hardcoded ids, `curiosity_gap` and
        `pattern_interrupt`. Those belonged to the old five-branch entity engine and were
        its only framework labels; `title_seo` replaced it with a pattern bank, so the ids
        no longer exist and the framework a given clip leads with now depends on the video
        context and the rotation.

        Asserting those two ids is therefore not a contract the engine can still honour.
        What the test was really protecting -- that the candidate set is diverse and that no
        candidate is a junk or over-length title -- is now asserted directly and STRICTLY:
        uniqueness of ids and frameworks, the 5-key shape Task 5 indexes, a `pattern_id`
        that resolves against ALL_PATTERNS, and a title that passes `validate_title`.
        """
        candidates = generate_candidate_titles(self.sample_transcript, "tech_ai")
        self.assertGreaterEqual(len(candidates), 3)
        ids = [c["id"] for c in candidates]
        self.assertEqual(len(ids), len(set(ids)), f"candidate ids repeat: {ids}")
        frameworks = [c["framework"] for c in candidates]
        self.assertEqual(len(frameworks), len(set(frameworks)),
                         f"candidate frameworks repeat: {frameworks}")
        pattern_ids = {p["id"] for p in ALL_PATTERNS}
        for c in candidates:
            self.assertLessEqual(len(c["title"]), 100)
            self.assertGreater(len(c["title"]), 10)
            self.assertEqual(
                set(c), {"id", "framework", "title", "pattern_id", "keyword"},
                f"candidate shape drifted: {sorted(c)}")
            self.assertIn(c["pattern_id"], pattern_ids)
            self.assertTrue(c["keyword"].strip(),
                            f"{c['title']!r} has no keyword to search on")
            ok, reason = validate_title(c["title"])
            self.assertTrue(ok, f"{c['title']!r} rejected by the SEO gate: {reason}")

    def test_04_full_title_and_tag_engine(self):
        """Full engine returns title, hashtags, candidates, niche, platform metadata."""
        # Actual signature: generate_smart_title_and_hashtags(transcript_text, category, api_key)
        meta = generate_smart_title_and_hashtags(
            transcript_text=self.sample_transcript,
            category="tech_ai"
        )
        self.assertIn("suggested_title", meta)
        self.assertIn("suggested_hashtags", meta)
        self.assertIn("candidates", meta)
        self.assertIn("platform_metadata", meta)
        self.assertIn("niche", meta)

        title = meta["suggested_title"]
        self.assertLessEqual(len(title), 100)
        self.assertGreater(len(title), 5)

        hashtags = meta["suggested_hashtags"]
        self.assertTrue(any("#" in h for h in hashtags))

        platforms = meta["platform_metadata"]
        self.assertIn("youtube", platforms)
        self.assertIn("tiktok", platforms)
        self.assertIn("instagram", platforms)

    def test_05_uploader_config_management(self):
        """Config loads defaults, saves updates, and persists to disk."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = Path(tmp_dir) / "upload_config.json"
            with patch("uploader.UPLOAD_CONFIG_PATH", config_file):
                cfg = load_upload_config()
                self.assertFalse(cfg.get("auto_upload", {}).get("enabled", False))
                self.assertIn("youtube", cfg)
                self.assertIn("ayrshare", cfg)

                # Mutate and save
                cfg["auto_upload"]["enabled"] = True
                cfg["auto_upload"]["default_privacy"] = "unlisted"
                cfg["ayrshare"]["api_key"] = "test_token_123"
                saved = save_upload_config(cfg)
                self.assertTrue(saved)

                # Reload and verify persistence
                reloaded = load_upload_config()
                self.assertTrue(reloaded["auto_upload"]["enabled"])
                self.assertEqual(reloaded["auto_upload"]["default_privacy"], "unlisted")
                self.assertEqual(reloaded["ayrshare"]["api_key"], "test_token_123")

    def test_06_uploader_dispatch_persists_receipt(self):
        """upload_clip_to_platforms dispatches, saves receipt into clip JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            clip_mp4 = out_dir / "test_clip_101.mp4"
            clip_mp4.write_text("fake video content", encoding="utf-8")

            clip_json = out_dir / "test_clip_101.json"
            clip_meta = {
                "filename": "test_clip_101.mp4",
                "suggested_title": "Why You Must Know This AI Secret! 🤯",
                "suggested_hashtags": ["#tech", "#shorts", "#viral"],
                "duration": 28
            }
            clip_json.write_text(json.dumps(clip_meta), encoding="utf-8")

            fake_cfg = {
                "youtube": {"is_authenticated": True, "token_file": "fake.json"},
                "ayrshare": {"api_key": "test_ayr_key", "is_configured": True},
                "auto_upload": {"enabled": False, "platforms": ["youtube"], "default_privacy": "public"}
            }

            with patch("uploader.OUTPUT_DIR", out_dir), \
                 patch("uploader.load_upload_config", return_value=fake_cfg), \
                 patch("uploader.upload_video_to_youtube") as mock_yt, \
                 patch("uploader.upload_to_social_platforms") as mock_ayr:

                mock_yt.return_value = {
                    "success": True,
                    "platform": "youtube",
                    "status": "uploaded",
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Why You Must Know This AI Secret! 🤯"
                }
                mock_ayr.return_value = {
                    "success": True,
                    "platform": "ayrshare",
                    "status": "uploaded",
                    "post_id": "ayr_post_999",
                    "target_platforms": ["tiktok"]
                }

                result = upload_clip_to_platforms(
                    clip_filename="test_clip_101.mp4",
                    title="Custom Hook Title!",
                    description="Custom Hook Title!\n\n#tech #shorts #viral",
                    hashtags=["#tech", "#shorts", "#viral"],
                    platforms=["youtube", "tiktok"],
                    privacy_status="unlisted"
                )

                # Return shape: {"success": bool, "results": {"youtube": {...}, ...}}
                self.assertTrue(result.get("success"))
                results = result.get("results", {})
                self.assertIn("youtube", results)
                self.assertEqual(results["youtube"]["video_id"], "dQw4w9WgXcQ")

                # Verify clip json updated with upload_history
                saved_json = json.loads(clip_json.read_text(encoding="utf-8"))
                self.assertIn("upload_history", saved_json)
                self.assertEqual(len(saved_json["upload_history"]), 1)
                self.assertIn("youtube", saved_json["upload_history"][0]["results"])

    def test_07_flask_upload_api_endpoints(self):
        """Flask /api/upload/config GET/POST and /api/upload/clip work correctly."""
        app.app.config["TESTING"] = True
        client = app.app.test_client()

        # GET config – returns nested {success, config: {youtube, ayrshare, auto_upload}}
        res = client.get("/api/upload/config")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        cfg = data.get("config", {})
        self.assertIn("youtube", cfg)
        self.assertIn("ayrshare", cfg)
        self.assertIn("auto_upload", cfg)

        # POST config – expects {ayrshare_key, auto_upload: {enabled, platforms, default_privacy}}
        res = client.post("/api/upload/config", json={
            "auto_upload": {
                "enabled": False,
                "default_privacy": "private",
                "platforms": ["youtube"]
            }
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get("success"))

        # POST upload clip – expects {filename, title, description, hashtags, platforms, privacy}
        with patch("app.upload_clip_to_platforms") as mock_dispatch:
            mock_dispatch.return_value = {
                "youtube": {"status": "uploaded", "video_id": "abc1234"}
            }
            res = client.post("/api/upload/clip", json={
                "filename": "test_clip.mp4",
                "title": "Testing Title",
                "description": "Testing Description",
                "hashtags": ["#shorts"],
                "platforms": ["youtube"],
                "privacy": "public"
            })
            self.assertEqual(res.status_code, 200)
            post_data = res.get_json()
            self.assertIn("youtube", post_data)

    def test_08_jev_scoring_with_visual_context_and_guardrails(self):
        """Jev payload includes visual_context and handles multi-question decisions."""
        candidates = [
            {"id": "cand_1", "framework": "Curiosity", "title": "Watch This: The Truth About Snow Cave 🤯"},
            {"id": "cand_2", "framework": "Loss Aversion", "title": "The Mistake Everyone Makes With Snow Cave 🔥"},
        ]
        visual_ctx = {
            "scene_type": "Outdoor snowy mountain",
            "action_detected": "Digging into snow cave with shovel",
            "on_screen_text": "ALASKA -30F"
        }

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "data": {
                "answers": {
                    "best_title_hook": {"choice": "cand_2", "confidence": 0.91},
                    "content_niche": {"choice": "outdoors_survival"},
                    "clickbait_penalty": 0.05,
                    "visual_clip_alignment": 0.95,
                    "hook_ctr_potential": {"score": 3.0}
                }
            }
        }

        with patch("requests.post", return_value=fake_resp) as mock_post:
            best_id, niche, conf = score_and_rank_titles_with_jev(
                candidates=candidates,
                transcript_text=self.sample_transcript,
                api_key="fake_jev_key_123",
                topics=["snow cave"],
                visual_context=visual_ctx
            )
            self.assertEqual(best_id, "cand_2")
            self.assertEqual(niche, "outdoors_survival")
            self.assertAlmostEqual(conf, 0.91)

            # Verify the payload structure passed to Jev API
            self.assertTrue(mock_post.called)
            _args, kwargs = mock_post.call_args
            payload = kwargs.get("json", {})
            self.assertIn("visual_clip_context", payload.get("state", {}))
            self.assertEqual(payload["state"]["visual_clip_context"], visual_ctx)
            questions = payload.get("questions", {})
            self.assertIn("visual_clip_alignment", questions)
            self.assertIn("clickbait_penalty", questions)
            self.assertEqual(questions["visual_clip_alignment"]["type"], "noul")
            self.assertEqual(questions["clickbait_penalty"]["type"], "noul")

    def test_09_jev_low_confidence_falls_back_to_local_ranking(self):
        """When Jev confidence is below 0.60, system falls back to deterministic local ranking."""
        candidates = [
            {"id": "poor_cand", "framework": "A", "title": "A Long Windy Lead In About Snow Cave"},
            {"id": "sharp_cand", "framework": "B", "title": "What Nobody Tells You About Snow Cave 🔥"},
        ]
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "data": {
                "answers": {
                    "best_title_hook": {"choice": "poor_cand", "confidence": 0.42},  # Low confidence < 0.60
                    "content_niche": {"choice": "outdoors_survival"},
                    "clickbait_penalty": 0.02,
                    "visual_clip_alignment": 0.90
                }
            }
        }
        with patch("requests.post", return_value=fake_resp):
            best_id, _niche, conf = score_and_rank_titles_with_jev(
                candidates=candidates,
                transcript_text=self.sample_transcript,
                api_key="fake_jev_key_123",
                topics=["snow cave"]
            )
            # Must fall back to local ranking, which prefers sharp_cand over poor_cand
            self.assertEqual(best_id, "sharp_cand")
            self.assertEqual(conf, 0.70)

    def test_10_jev_clickbait_penalty_triggers_fallback(self):
        """When Jev detects clickbait probability > 0.50, title is rejected in favor of local ranking."""
        candidates = [
            {"id": "clickbait_cand", "framework": "A", "title": "You Will Not Believe This Insane Secret"},
            {"id": "honest_cand", "framework": "B", "title": "What Nobody Tells You About Snow Cave 🔥"},
        ]
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "data": {
                "answers": {
                    "best_title_hook": {"choice": "clickbait_cand", "confidence": 0.95},
                    "content_niche": {"choice": "outdoors_survival"},
                    "clickbait_penalty": 0.88,  # Deceptive!
                    "visual_clip_alignment": 0.85
                }
            }
        }
        with patch("requests.post", return_value=fake_resp):
            best_id, _niche, conf = score_and_rank_titles_with_jev(
                candidates=candidates,
                transcript_text=self.sample_transcript,
                api_key="fake_jev_key_123",
                topics=["snow cave"]
            )
            # Must reject clickbait candidate and fall back to local ranking
            self.assertEqual(best_id, "honest_cand")
            self.assertEqual(conf, 0.70)

    def test_11_jev_visual_mismatch_triggers_fallback(self):
        """When title visually conflicts with the clip frame (prob < 0.40), fallback triggers."""
        candidates = [
            {"id": "wrong_scene_cand", "framework": "A", "title": "Inside The Luxury Superyacht"},
            {"id": "right_scene_cand", "framework": "B", "title": "What Nobody Tells You About Snow Cave 🔥"},
        ]
        visual_ctx = {"scene_type": "Outdoor snowy arctic mountain"}
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "data": {
                "answers": {
                    "best_title_hook": {"choice": "wrong_scene_cand", "confidence": 0.88},
                    "content_niche": {"choice": "business_money"},
                    "clickbait_penalty": 0.10,
                    "visual_clip_alignment": 0.12  # Visual mismatch!
                }
            }
        }
        with patch("requests.post", return_value=fake_resp):
            best_id, _niche, conf = score_and_rank_titles_with_jev(
                candidates=candidates,
                transcript_text=self.sample_transcript,
                api_key="fake_jev_key_123",
                topics=["snow cave"],
                visual_context=visual_ctx
            )
            self.assertEqual(best_id, "right_scene_cand")
            self.assertEqual(conf, 0.70)

    def test_12_orchestrator_passes_visual_context_and_exposes_grounding(self):
        """generate_smart_title_and_hashtags accepts visual_context and reports visual_grounding."""
        visual_ctx = {"scene_type": "Gym setting", "action": "Bench press"}
        meta = generate_smart_title_and_hashtags(
            transcript_text=self.sample_transcript,
            category="tech_ai",
            visual_context=visual_ctx
        )
        self.assertIn("visual_grounding", meta)
        self.assertTrue(meta["visual_grounding"])

        # Without visual context
        meta_no_vis = generate_smart_title_and_hashtags(
            transcript_text=self.sample_transcript,
            category="tech_ai"
        )
        self.assertFalse(meta_no_vis["visual_grounding"])

if __name__ == "__main__":
    unittest.main()
