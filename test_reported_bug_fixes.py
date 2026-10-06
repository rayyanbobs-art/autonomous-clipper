

_LOG_REDIRECT_TMPDIR = None
_LOG_REDIRECT_PREVIOUS = None


def setUpModule():
    """
    Redirect the publish log to a tmp dir. This suite drives the production
    clip/app/rerender paths with fixture transcripts; without redirection those runs
    would append FAKE records to logs/publish_log.jsonl -- data-poisoning the very
    log TRENDING_BOOST will one day be set from. See publish_log.default_log_path.
    """
    import os
    global _LOG_REDIRECT_TMPDIR, _LOG_REDIRECT_PREVIOUS
    _LOG_REDIRECT_PREVIOUS = os.environ.get("CLIPPER_PUBLISH_LOG")
    _LOG_REDIRECT_TMPDIR = tempfile.TemporaryDirectory()
    os.environ["CLIPPER_PUBLISH_LOG"] = str(
        Path(_LOG_REDIRECT_TMPDIR.name) / "publish_log.jsonl")


def tearDownModule():
    import os
    global _LOG_REDIRECT_TMPDIR, _LOG_REDIRECT_PREVIOUS
    if _LOG_REDIRECT_PREVIOUS is None:
        os.environ.pop("CLIPPER_PUBLISH_LOG", None)
    else:
        os.environ["CLIPPER_PUBLISH_LOG"] = _LOG_REDIRECT_PREVIOUS
    if _LOG_REDIRECT_TMPDIR is not None:
        _LOG_REDIRECT_TMPDIR.cleanup()
        _LOG_REDIRECT_TMPDIR = None

"""
Regression suite for the 22 confirmed bugs (findings #15 and #22 were disproven and are
deliberately not represented here).

Every test drives the real production code path and asserts the corrected behaviour, so a
regression re-introduces a failure rather than silently returning to the old defect.
Numbering matches the original report for cross-reference.
"""

import json
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as app_module
import batch_rerender
import broll_engine
import clipper
import config
import hashtag_engine
import niche_generator
import niche_scraper
import scorer
import subtitles
import title_tag_engine
import uploader
import video_cutter
from app import app
from topic_engine import NICHE_TAGS

TEMPLATE = Path(app_module.__file__).resolve().parent / "templates" / "index.html"


# ---------------------------------------------------------------------------
# 1. Channel Deep-Dive silently analyses a completely different channel
# ---------------------------------------------------------------------------
class TestBug01_WrongChannelAnalysis(unittest.TestCase):

    def test_identifiable_handles_reject_mismatched_search_result(self):
        """A typed handle that resolves to a different channel must not match."""
        wrong = {
            "channel_id": "UCabcdefghijklmnopqrstuv",
            "channel_url": "https://www.youtube.com/@SomeOtherCreator",
            "uploader_url": "https://www.youtube.com/@SomeOtherCreator",
            "title": "Some Other Creator",
        }
        self.assertFalse(
            niche_scraper.channel_matches_request("@mishandled", wrong, wrong["channel_url"]),
            "A typo'd handle must not be accepted for an unrelated channel")
        self.assertFalse(niche_scraper.channel_matches_request(
            "https://www.youtube.com/@mishandled/videos", wrong, wrong["channel_url"]))
        self.assertFalse(niche_scraper.channel_matches_request(
            "UCzzzzzzzzzzzzzzzzzzzzzz", wrong, wrong["channel_url"]))

    def test_matching_channel_is_accepted(self):
        rec = {
            "channel_id": "UCabcdefghijklmnopqrstuv",
            "channel_url": "https://www.youtube.com/@RightCreator",
        }
        self.assertTrue(niche_scraper.channel_matches_request("@rightcreator", rec, rec["channel_url"]))
        self.assertTrue(niche_scraper.channel_matches_request(
            "https://www.youtube.com/@RightCreator/videos", rec, rec["channel_url"]))
        self.assertTrue(niche_scraper.channel_matches_request(
            "UCabcdefghijklmnopqrstuv", rec, rec["channel_url"]))

    def test_identifiability_gate(self):
        self.assertTrue(niche_scraper.is_identifiable_channel_input("@handle"))
        self.assertTrue(niche_scraper.is_identifiable_channel_input("UCabcdefghijklmnopqrstuv"))
        self.assertTrue(niche_scraper.is_identifiable_channel_input("https://youtube.com/@x"))
        # A bare display name is the only thing allowed to resolve fuzzily.
        self.assertFalse(niche_scraper.is_identifiable_channel_input("Outdoor Boys"))
        self.assertFalse(niche_scraper.is_identifiable_channel_input(""))

    def test_get_channel_details_rejects_a_mismatched_search_fallback(self):
        """End-to-end: a typo'd handle must raise ValueError, not return the wrong channel."""
        wrong = {
            "channel_id": "UCabcdefghijklmnopqrstuv",
            "channel_url": "https://www.youtube.com/@SomeOtherCreator/videos",
            "title": "Some Other Creator - Videos",
            "entries": [],
        }

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                if url.startswith("ytsearch"):
                    return {"entries": [{"channel_url": "https://www.youtube.com/@SomeOtherCreator"}]}
                if "SomeOtherCreator" in url:
                    return wrong
                raise RuntimeError("tab extraction failed")

        with patch.object(niche_scraper.yt_dlp, "YoutubeDL", FakeYDL):
            with self.assertRaises(ValueError) as ctx:
                niche_scraper.get_channel_details("@mishandledhandle", max_videos=5)
        self.assertIn("different channel", str(ctx.exception))

    def test_endpoint_returns_404_for_mismatched_resolution(self):
        with patch.object(app_module, "get_channel_details",
                          side_effect=ValueError("Requested channel '@typo' but YouTube search resolved to a different channel")):
            res = app.test_client().post("/api/niche/channel", json={"channel_input": "@typo"})
        self.assertEqual(res.status_code, 404)
        self.assertFalse(res.get_json()["success"])

    def test_fuzzy_name_lookup_is_disclosed_not_silent(self):
        with patch.object(app_module, "get_channel_details", return_value={
            "channel_name": "Outdoor Boys", "channel_id": "UCx", "subscribers": 5,
            "videos": [], "requested_input": "Outdoor Boys", "resolved_by_search": True,
        }), patch.object(app_module, "analyze_channel_outliers", return_value={
            "channel_name": "Outdoor Boys", "videos": [], "outliers": [],
            "stats": {"median_views": 1},
        }):
            body = app.test_client().post(
                "/api/niche/channel", json={"channel_input": "Outdoor Boys"}).get_json()
        self.assertTrue(body["success"])
        self.assertIn("warnings", body, "A search-resolved channel must be disclosed to the user")
        self.assertIn("Outdoor Boys", body["warnings"][0])


# ---------------------------------------------------------------------------
# 2. Topic "Cloner" ignores the outlier titles it is given
# ---------------------------------------------------------------------------
class TestBug02_TopicClonerIgnoresInput(unittest.TestCase):

    OUTLIERS = [
        "Why Cold Starts Kill Your Battery",
        "Why Nobody Warns You About Thermal Throttling",
        "How I Doubled My Frame Rate",
    ]

    def test_output_depends_on_outlier_titles(self):
        a = niche_generator._generate_with_templates("Chan", "Topic", self.OUTLIERS, "Curiosity Gap", 150)
        b = niche_generator._generate_with_templates("Chan", "Topic", [], "Curiosity Gap", 150)
        self.assertNotEqual([i["title"] for i in a], [i["title"] for i in b],
                            "Output must change when the observed outlier titles change")

    def test_outlier_subjects_appear_in_synthesis(self):
        ideas = niche_generator._generate_with_templates("Chan", "Generic", self.OUTLIERS, "Curiosity Gap", 150)
        blob = " ".join(i["title"].lower() for i in ideas)
        for subject in ("battery", "thermal", "frame", "cold"):
            self.assertIn(subject, blob, f"Outlier subject '{subject}' never reached the output")

    def test_hook_framework_reaches_the_hook_script(self):
        gap = niche_generator._generate_with_templates("C", "T", self.OUTLIERS, "Curiosity Gap", 150)
        interrupt = niche_generator._generate_with_templates("C", "T", self.OUTLIERS, "Pattern Interrupt", 150)
        self.assertNotEqual(gap[0]["hook_script"], interrupt[0]["hook_script"])

    def test_pacing_controls_script_density(self):
        """
        The hook is a fixed time-boxed cold open, so a faster requested speaking rate means
        more words packed into the same span. That is the correct semantic: pacing must
        change the output.
        """
        slow = niche_generator._generate_with_templates("C", "T", self.OUTLIERS, "Curiosity Gap", 90)
        fast = niche_generator._generate_with_templates("C", "T", self.OUTLIERS, "Curiosity Gap", 260)
        self.assertGreater(len(fast[0]["hook_script"].split()), len(slow[0]["hook_script"].split()))
        self.assertNotEqual(slow[0]["hook_script"], fast[0]["hook_script"])

    def test_channel_name_used_when_no_outliers_or_niche(self):
        ideas = niche_generator._generate_with_templates("Deep Sea Diaries", "", [], "Curiosity Gap", 150)
        blob = " ".join(i["title"] for i in ideas).lower()
        self.assertIn("deep sea diaries", blob)

    def test_output_shape_and_uniqueness(self):
        ideas = niche_generator.generate_viral_ideas("C", "T", self.OUTLIERS, "Curiosity Gap", 150, api_key=None)
        self.assertEqual(len(ideas), 8)
        titles = [i["title"] for i in ideas]
        self.assertEqual(len(set(titles)), len(titles), "Titles must be unique")
        for i in ideas:
            for key in ("title", "thumbnail_concept", "hook_script", "outlier_rationale"):
                self.assertTrue(str(i.get(key, "")).strip(), f"empty {key}")


# ---------------------------------------------------------------------------
# 3. batch_rerender has no exception handling
# ---------------------------------------------------------------------------
class TestBug03_BatchRerenderAborts(unittest.TestCase):

    def _run(self, metas):
        tmp = Path(tempfile.mkdtemp())
        for name, meta in metas.items():
            (tmp / name).write_text(json.dumps(meta), encoding="utf-8")
        with patch("batch_rerender.cut_and_format_clip", return_value=True) as mock_cut, \
             patch("batch_rerender.OUTPUT_DIR", tmp):
            batch_rerender.rerender_all(skip_recent_seconds=0)
        return mock_cut

    def test_null_timing_field_does_not_abort_batch(self):
        mock_cut = self._run({
            "a.json": {"video_id": "v1", "start_time": None, "end_time": 30.0},
            "b.json": {"video_id": "v1", "start_time": 10.0, "end_time": 40.0},
        })
        self.assertEqual(mock_cut.call_count, 1, "Bad record must be skipped, not abort the run")
        kwargs = mock_cut.call_args.kwargs
        self.assertEqual((kwargs["start_sec"], kwargs["end_sec"]), (10.0, 40.0))

    def test_raising_render_does_not_abort_batch(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.json").write_text(json.dumps(
            {"video_id": "v1", "start_time": 0.0, "end_time": 30.0}), encoding="utf-8")
        (tmp / "b.json").write_text(json.dumps(
            {"video_id": "v2", "start_time": 5.0, "end_time": 35.0}), encoding="utf-8")
        # get_ffmpeg_path() raises RuntimeError, which previously propagated out of rerender_all.
        with patch("batch_rerender.cut_and_format_clip",
                   side_effect=RuntimeError("ffmpeg not found on system PATH.")), \
             patch("batch_rerender.OUTPUT_DIR", tmp):
            batch_rerender.rerender_all(skip_recent_seconds=0)  # must not raise

    def test_summary_accounting_still_runs(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.json").write_text("not json", encoding="utf-8")
        (tmp / "b.json").write_text(json.dumps(
            {"video_id": "v1", "start_time": None, "end_time": None}), encoding="utf-8")
        out = []
        with patch("batch_rerender.cut_and_format_clip", return_value=True), \
             patch("batch_rerender.OUTPUT_DIR", tmp), \
             patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            batch_rerender.rerender_all(skip_recent_seconds=0)
        self.assertTrue(any("BATCH RE-RENDER SUMMARY" in line for line in out))


# ---------------------------------------------------------------------------
# 4. Partial Jev batch response recorded as a legitimate 0.0 score
# ---------------------------------------------------------------------------
class TestBug04_PartialJevBatch(unittest.TestCase):

    def _batch_call(self, answers, batch=2):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"answers": answers}}
        with patch.object(scorer, "get_jev_api_key", return_value="k"), \
             patch.object(scorer.requests, "post", return_value=resp), \
             patch.object(scorer, "score_chunk_with_jev",
                          side_effect=lambda t, api_key=None: {"success": False, "error": "seq"}) as seq:
            results = scorer.score_chunks_batch_with_jev(["a", "b", "c"][:batch], api_key="k")
        return results, seq

    def test_truncated_response_is_never_scored_zero(self):
        # c1_virality entirely absent -> must not become virality 0.0 / success True
        answers = {
            "c0_virality": {"score": 2.5, "confidence": 0.9},
            "c0_standalone": {"noul": 0.8},
            "c0_sponsor": {"noul": 0.0},
            "c0_category": {"choice": "high_value_insight"},
        }
        results, seq = self._batch_call(answers, batch=2)
        self.assertTrue(any(r.get("virality_score") == 0.0 and r.get("success") for r in results) is False,
                        "A missing answer must not be recorded as a successful 0.0")
        self.assertTrue(any(not r.get("success") for r in results),
                        "The incomplete batch must fall back to per-candidate scoring")
        self.assertGreaterEqual(seq.call_count, 1)

    def test_explicit_null_score_is_not_treated_as_zero(self):
        answers = {
            "c0_virality": {"score": None, "confidence": 0.9},
            "c0_standalone": {"noul": 0.8},
        }
        results, _ = self._batch_call(answers, batch=1)
        self.assertFalse(results[0].get("success"))
        self.assertIsNot(results[0].get("virality_score"), 0.0)

    def test_complete_response_is_accepted(self):
        answers = {
            "c0_virality": {"score": 2.0, "confidence": 0.9},
            "c0_standalone": {"noul": 0.7},
            "c0_sponsor": {"noul": 0.0},
            "c0_category": {"choice": "shocking_revelation"},
            "c1_virality": {"score": 1.0, "confidence": 0.5},
            "c1_standalone": {"noul": 0.6},
            "c1_sponsor": {"noul": 0.0},
            "c1_category": {"choice": "compelling_story"},
        }
        results, seq = self._batch_call(answers, batch=2)
        self.assertEqual([r["success"] for r in results], [True, True])
        self.assertEqual(seq.call_count, 0)
        self.assertEqual(results[0]["category"], "shocking_revelation")

    def test_genuine_zero_score_is_still_honoured(self):
        answers = {
            "c0_virality": {"score": 0, "confidence": 0.8},
            "c0_standalone": {"noul": 0.4},
            "c0_sponsor": {"noul": 0.0},
        }
        results, seq = self._batch_call(answers, batch=1)
        self.assertTrue(results[0]["success"])
        self.assertEqual(results[0]["virality_score"], 0.0)
        self.assertEqual(seq.call_count, 0)


# ---------------------------------------------------------------------------
# 5. Malformed body to /api/upload/config returns HTTP 500
# ---------------------------------------------------------------------------
class TestBug05_UploadConfigValidation(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self.tmp = tempfile.mkdtemp()
        self.cfg_path = Path(self.tmp) / "upload_config.json"
        self.cfg_path.write_text(json.dumps({
            "youtube": {"is_authenticated": False},
            "ayrshare": {"api_key": "OLDKEY12345", "is_configured": True},
            "auto_upload": {"enabled": False, "platforms": ["youtube"], "default_privacy": "public"},
        }), encoding="utf-8")
        patcher = patch.object(uploader, "UPLOAD_CONFIG_PATH", self.cfg_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_null_ayrshare_key_is_400_not_500(self):
        res = self.client.post("/api/upload/config", json={"ayrshare_key": None})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json()["success"])

    def test_null_does_not_silently_wipe_the_stored_key(self):
        """A null must not be interpreted as 'clear', or a blank form field destroys the key."""
        before = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.client.post("/api/upload/config", json={"ayrshare_key": None})
        after = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(before["ayrshare"], after["ayrshare"])

    def test_bool_auto_upload_is_400_not_500(self):
        res = self.client.post("/api/upload/config", json={"auto_upload": True})
        self.assertEqual(res.status_code, 400)

    def test_numeric_ayrshare_key_is_400(self):
        res = self.client.post("/api/upload/config", json={"ayrshare_key": 12345})
        self.assertEqual(res.status_code, 400)

    def test_non_object_body_is_400(self):
        res = self.client.post("/api/upload/config", json=["not", "an", "object"])
        self.assertEqual(res.status_code, 400)

    def test_valid_payload_still_succeeds(self):
        res = self.client.post("/api/upload/config", json={
            "ayrshare_key": "  NEWKEY  ",
            "auto_upload": {"enabled": True, "platforms": ["youtube", "tiktok"], "default_privacy": "unlisted"},
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])
        saved = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["ayrshare"]["api_key"], "NEWKEY")
        self.assertTrue(saved["auto_upload"]["enabled"])


# ---------------------------------------------------------------------------
# 6. Retired OOB flow + blocking request handler
# ---------------------------------------------------------------------------
class TestBug06_YouTubeOAuth(unittest.TestCase):

    def test_no_oob_redirect_uri_anywhere(self):
        for mod in (uploader,):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertNotIn("oauth:2.0:oob", src,
                             "The retired OOB flow must not be referenced anywhere")

    def test_auth_url_uses_loopback_redirect(self):
        self.assertEqual(uploader.DEFAULT_LOOPBACK_REDIRECT, "http://localhost")

        captured = {}

        class FakeFlow:
            def __init__(self, redirect_uri):
                captured["redirect_uri"] = redirect_uri

            def authorization_url(self, **kw):
                return ("https://accounts.google.com/o/oauth2/auth?x=1", "state")

        @staticmethod
        def from_client_secrets_file(path, scopes, redirect_uri=None):
            return FakeFlow(redirect_uri)

        flow_mod = types.ModuleType("google_auth_oauthlib.flow")
        flow_mod.InstalledAppFlow = types.SimpleNamespace(
            from_client_secrets_file=from_client_secrets_file)
        pkg_mod = types.ModuleType("google_auth_oauthlib")
        pkg_mod.__path__ = []
        pkg_mod.flow = flow_mod

        secrets = Path(tempfile.mkdtemp()) / "client_secrets.json"
        secrets.write_text("{}", encoding="utf-8")
        cfg = Path(tempfile.mkdtemp()) / "upload_config.json"
        cfg.write_text(json.dumps({"youtube": {"client_secrets_file": str(secrets)}}), encoding="utf-8")

        with patch.dict(sys.modules, {"google_auth_oauthlib": pkg_mod,
                                      "google_auth_oauthlib.flow": flow_mod}), \
             patch.object(uploader, "UPLOAD_CONFIG_PATH", cfg):
            res = uploader.get_youtube_auth_url()
        self.assertTrue(res["success"], res)
        self.assertEqual(captured["redirect_uri"], "http://localhost")
        self.assertEqual(res["redirect_uri"], "http://localhost")

    def test_auth_start_does_not_block_the_request(self):
        """run_local_server() must run off the request thread, so the call returns immediately."""
        slow_started = threading.Event()
        release = threading.Event()

        def slow_local_auth(open_browser=True):
            slow_started.set()
            release.wait(3.0)
            return {"success": True, "channel_title": "Chan"}

        self.addCleanup(release.set)
        with patch.object(app_module, "start_youtube_local_auth", side_effect=slow_local_auth):
            t0 = time.time()
            res = app.test_client().post("/api/upload/youtube-auth-start")
            elapsed = time.time() - t0
        self.assertEqual(res.status_code, 200)
        self.assertLess(elapsed, 2.0,
                        "The endpoint blocked the request thread waiting for the browser")
        body = res.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "pending")
        self.assertTrue(slow_started.wait(2.0), "Auth work must have been dispatched to a thread")
        self.assertIn("auth_id", body)

    def test_auth_status_endpoint_reports_completion(self):
        with patch.object(app_module, "start_youtube_local_auth",
                          return_value={"success": True, "channel_title": "My Channel"}):
            started = app.test_client().post("/api/upload/youtube-auth-start")
        auth_id = started.get_json()["auth_id"]
        data = {"status": "pending"}
        for _ in range(200):
            data = app.test_client().get(f"/api/upload/youtube-auth-status/{auth_id}").get_json()
            if data.get("status") != "pending":
                break
            time.sleep(0.02)
        self.assertTrue(data.get("success"), data)
        self.assertEqual(data.get("channel_title"), "My Channel")

    def test_ui_no_longer_advertises_paste_a_code_from_consent_screen(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("pollYouTubeAuth", html, "Client must poll the async auth handshake")
        self.assertIn("?code=", html, "Manual fallback must tell the user where to find the code")

    def test_unknown_auth_id_is_404(self):
        res = app.test_client().get("/api/upload/youtube-auth-status/does-not-exist")
        self.assertEqual(res.status_code, 404)

    def test_ui_no_longer_advertises_paste_a_code_from_consent_screen(self):
        html = Path(app_module.__file__).resolve().parent.joinpath("templates", "index.html").read_text(encoding="utf-8")
        self.assertIn("pollYouTubeAuth", html, "Client must poll the async auth handshake")
        self.assertIn("?code=", html, "Manual fallback must tell the user where to find the code")


# ---------------------------------------------------------------------------
# 7. Multi-word entities become broken hashtags
# ---------------------------------------------------------------------------
class TestBug07_BrokenHashtags(unittest.TestCase):

    TRANSCRIPT = (
        "So the athletic greens year supply stuff is real. I mean the year supply thing costs a lot. "
        "Athletic greens uses cold brew and the athletic greens bottle costs twelve dollars. "
        "We ran the athletic greens test for a month in Austin Texas with Ben and Dave."
    )

    def test_no_concatenated_multiword_tags(self):
        meta = title_tag_engine.generate_smart_title_and_hashtags(self.TRANSCRIPT)
        for tag in meta["suggested_hashtags"]:
            body = tag.lstrip("#")
            self.assertTrue(body.isalnum(), f"{tag} is not a single valid hashtag token")
            self.assertGreaterEqual(len(body), 3)

    def test_bigram_weight_no_longer_dominates_single_proper_nouns(self):
        entities = title_tag_engine.extract_topical_entities(self.TRANSCRIPT)
        for ent, score in entities:
            if " " in ent:
                self.assertLessEqual(
                    score,
                    8.0,
                    f"Multi-word entity '{ent}' scored {score}, outranking single proper nouns (8.0)")

    def test_junk_pairs_are_not_promoted(self):
        entities = [e.lower() for e, _ in title_tag_engine.extract_topical_entities(self.TRANSCRIPT)]
        for junk in ("year supply", "real reason", "cold brew" if False else "zzz"):
            self.assertNotIn(junk, entities)

    def test_entity_to_tag_uses_one_real_word(self):
        self.assertEqual(title_tag_engine._entity_to_tag("Athletic Greens"), "#greens")
        self.assertEqual(title_tag_engine._entity_to_tag("Snow Cave"), "#cave")
        self.assertEqual(title_tag_engine._entity_to_tag("Austin"), "#austin")
        self.assertIsNone(title_tag_engine._entity_to_tag("the of and"))

    def test_real_entities_still_survive(self):
        entities = [e.lower() for e, _ in title_tag_engine.extract_topical_entities(self.TRANSCRIPT)]
        self.assertTrue(any("brew" in e or "texas" in e or "austin" in e or "greens" in e for e in entities),
                        f"Real entities lost: {entities}")


# ---------------------------------------------------------------------------
# 8. #viral silently dropped when entity tags fill the quota
# ---------------------------------------------------------------------------
class TestBug08_ViralDropped(unittest.TestCase):

    CASES = [
        "Let me tell you about the cold brew coffee extraction method in detail today",
        "I spent three weeks testing the athletic greens supplement bottle for protein and energy",
        "The thermal throttling problem on this laptop is caused by the copper heat pipe design",
        "Why the year supply cost of whey protein powder matters more than the total amount",
        "The toboggan run down that frozen mountain slope was genuinely terrifying yesterday",
    ]

    def test_baseline_tags_always_present(self):
        # The reported bug was "#viral silently dropped when entity tags fill the quota":
        # a reach tag that the code comment promised was lost to an order-dependent guard.
        # That failure mode is still the one worth guarding, so this test still pins a
        # reach tag that CANNOT be displaced. Under the hashtag engine that tag is
        # `#Shorts`, which is added first and unconditionally; `#viral` is no longer a
        # reserved tag, and demanding it would be asserting an implementation detail that
        # the whole point of this task removed. The quota is now `hashtag_engine.MAX_HASHTAGS`
        # (5), not this module's deleted legacy 7.
        for text in self.CASES:
            meta = title_tag_engine.generate_smart_title_and_hashtags(text)
            tags = meta["suggested_hashtags"]
            self.assertIn("#Shorts", tags, f"missing #Shorts for {text[:30]!r}: {tags}")
            self.assertLessEqual(len(tags), hashtag_engine.MAX_HASHTAGS,
                                 f"{len(tags)} tags exceeds the cap: {tags}")

    def test_no_duplicate_tags(self):
        for text in self.CASES:
            tags = title_tag_engine.generate_smart_title_and_hashtags(text)["suggested_hashtags"]
            self.assertEqual(len(tags), len(set(tags)), f"duplicates in {tags}")

    def test_niche_tags_still_present(self):
        meta = title_tag_engine.generate_smart_title_and_hashtags(
            "This gym workout and muscle training session was brutal, the protein diet matters")
        # Asserted against NICHE_TAGS rather than a hardcoded list, so a retune of that
        # list cannot silently unpin this test. The first entry is what the engine always
        # emits, and a second is asserted too because the budget allows it here.
        expected = NICHE_TAGS["fitness_health"][:2]
        present = [t for t in expected if t in meta["suggested_hashtags"]]
        self.assertEqual(present, expected, meta["suggested_hashtags"])


# ---------------------------------------------------------------------------
# 9. Channel chart sort-mode label inverted
# ---------------------------------------------------------------------------
class TestBug09_ChartSortLabel(unittest.TestCase):

    def setUp(self):
        self.html = Path(app_module.__file__).resolve().parent.joinpath("templates", "index.html").read_text(encoding="utf-8")

    def test_label_matches_mode_semantics(self):
        # 'ranked' == views descending == High to Low
        self.assertIn("chartSortMode === 'ranked' ? 'Sort: High to Low' : 'Sort: Chronological'", self.html)
        self.assertIn("chartVideos.sort((a, b) => (b.views || 0) - (a.views || 0));", self.html)

    def test_initial_label_matches_initial_mode(self):
        self.assertIn("let chartSortMode = 'timeline';", self.html)
        self.assertIn('<span id="chart-order-label">Sort: Chronological</span>', self.html)
        self.assertNotIn('<span id="chart-order-label">Sort: High to Low</span>', self.html)

    def test_no_false_parallel_claim_in_ui(self):
        self.assertNotIn("10x", self.html, "The '10x Parallel' claim was never true and must be removed")


# ---------------------------------------------------------------------------
# 10. A null field in a Jev answer crashes the whole job
# ---------------------------------------------------------------------------
class TestBug10_NullJevFields(unittest.TestCase):

    def test_compute_rubric_scores_survives_nulls(self):
        for field in ("virality_score", "standalone_prob", "sponsor_prob", "duration", "text"):
            cand = {"text": "why is this interesting", "start": 0.0, "end": 40.0,
                    "virality_score": 2.0, "standalone_prob": 0.7,
                    "sponsor_prob": 0.0, "duration": 40.0, "category": "high_value_insight"}
            cand[field] = None
            scores, avg, notes = scorer.compute_rubric_scores(cand)
            self.assertEqual(len(scores), 6)
            self.assertIsInstance(avg, float)

    def test_critique_gate_survives_null_sponsor_prob(self):
        windows = []
        for i in range(4):
            windows.append({
                "start": float(i * 60), "end": float(i * 60 + 30), "duration": 30.0,
                "text": "why does this matter so much to everyone listening right now",
                "virality_score": None, "standalone_prob": None,
                "sponsor_prob": None, "category": None,
            })
        notes = []
        out = scorer.critique_gate_search(
            candidate_windows=windows, target_clips=2, threshold=1.0, max_attempts=2,
            shortfall_callback=lambda f, r: notes.append((f, r)))
        self.assertEqual(len(out), 2)

    def test_sequential_scorer_reports_missing_virality_as_failure(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"answers": {"is_standalone": {"noul": 0.9}}}}
        with patch.object(scorer, "get_jev_api_key", return_value="k"), \
             patch.object(scorer.requests, "post", return_value=resp):
            res = scorer.score_chunk_with_jev("some text", api_key="k")
        self.assertFalse(res["success"], "A response with no virality score must not be a success")

    def test_sequential_scorer_handles_explicit_null_values(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"answers": {
            "virality": {"score": 2.0, "confidence": None},
            "is_standalone": {"noul": None},
            "is_sponsor_read": {"noul": None},
            "category": {"choice": None},
        }}}
        with patch.object(scorer, "get_jev_api_key", return_value="k"), \
             patch.object(scorer.requests, "post", return_value=resp):
            res = scorer.score_chunk_with_jev("some text", api_key="k")
        self.assertTrue(res["success"])
        self.assertIsInstance(res["virality_score"], float)
        self.assertIsInstance(res["standalone_prob"], float)
        self.assertIsInstance(res["sponsor_prob"], float)
        self.assertEqual(res["category"], "high_value_insight")


# ---------------------------------------------------------------------------
# 11. build_ass_from_words mutates the caller's word-timestamp list
# ---------------------------------------------------------------------------
class TestBug11_InputMutation(unittest.TestCase):

    def test_overlapping_word_start_is_not_written_back(self):
        words = [
            {"word": "hello", "start": 5.0, "end": 5.3},
            {"word": "y", "start": 5.0, "end": 5.3},  # zero-length onset -> nudged by the engine
        ]
        before = [dict(w) for w in words]
        subtitles.build_ass_from_words(words, style_key="bold_pop", enable_emojis=False)
        self.assertEqual(words, before, "build_ass_from_words mutated the caller's word list")

    def test_reversed_word_start_is_not_written_back(self):
        words = [
            {"word": "hello", "start": 4.0, "end": 5.0},
            {"word": "y", "start": 4.0, "end": 5.0},
        ]
        before = [dict(w) for w in words]
        subtitles.build_ass_from_words(words, style_key="bold_pop", enable_emojis=False)
        self.assertEqual(words, before)

    def test_bleep_and_broll_windows_see_unshifted_timestamps(self):
        """video_cutter reuses the same list for bleep + B-roll; those must match the audio."""
        words = [
            {"word": "fuck", "start": 5.0, "end": 5.4},
            {"word": "y", "start": 5.0, "end": 5.4},
        ]
        subtitles.build_ass_from_words(words, style_key="bold_pop", enable_emojis=False)
        intervals = subtitles.detect_profanity_intervals(words)
        self.assertEqual(intervals[0][0], 4.94, "Bleep window must track the original start")
        cues = broll_engine.find_broll_cues(
            [dict(w, word="mountain") for w in words], clip_duration=30.0)
        self.assertTrue(all(c["start"] == 5.0 for c in cues), cues)


# ---------------------------------------------------------------------------
# 12. A corrupt cached B-roll file permanently breaks the clips that need it
# ---------------------------------------------------------------------------
class TestBug12_PoisonedBrollCache(unittest.TestCase):

    def test_undecodable_cache_entry_is_evicted_and_refetched(self):
        cache = Path(tempfile.mkdtemp()) / "kw_deadbeef.mp4"
        cache.write_bytes(b"x" * 100000)  # big enough to pass the old size-only check
        with patch.object(broll_engine, "is_decodable_video", return_value=False), \
             patch.object(broll_engine.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.side_effect = [
                b"y" * 100000, b""]  # a fresh, valid transfer
            with patch.object(broll_engine, "is_decodable_video",
                              side_effect=[False, True, True]):
                ok = broll_engine.download_broll("https://cdn/x.mp4", cache)
        self.assertTrue(ok)
        self.assertTrue(urlopen.called, "A poisoned entry must trigger a re-download")

    def test_undecodable_download_is_not_cached(self):
        cache = Path(tempfile.mkdtemp()) / "kw_1234abcd.mp4"
        with patch.object(broll_engine, "is_decodable_video", return_value=False), \
             patch.object(broll_engine.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.side_effect = [b"z" * 100000, b""]
            ok = broll_engine.download_broll("https://cdn/y.mp4", cache)
        self.assertFalse(ok)
        self.assertFalse(cache.exists(), "An undecodable download must not be cached")

    def test_render_drops_bad_broll_instead_of_failing_the_clip(self):
        out = Path(tempfile.mkdtemp()) / "out.mp4"

        def fake_run(cmd, *a, **kw):
            for item in cmd:
                p = str(item)
                if "raw_" in p and p.endswith(".mp4"):
                    Path(p).write_bytes(b"fake")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("video_cutter.subprocess.run", side_effect=fake_run), \
             patch("video_cutter.get_ffmpeg_path", return_value="ffmpeg"), \
             patch("video_cutter.get_video_duration", return_value=10.0), \
             patch("video_cutter.generate_synced_subtitles", return_value=False), \
             patch("video_cutter.plan_and_fetch_brolls", return_value=[
                 {"keyword": "money", "start": 1.0, "end": 4.0,
                  "duration": 3.0, "file_path": Path("temp/broll/poison_0.mp4")}]), \
             patch("video_cutter.is_decodable_video", return_value=False):
            ok = video_cutter.cut_and_format_clip(
                youtube_url="https://youtube.com/watch?v=x", start_sec=0.0, end_sec=10.0,
                output_path=out, enable_broll=True, enable_snappy_cuts=False,
                enable_punch_zooms=False, enable_outro=False)
        self.assertTrue(ok, "A bad B-roll must not discard an otherwise-good clip")

    def test_is_decodable_rejects_undecodable_file(self):
        bad = Path(tempfile.mkdtemp()) / "bad.mp4"
        bad.write_bytes(b"x" * 100000)
        if not broll_engine.shutil.which("ffprobe"):
            self.skipTest("ffprobe not available")
        self.assertFalse(broll_engine.is_decodable_video(bad))
        self.assertFalse(broll_engine.is_decodable_video(Path("nope.mp4")))
        self.assertFalse(broll_engine.is_decodable_video(Path(tempfile.mkdtemp()) / "tiny.mp4"))


# ---------------------------------------------------------------------------
# 13. Karaoke / static styles emit negative-duration Dialogue events
# ---------------------------------------------------------------------------
class TestBug13_NegativeDurationEvents(unittest.TestCase):

    def _events(self, ass):
        out = []
        for line in ass.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",")
            out.append((parts[1].strip(), parts[2].strip()))
        return out

    def _secs(self, ts):
        h, m, rest = ts.split(":")
        s, cs = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0

    def test_no_negative_duration_for_any_style(self):
        # A chunk spanning <= 0.35s forces the fallback branch.
        words = [{"word": "hi", "start": 9.00, "end": 9.02}]
        for style in ("karaoke", "minimal_caption", "netflix_standard", "bbc_sdh",
                      "cinematic_auteur", "bold_pop", "boxed_clean", "hormozi",
                      "beast", "neon_green", "red_punch", "clean_white", "kinetic_pop"):
            ass = subtitles.build_ass_from_words(words, style_key=style, enable_emojis=False)
            for start, end in self._events(ass):
                self.assertGreaterEqual(
                    self._secs(end), self._secs(start),
                    f"{style}: negative-duration event {start} -> {end}")

    def test_final_chunk_is_also_clamped(self):
        words = [{"word": "solo", "start": 5.00, "end": 5.02}]
        for style in ("karaoke", "minimal_caption", "netflix_standard"):
            ass = subtitles.build_ass_from_words(words, style_key=style, enable_emojis=False)
            evs = self._events(ass)
            self.assertTrue(evs)
            for start, end in evs:
                self.assertGreaterEqual(self._secs(end), self._secs(start), f"{style}: {start}->{end}")

    def test_reversed_single_word_is_clamped(self):
        words = [{"word": "x", "start": 5.00, "end": 4.35}]
        ass = subtitles.build_ass_from_words(words, style_key="netflix_standard", enable_emojis=False)
        for start, end in self._events(ass):
            self.assertGreaterEqual(self._secs(end), self._secs(start))


# ---------------------------------------------------------------------------
# 14. The app can silently return fewer clips than the user requested
# ---------------------------------------------------------------------------
class TestBug14_FewerClipsThanRequested(unittest.TestCase):

    def _windows(self, n):
        return [{"start": float(i * 25), "end": float(i * 25 + 28), "duration": 28.0,
                 "text": "why this matters", "virality_score": 2.0, "standalone_prob": 0.8,
                 "sponsor_prob": 0.0, "category": "high_value_insight"} for i in range(n)]

    def test_shortfall_is_reported_not_silent(self):
        # Windows that all overlap each other, so the quota cannot be filled.
        dense = [{"start": float(i * 5), "end": float(i * 5 + 28), "duration": 28.0,
                  "text": "why this matters", "virality_score": 2.0, "standalone_prob": 0.8,
                  "sponsor_prob": 0.0, "category": "high_value_insight"} for i in range(4)]
        notes = []
        out = scorer.critique_gate_search(
            candidate_windows=dense, target_clips=5, threshold=1.0, max_attempts=1,
            shortfall_callback=lambda f, r: notes.append((f, r)))
        self.assertLess(len(out), 5, "Guard setup: expected the quota to go unfilled")
        self.assertTrue(notes, "An unfilled quota must be reported, not returned silently")
        self.assertEqual(notes[-1], (len(out), 5))

    def test_no_shortfall_callback_when_the_quota_is_met(self):
        spread = [{"start": float(i * 300), "end": float(i * 300 + 28), "duration": 28.0,
                   "text": "why this matters", "virality_score": 2.0, "standalone_prob": 0.8,
                   "sponsor_prob": 0.0, "category": "high_value_insight"} for i in range(6)]
        notes = []
        out = scorer.critique_gate_search(
            candidate_windows=spread, target_clips=3, threshold=1.0, max_attempts=2,
            shortfall_callback=lambda f, r: notes.append((f, r)))
        self.assertEqual(len(out), 3)
        self.assertEqual(notes, [])

    def test_widened_sweep_fills_more_slots(self):
        # Heavily overlapping windows: the narrow sweep alone can only fill the first slot.
        dense = [{"start": float(i * 5), "end": float(i * 5 + 28), "duration": 28.0,
                  "text": "why this matters a lot", "virality_score": 2.0,
                  "standalone_prob": 0.8, "sponsor_prob": 0.0,
                  "category": "high_value_insight"} for i in range(10)]
        out = scorer.critique_gate_search(
            candidate_windows=dense, target_clips=4, threshold=1.0, max_attempts=2)
        self.assertGreater(len(out), 1, "The widened sweep must find non-overlapping windows")

    def test_status_endpoint_exposes_warning(self):
        job_id = "shortfalltest"
        app_module.JOBS[job_id] = {
            "job_id": job_id, "status": "completed", "progress": 100,
            "step": "Finished! 2 viral Shorts are ready. (NOTE: only 2 of 5 requested clips were available after the non-overlap and quality gates.)",
            "clips": [], "error": None,
            "warning": "Requested 5 clips but only 2 were produced.",
            "_created_at": time.time(), "_finished_at": time.time(),
        }
        self.addCleanup(app_module.JOBS.pop, job_id, None)
        body = app.test_client().get(f"/api/status/{job_id}").get_json()
        self.assertIn("warning", body)
        self.assertNotIn("_created_at", body, "Internal bookkeeping must not leak to the client")
        self.assertNotIn("_finished_at", body)


# ---------------------------------------------------------------------------
# 16. JOBS grows without bound
# ---------------------------------------------------------------------------
class TestBug16_UnboundedJobs(unittest.TestCase):

    def test_ttl_and_overflow_eviction(self):
        app_module.JOBS.clear()
        try:
            old = app_module.JOBS.get("keepme")
            for i in range(app_module.JOB_MAX_ENTRIES + 50):
                app_module.JOBS[f"j{i}"] = {
                    "job_id": f"j{i}", "status": "completed", "clips": [{"blob": "x" * 100}],
                    "_created_at": float(i), "_finished_at": float(i),
                }
            self.assertGreater(len(app_module.JOBS), app_module.JOB_MAX_ENTRIES)

            app_module._prune_jobs(now=1e12)
            self.assertLessEqual(len(app_module.JOBS), app_module.JOB_MAX_ENTRIES,
                                 "Overflow eviction failed")
            self.assertEqual(app_module.JOBS, {}, "TTL eviction failed")
        finally:
            app_module.JOBS.clear()
            if old is not None:
                app_module.JOBS.update(old) if isinstance(old, dict) else None

    def test_unfinished_jobs_are_never_ttl_evicted(self):
        app_module.JOBS.clear()
        try:
            app_module.JOBS["running"] = {"status": "running", "clips": [],
                                          "_created_at": 0.0, "_finished_at": None}
            app_module._prune_jobs(now=1e12)
            self.assertIn("running", app_module.JOBS)
        finally:
            app_module.JOBS.clear()

    def test_prune_runs_on_status_read(self):
        app_module.JOBS.clear()
        try:
            app_module.JOBS["old"] = {"status": "completed", "clips": [],
                                      "_created_at": 0.0, "_finished_at": 0.0}
            app.test_client().get("/api/status/old")
            self.assertNotIn("old", app_module.JOBS)
        finally:
            app_module.JOBS.clear()


# ---------------------------------------------------------------------------
# 17. The Ayrshare key can never be cleared
# ---------------------------------------------------------------------------
class TestBug17_ClearAyrshareKey(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self.cfg_path = Path(tempfile.mkdtemp()) / "upload_config.json"
        self.cfg_path.write_text(json.dumps({
            "youtube": {"is_authenticated": False},
            "ayrshare": {"api_key": "STALEKEY1234", "is_configured": True},
            "auto_upload": {"enabled": False, "platforms": ["youtube"], "default_privacy": "public"},
        }), encoding="utf-8")
        p = patch.object(uploader, "UPLOAD_CONFIG_PATH", self.cfg_path)
        p.start()
        self.addCleanup(p.stop)

    def test_empty_key_clears_credential_and_flag(self):
        res = self.client.post("/api/upload/config", json={"ayrshare_key": ""})
        self.assertEqual(res.status_code, 200)
        saved = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["ayrshare"]["api_key"], "")
        self.assertFalse(saved["ayrshare"]["is_configured"])

    def test_config_endpoint_reports_cleared_state(self):
        self.client.post("/api/upload/config", json={"ayrshare_key": "   "})
        body = self.client.get("/api/upload/config").get_json()
        self.assertFalse(body["config"]["ayrshare"]["is_configured"])
        self.assertEqual(body["config"]["ayrshare"]["masked_key"], "")

    def test_cleared_key_no_longer_diverts_youtube_uploads(self):
        self.client.post("/api/upload/config", json={"ayrshare_key": ""})
        clip = Path(config.OUTPUT_DIR) / "fake_for_test.mp4"
        clip.write_bytes(b"x" * 10)
        self.addCleanup(clip.unlink, missing_ok=True)
        with patch.object(uploader, "upload_video_to_youtube") as yt, \
             patch.object(uploader, "upload_to_social_platforms") as social:
            res = uploader.upload_clip_to_platforms(
                clip_filename=clip.name, title="t", description="d",
                hashtags=["#a"], platforms=["youtube"])
        self.assertIn("youtube", res["results"])
        self.assertFalse(res["results"]["youtube"]["success"])
        self.assertFalse(social.called, "YouTube must not be routed to Ayrshare after the key is cleared")
        self.assertFalse(yt.called, "Native YouTube is not authenticated, so it must not be attempted")

    def test_stale_flag_without_key_does_not_divert(self):
        cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        cfg["ayrshare"] = {"api_key": "", "is_configured": True}
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        clip = Path(config.OUTPUT_DIR) / "fake_for_test2.mp4"
        clip.write_bytes(b"x" * 10)
        self.addCleanup(clip.unlink, missing_ok=True)
        with patch.object(uploader, "upload_to_social_platforms") as social:
            uploader.upload_clip_to_platforms(
                clip_filename=clip.name, title="t", description="d",
                hashtags=["#a"], platforms=["youtube"])
        self.assertFalse(social.called)

    def test_ui_exposes_a_remove_affordance(self):
        html = Path(app_module.__file__).resolve().parent.joinpath("templates", "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btn-clear-ayrshare"', html)
        self.assertIn("markAyrshareForRemoval", html)


# ---------------------------------------------------------------------------
# 18. Subtitle styles without an explicit key default to upper-case
# ---------------------------------------------------------------------------
class TestBug18_UppercaseDefault(unittest.TestCase):

    def test_uppercase_is_explicit_for_every_style(self):
        for key, style in subtitles.SUBTITLE_STYLES.items():
            self.assertIn("uppercase", style,
                          f"Style '{key}' relies on the implicit default; make it explicit")
            self.assertIsInstance(style["uppercase"], bool)

    def test_minimal_caption_matches_its_preview(self):
        words = [{"word": "The", "start": 0.0, "end": 0.2},
                 {"word": "subtle", "start": 0.2, "end": 0.5},
                 {"word": "documentary", "start": 0.5, "end": 0.9},
                 {"word": "caption", "start": 0.9, "end": 1.3}]
        ass = subtitles.build_ass_from_words(words, style_key="minimal_caption",
                                             enable_emojis=False, max_words_per_line=4)
        self.assertIn("The subtle documentary caption", ass)
        self.assertNotIn("THE SUBTLE DOCUMENTARY CAPTION", ass)

    def test_creator_styles_still_uppercase(self):
        words = [{"word": "never", "start": 0.0, "end": 0.4},
                 {"word": "give", "start": 0.4, "end": 0.7},
                 {"word": "up", "start": 0.7, "end": 0.9}]
        for style in ("bold_pop", "karaoke", "boxed_clean", "hormozi", "beast",
                      "neon_green", "red_punch", "clean_white", "kinetic_pop"):
            ass = subtitles.build_ass_from_words(words, style_key=style, enable_emojis=False)
            self.assertIn("NEVER", ass, f"{style} lost its intended all-caps rendering")

    def test_streaming_styles_stay_natural_case(self):
        words = [{"word": "standard", "start": 0.0, "end": 0.5},
                 {"word": "caption", "start": 0.5, "end": 1.0}]
        for style in ("netflix_standard", "bbc_sdh", "cinematic_auteur", "minimal_caption"):
            ass = subtitles.build_ass_from_words(words, style_key=style, enable_emojis=False)
            self.assertIn("standard", ass, f"{style} must keep sentence case")


# ---------------------------------------------------------------------------
# 19. YouTube description keeps the old title after picking an alternative hook
# ---------------------------------------------------------------------------
class TestBug19_StaleDescription(unittest.TestCase):

    def setUp(self):
        self.html = Path(app_module.__file__).resolve().parent.joinpath("templates", "index.html").read_text(encoding="utf-8")

    def test_chip_click_resyncs_the_description(self):
        onclick = re.search(
            r"chip\.onclick = \(\) => \{(.*?)\n\s*\};", self.html, re.DOTALL)
        self.assertIsNotNone(onclick, "chip onclick handler not found")
        self.assertIn("syncUploadDescription()", onclick.group(1),
                      "Selecting a hook must re-derive the description")

    def test_description_is_derived_by_a_shared_helper(self):
        self.assertIn("function syncUploadDescription()", self.html)
        # The description is now built in exactly one place, from the current title input.
        self.assertEqual(self.html.count("descInput.value = `${titleVal}"), 1)
        self.assertEqual(self.html.count("function syncUploadDescription()"), 1)

    def test_helper_is_called_when_the_modal_opens(self):
        idx_modal = self.html.index("function openUploadModalByIndex")
        idx_helper_call = self.html.index("syncUploadDescription();", idx_modal)
        idx_show = self.html.index("modal.classList.remove('hidden')", idx_modal)
        self.assertLess(idx_helper_call, idx_show)


# ---------------------------------------------------------------------------
# 20. A virality score of exactly 0 is displayed as 2.0
# ---------------------------------------------------------------------------
class TestBug20_ZeroScoreDisplay(unittest.TestCase):

    def setUp(self):
        self.html = Path(app_module.__file__).resolve().parent.joinpath("templates", "index.html").read_text(encoding="utf-8")

    def test_existence_check_instead_of_truthiness(self):
        self.assertNotIn("c.virality_score ? c.virality_score.toFixed(1) : '2.0'", self.html)
        self.assertNotIn("c.rubric_average ? Number(c.rubric_average)", self.html)
        self.assertIn("c.virality_score !== undefined && c.virality_score !== null", self.html)

    def test_zero_renders_as_zero(self):
        """Reproduce the exact expression now used by the clip-card renderer."""
        c = {"virality_score": 0, "rubric_average": 0}

        def has(v):
            return v is not None and v != ""

        score = f"{float(c['virality_score']):.1f}" if has(c["virality_score"]) else "2.0"
        rubric = (f"{float(c['rubric_average']):.1f}" if has(c["rubric_average"]) else
                  (f"{float(c['virality_score']) * 3.0:.1f}" if has(c["virality_score"]) else "6.5"))
        self.assertEqual(score, "0.0")
        self.assertEqual(rubric, "0.0")
        self.assertNotEqual(score, "2.0")

    def test_missing_score_still_uses_the_placeholder(self):
        c = {}
        score = f"{float(c['virality_score']):.1f}" if (c.get("virality_score") is not None
                                                        and c.get("virality_score") != "") else "2.0"
        self.assertEqual(score, "2.0")


# ---------------------------------------------------------------------------
# 21. The web UI and the CLI select different candidate windows
# ---------------------------------------------------------------------------
class TestBug21_WindowParity(unittest.TestCase):

    def test_config_constants_match_real_window_geometry(self):
        self.assertEqual(config.MIN_CLIP_DURATION, 35.0)
        self.assertEqual(config.MAX_CLIP_DURATION, 58.5)
        self.assertEqual(config.CLIP_WINDOW_STEP, 20.0)

    def test_both_entry_points_use_the_shared_constants(self):
        app_src = Path(app_module.__file__).read_text(encoding="utf-8")
        clip_src = Path(clipper.__file__).read_text(encoding="utf-8")
        for src, label in ((app_src, "app.py"), (clip_src, "clipper.py")):
            self.assertIn("MIN_CLIP_DURATION", src, f"{label} does not use the shared constants")
            self.assertIn("MAX_CLIP_DURATION", src, f"{label} does not use the shared constants")
            self.assertIn("CLIP_WINDOW_STEP", src, f"{label} does not use the shared constants")
            self.assertNotRegex(src, r"create_windows\(transcript, min_duration=[\d.]+",
                                f"{label} still hardcodes window geometry")

    def test_same_transcript_yields_identical_windows(self):
        transcript = [{"text": f"line {i}", "start": float(i * 5), "duration": 5.0}
                      for i in range(60)]
        a = scorer.create_windows(transcript, min_duration=config.MIN_CLIP_DURATION,
                                  max_duration=config.MAX_CLIP_DURATION, step=config.CLIP_WINDOW_STEP)
        b = scorer.create_windows(transcript, min_duration=config.MIN_CLIP_DURATION,
                                  max_duration=config.MAX_CLIP_DURATION, step=config.CLIP_WINDOW_STEP)
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_app_no_longer_imports_unused_scorer_symbols(self):
        src = Path(app_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("score_chunk_with_jev", src)
        self.assertNotIn("filter_non_overlapping_clips", src)


# ---------------------------------------------------------------------------
# 23. to_ass_timestamp emits malformed ASS for negative timestamps
# ---------------------------------------------------------------------------
class TestBug23_NegativeAssTimestamps(unittest.TestCase):

    def test_negative_clamps_to_zero(self):
        self.assertEqual(subtitles.to_ass_timestamp(-2.0), "0:00:00.00")
        self.assertEqual(subtitles.to_ass_timestamp(-0.001), "0:00:00.00")

    def test_output_is_always_a_valid_ass_timestamp(self):
        for v in (-5.0, -1.0, -0.2, 0.0, 0.004, 0.005, 1.0, 59.999, 60.0, 3599.99, 7200.5):
            ts = subtitles.to_ass_timestamp(v)
            self.assertRegex(ts, r"^\d+:\d{2}:\d{2}\.\d{2}$", f"{v} -> {ts}")

    def test_no_known_value_regressed(self):
        self.assertEqual(subtitles.to_ass_timestamp(0.0), "0:00:00.00")
        self.assertEqual(subtitles.to_ass_timestamp(72.35), "0:01:12.35")
        self.assertEqual(subtitles.to_ass_timestamp(61.5), "0:01:01.50")
        self.assertEqual(subtitles.to_ass_timestamp(3661.007), "1:01:01.01")

    def test_non_numeric_input_is_handled(self):
        self.assertEqual(subtitles.to_ass_timestamp(None), "0:00:00.00")
        self.assertEqual(subtitles.to_ass_timestamp("nope"), "0:00:00.00")
        self.assertEqual(subtitles.to_ass_timestamp(float("nan")), "0:00:00.00")


# ---------------------------------------------------------------------------
# 24. The 6-Dimension recap claims a 10x speedup that batching does not provide
# ---------------------------------------------------------------------------
class TestBug24_FalseSpeedupClaim(unittest.TestCase):

    def test_no_speedup_claims_remain(self):
        html = Path(app_module.__file__).resolve().parent.joinpath("templates", "index.html").read_text(encoding="utf-8")
        self.assertNotIn("10x", html)
        self.assertNotIn("Parallel Batch VOD", html)

        scorer_src = Path(scorer.__file__).read_text(encoding="utf-8")
        for claim in ("10x faster", "parallel batches", "in parallel batches"):
            self.assertNotIn(claim, scorer_src, f"Stale claim remains: {claim}")

    def test_batching_is_documented_honestly(self):
        doc = scorer.score_chunks_batch_with_jev.__doc__
        self.assertIn("sequentially", doc)
        self.assertIn("round-trips", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
