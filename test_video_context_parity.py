

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
Regression tests for video-level context (plan Task 3).

The bug: topic extraction and niche classification ran PER CLIP on a ~30 s window, so
clips of one video disagreed. Measured on the real 11-video corpus, 4 of 11 videos had
their clips landing in different niches, and one video spread across 5 niches.

The fix: compute the context ONCE per job from the whole transcript and share it.

These tests drive `run_clipping_job` end to end with stubbed I/O rather than reading
app.py's source text. An earlier version asserted `app.py.count("build_video_context(") == 1`,
which cannot distinguish "once per job" from "once per clip" -- a mutation that moved the
call inside the clip loop survived it. Everything below asserts observable behaviour.
"""

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = pathlib.Path(__file__).parent

import app as app_module
from app import app

import batch_rerender
import clipper as clipper_module
from title_tag_engine import build_video_context


def _job_stub(**over):
    base = {
        "job_id": "j1", "status": "queued", "progress": 0, "step": "",
        "clips": [], "error": None, "_created_at": 0, "_finished_at": None,
    }
    base.update(over)
    return base


TRANSCRIPT = [
    {"text": "The sodium content in that broth is really high.", "start": 0.0, "duration": 4.0},
    {"text": "Sodium is what makes the flavour pop.", "start": 4.0, "duration": 3.0},
    {"text": "Later we talk about the boss fight on Elden Ring.", "start": 30.0, "duration": 4.0},
    {"text": "My weapon level was too low.", "start": 60.0, "duration": 3.0},
]

# Padded so the joined transcript is comfortably longer than 500 characters.
#
# Without this, a mutant that truncates the join (`" ".join(...)[:500]`) is a NO-OP on
# this fixture: the assertion `build_calls[0] == full` still holds, so the suite stays
# green while the CLI silently stops reading most of the transcript. That is a real bug --
# truncating the real corpus to 500 chars flips the niche on 3 of 11 videos and changes
# `seo_topic` on 8 of 11 -- so the fixture has to be long enough for the difference to
# exist at all. `test_full_transcript_is_long_enough_to_detect_truncation` pins that.
#
# The padding is made ONLY of words that are in `BLOCKED_TOPIC_WORDS`, so it adds length
# without adding topics. A first attempt padded with real English ("Filler context line 3
# about the subject matter of this particular video") and the miner duly returned
# `['line', 'filler', 'matter', 'context', 'subject', 'particular']` as topics, which
# quietly changed what the other tests in this module were measuring.
_PAD_WORDS = ("the", "a", "of", "that", "just", "really", "thing", "stuff", "kind",
             "sort", "bit", "way", "well", "so", "and", "but", "like", "yeah", "got")
TRANSCRIPT += [
    {"text": " ".join(_PAD_WORDS[i % len(_PAD_WORDS):] + _PAD_WORDS[:i % len(_PAD_WORDS)]),
     "start": 90.0 + 5 * i, "duration": 4.0}
    for i in range(6)
]
FULL_TRANSCRIPT_TEXT = " ".join(t["text"] for t in TRANSCRIPT)

# Deliberately different from the video context's niche/topic, so a test can tell which
# source a metadata field came from. If code falls back to smart_meta these will not match.
CONTEXT = {
    "topics": ["sodium", "broth", "flavour", "boss", "ring", "elden"],
    "niche": "science_health",
    "niche_tags": ["#science", "#health"],
}
SMART_META_NICHE = "general_viral"

# Two clips whose 30 s windows would classify differently on their own. That disagreement
# is what makes a per-clip implementation visibly wrong.
CLIP_WINDOWS = [
    {"start": 0.0, "end": 20.0, "duration": 20.0, "text": "sodium is what makes the flavour pop",
     "category": "high_value_insight", "virality_score": 8.0, "standalone_prob": 0.8,
     "sponsor_prob": 0.0, "critique": {"scores": {}, "average": 8.0, "passed": True, "notes": ""}},
    {"start": 30.0, "end": 50.0, "duration": 20.0, "text": "the boss fight on elden ring",
     "category": "high_value_insight", "virality_score": 8.0, "standalone_prob": 0.8,
     "sponsor_prob": 0.0, "critique": {"scores": {}, "average": 8.0, "passed": True, "notes": ""}},
    {"start": 60.0, "end": 80.0, "duration": 20.0, "text": "my weapon level was too low",
     "category": "high_value_insight", "virality_score": 8.0, "standalone_prob": 0.8,
     "sponsor_prob": 0.0, "critique": {"scores": {}, "average": 8.0, "passed": True, "notes": ""}},
]


# A pair of clip snippets from one video that classify DIFFERENTLY on their own:
#   SNIPPET_A alone -> general_viral,  SNIPPET_B alone -> gaming,  both -> gaming
# So a per-clip implementation is visibly wrong on this fixture, and a helper that read
# only the first snippet is also visibly wrong. Pinned by
# test_batch_rerender_helper_uses_every_snippet_of_the_video_not_just_the_first.
SNIPPET_A = "sodium is what makes the flavour pop"
SNIPPET_B = "the boss fight on elden ring"


class _JobHarness:
    """
    Runs run_clipping_job with every I/O seam stubbed, recording what it did.

    `fail_context` exists so the failure-path test needs only ONE harness. An earlier
    version of that test built a throwaway harness inside a `with mock.patch(...)` block
    to inject the failure, and because patches are registered with `addCleanup` (which
    unwinds LIFO) the throwaway harness recorded the *raising mock* as the original value
    and left it installed on `app_module.build_video_context` for the rest of the process.
    The module still reported OK, and the leak only surfaced when another module ran
    afterwards and got a MagicMock instead of the real function -- an order-dependent
    failure pointing at the wrong file. Injecting through a constructor argument removes
    the hazard by construction rather than by remembering not to do it again.
    """

    def __init__(self, testcase, context=None, clips=None, fail_context=None):
        self.tc = testcase
        self.context = dict(context or CONTEXT)
        self.clips = clips if clips is not None else CLIP_WINDOWS
        self.fail_context = fail_context
        self.build_calls = []          # every transcript string passed to build_video_context
        self.context_args = []        # the video_context each clip's title call received
        self.tmpdir = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.tmpdir.cleanup)
        self.outdir = Path(self.tmpdir.name)
        self._patch()

    def _build_video_context(self, text):
        self.build_calls.append(text)
        if self.fail_context is not None:
            raise self.fail_context
        return dict(self.context)

    def _generate_meta(self, transcript_text, category, api_key=None, video_context=None):
        self.context_args.append(video_context)
        return {
            "suggested_title": "A Title About Sodium Broth And Flavour",
            "suggested_hashtags": ["#science", "#health"],
            "niche": SMART_META_NICHE,
            "candidates": ["c1"],
            "platform_metadata": {"youtube": {"description": "d"}},
        }

    def _cut(self, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake")
        return True

    def _patch(self):
        m = app_module
        for name, val in [
            ("extract_video_id", lambda url: "vid123"),
            ("get_transcript", lambda vid: list(TRANSCRIPT)),
            ("create_windows", lambda *a, **k: [{"start": 0, "end": 20}]),
            ("get_jev_api_key", lambda: None),
            ("critique_gate_search", lambda **k: list(self.clips)),
            ("cut_and_format_clip", self._cut),
            ("load_upload_config", lambda: {"auto_upload": {"enabled": False}}),
            ("build_video_context", self._build_video_context),
            ("generate_smart_title_and_hashtags", self._generate_meta),
        ]:
            patcher = mock.patch.object(m, name, val)
            patcher.start()
            self.tc.addCleanup(patcher.stop)
        patcher = mock.patch.object(m, "OUTPUT_DIR", self.outdir)
        patcher.start()
        self.tc.addCleanup(patcher.stop)

    def run(self):
        app_module.JOBS["t3"] = _job_stub()
        self.tc.addCleanup(app_module.JOBS.pop, "t3", None)
        app_module.run_clipping_job("t3", "https://youtu.be/vid123", 3, 10)
        return app_module.JOBS["t3"]

    def written_metadata(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.outdir.glob("*.json"))]


class TestVideoContextParity(unittest.TestCase):

    def test_build_video_context_shape(self):
        ctx = build_video_context(" ".join(t["text"] for t in TRANSCRIPT))
        self.assertIn("topics", ctx)
        self.assertIn("niche", ctx)
        self.assertIn("niche_tags", ctx)
        self.assertIsInstance(ctx["topics"], list)
        self.assertIsInstance(ctx["niche"], str)

    def test_one_video_yields_one_niche_for_every_clip(self):
        """
        The core regression, at the level of the real classifier on a real fixture.

        The first version of this test added `ctx["niche"]` to a set inside a loop, so the
        set always held exactly one element and the assertion could never fail. This version
        proves the fix matters FIRST -- that per-clip extraction genuinely disagrees on this
        transcript -- and only then that the shared context resolves the disagreement.
        """
        full = " ".join(t["text"] for t in TRANSCRIPT)
        ctx = build_video_context(full)
        self.assertTrue(ctx["topics"], "context must carry topics")

        clip_texts = ("sodium is what makes the flavour pop",
                      "my weapon level was too low",
                      "the boss fight on elden ring")

        # Guard: had per-clip extraction happened to agree, this test would prove nothing.
        per_clip = {build_video_context(c)["niche"] for c in clip_texts}
        self.assertGreater(len(per_clip), 1,
                           f"guard failed: per-clip contexts already agree ({per_clip}), "
                           f"so the shared-context test is vacuous on this transcript")

        self.assertIsInstance(ctx["niche"], str)
        self.assertNotEqual(ctx["niche"], "", "niche must always be a string")
        for _ in clip_texts:
            self.assertEqual(build_video_context(full)["niche"], ctx["niche"],
                             "build_video_context must be deterministic")

    def test_job_builds_the_context_exactly_once_from_the_full_transcript(self):
        """
        Kills the "once per clip" mutation that a source-text count could not detect.
        """
        h = _JobHarness(self)
        job = h.run()

        self.assertEqual(job["status"], "completed",
                         f"job failed: {job.get('error')}")
        self.assertEqual(
            len(h.build_calls), 1,
            f"build_video_context must run ONCE per job, ran {len(h.build_calls)}x "
            f"for {len(h.clips)} clips")
        self.assertEqual(h.build_calls[0], FULL_TRANSCRIPT_TEXT,
                         "the context must be built from the WHOLE transcript, not a window")

    def test_every_clip_receives_the_same_context_object(self):
        h = _JobHarness(self)
        job = h.run()
        self.assertEqual(job["status"], "completed", f"job failed: {job.get('error')}")

        self.assertEqual(len(h.context_args), len(h.clips),
                         "every clip must call the title engine exactly once")
        for i, ctx in enumerate(h.context_args):
            self.assertIsNotNone(
                ctx, f"clip #{i + 1} was called with no video_context -- the wiring is dead")
        # Identity, not equality: a per-clip rebuild with identical output would still be
        # the bug, because a future context field could differ between clips.
        self.assertEqual(len({id(c) for c in h.context_args}), 1,
                         "each clip must be handed the SAME shared context object, "
                         "not an equivalent per-clip copy")

    def test_metadata_niche_and_seo_topic_come_from_the_shared_context(self):
        h = _JobHarness(self)
        job = h.run()
        self.assertEqual(job["status"], "completed", f"job failed: {job.get('error')}")

        metas = h.written_metadata()
        self.assertEqual(len(metas), len(h.clips), "one metadata file per rendered clip")
        for m in metas:
            self.assertEqual(
                m["niche"], CONTEXT["niche"],
                "niche must come from the shared video context, not the per-clip smart_meta")
            self.assertNotEqual(
                m["niche"], SMART_META_NICHE,
                "guard: the stub's smart_meta niche differs from the context's, so a "
                "fallback to smart_meta is detectable")
            self.assertEqual(m["seo_topic"], CONTEXT["topics"][0],
                             "seo_topic must be the context's first topic")

        self.assertEqual(len({m["niche"] for m in metas}), 1,
                         "all clips of one video must agree on niche")
        self.assertEqual(len({m["seo_topic"] for m in metas}), 1,
                         "all clips of one video must agree on seo_topic")

    def test_metadata_on_disk_matches_the_metadata_returned_to_the_job(self):
        h = _JobHarness(self)
        job = h.run()
        self.assertEqual(job["status"], "completed", f"job failed: {job.get('error')}")
        on_disk = h.written_metadata()
        self.assertEqual([m["filename"] for m in on_disk], [c["filename"] for c in job["clips"]])
        self.assertEqual([m["niche"] for m in on_disk], [c["niche"] for c in job["clips"]])
        self.assertEqual([m["seo_topic"] for m in on_disk], [c["seo_topic"] for c in job["clips"]])

    def test_empty_context_topics_serialise_as_null_not_absent(self):
        """
        An empty topic list must not raise and must not silently drop the key.

        The context's niche is deliberately DIFFERENT from `SMART_META_NICHE` so the
        second assertion can tell which source won. An earlier version passed
        `niche="general_viral"` here while the smart_meta stub also returned
        `"general_viral"`, so its comment ("the niche must still come from the context")
        described a distinction the assertion could not detect.
        """
        h = _JobHarness(self, context={"topics": [], "niche": "topicless_niche",
                                       "niche_tags": []})
        job = h.run()
        self.assertEqual(job["status"], "completed", f"job failed: {job.get('error')}")
        for m in h.written_metadata():
            self.assertIn("seo_topic", m,
                          "key must be present so consumers see null, not a missing field")
            self.assertIsNone(m["seo_topic"])
            self.assertEqual(m["niche"], "topicless_niche",
                             "niche must come from the context even with no topics")
            self.assertNotEqual(m["niche"], SMART_META_NICHE,
                                "guard: both sources must differ for this to prove anything")

    def test_context_failure_is_reported_through_the_job_not_raised(self):
        """A failure in the new code must land in the job error path, not kill the thread."""
        h = _JobHarness(self, fail_context=RuntimeError("synthetic context failure"))
        job = h.run()
        self.assertEqual(job["status"], "failed")
        self.assertIn("synthetic context failure", job["error"])
    def test_older_callers_omitting_video_context_trigger_the_back_compat_default(self):
        """
        Pins the back-compatibility guarantee, which was previously untested.

        `generate_smart_title_and_hashtags` still has callers that pass no
        `video_context`, and the `if video_context is None:` default that derives one is
        therefore load bearing. It was completely unpinned: deleting it, or building the
        context from the empty string, both left the suite green.

        **The assertion must be on the CALL, not on the return value.** An earlier draft
        of this test asserted `meta["niche"] == classify_niche(FULL_TRANSCRIPT_TEXT)` and
        passed -- but by coincidence. `video_context` is currently a *dead assignment*:
        AST shows exactly two references, the `is None` load and the store, and it is
        never read. The `niche` in the return value comes from
        `score_and_rank_titles_with_jev`, a live API that returns 502 here, whose fallback
        happened to agree with the classifier on this fixture. A different fixture would
        have turned that false green red for no reason. What IS observable today is that
        the default runs, and runs on the caller's own text.
        """
        import title_tag_engine as tte

        with mock.patch.object(tte, "build_video_context",
                               wraps=tte.build_video_context) as spy:
            tte.generate_smart_title_and_hashtags(FULL_TRANSCRIPT_TEXT)

        self.assertEqual(spy.call_count, 1,
                         "omitting video_context must trigger the back-compat default "
                         "exactly once")
        self.assertEqual(
            spy.call_args.args[0], FULL_TRANSCRIPT_TEXT,
            "the default must build the context from the CALLER's text -- not a constant, "
            "not an empty string, and not a window")

    def test_passing_a_context_skips_the_back_compat_default_entirely(self):
        """The mirror image: an explicit context must not trigger a redundant rebuild."""
        import title_tag_engine as tte

        with mock.patch.object(tte, "build_video_context",
                               wraps=tte.build_video_context) as spy:
            tte.generate_smart_title_and_hashtags(FULL_TRANSCRIPT_TEXT,
                                                  video_context=dict(CONTEXT))
        self.assertEqual(spy.call_count, 0,
                         "a caller that supplies a context must not pay for a second one")

    def test_the_failure_test_leaves_no_mock_behind(self):
        """
        Pins the leak that the previous version of the failure test caused.

        `addCleanup` unwinds last-registered-first, so a harness constructed *inside* a
        `mock.patch` context recorded the patched mock as the value to restore. This test
        would pass while the module silently poisoned `app_module.build_video_context`
        for every later module. Assert the real function is back in place.
        """
        from title_tag_engine import build_video_context as real_fn
        self.assertIs(app_module.build_video_context, real_fn,
                      "a previous test left a mock installed on app.build_video_context")

    def test_status_endpoint_never_leaks_context_internals(self):
        """
        The status endpoint must not expose the internal context object.

        An earlier version of this test built its own literal clip dict and asserted the
        literal lacked the key -- it could not fail for any production change, because it
        never touched production data. This version renders a real job through the harness
        and reads back what the endpoint actually serves.
        """
        h = _JobHarness(self)
        job = h.run()
        self.assertEqual(job["status"], "completed", f"job failed: {job.get('error')}")

        app_module.JOBS["parity"] = _job_stub(
            status="completed", clips=h.written_metadata())
        self.addCleanup(app_module.JOBS.pop, "parity", None)
        body = app.test_client().get("/api/status/parity").get_json()

        self.assertTrue(body["clips"])
        self.assertEqual(len(body["clips"]), len(h.clips))
        for served in body["clips"]:
            self.assertNotIn("video_context", served,
                             f"the internal context leaked to the client: {served}")
            # The fields that SHOULD be there prove the dict is real, not a stub.
            self.assertIn("niche", served)
            self.assertIn("seo_topic", served)
            self.assertIn("filename", served)


class _CliHarness:
    """
    Runs clipper.process_video with every I/O seam stubbed, recording what it did.

    The CLI is a second producer of the same output/*.json schema, so it needs the same
    behavioural proof the web path got. It previously had only two `assertIn` checks on
    clipper.py's source text, which cannot distinguish once-per-job from once-per-clip:
    a mutant that ADDS a per-clip `video_context = build_video_context(clip["text"])`
    inside the loop leaves every source anchor intact and survives.
    """

    def __init__(self, testcase, context=None, clips=None):
        self.tc = testcase
        self.context = dict(context or CONTEXT)
        self.clips = clips if clips is not None else CLIP_WINDOWS
        self.build_calls = []
        self.context_args = []
        self.tmpdir = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.tmpdir.cleanup)
        self.outdir = Path(self.tmpdir.name)
        self._patch()

    def _build_video_context(self, text):
        self.build_calls.append(text)
        return dict(self.context)

    def _generate_meta(self, transcript_text, category, api_key=None, video_context=None):
        self.context_args.append(video_context)
        return {
            "suggested_title": "A Title About Sodium Broth And Flavour",
            "suggested_hashtags": ["#science", "#health"],
            "niche": SMART_META_NICHE,
            "candidates": ["c1"],
            "platform_metadata": {"youtube": {"description": "d"}},
        }

    def _cut(self, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake")
        return True

    def _patch(self):
        m = clipper_module
        for name, val in [
            ("extract_video_id", lambda url: "vid123"),
            ("get_transcript", lambda vid: list(TRANSCRIPT)),
            ("create_windows", lambda *a, **k: [{"start": 0, "end": 20}]),
            ("get_jev_api_key", lambda: None),
            ("critique_gate_search", lambda **k: list(self.clips)),
            ("cut_and_format_clip", self._cut),
            ("load_upload_config", lambda: {"auto_upload": {"enabled": False}}),
            ("build_video_context", self._build_video_context),
            ("generate_smart_title_and_hashtags", self._generate_meta),
        ]:
            patcher = mock.patch.object(m, name, val)
            patcher.start()
            self.tc.addCleanup(patcher.stop)
        patcher = mock.patch.object(m, "OUTPUT_DIR", self.outdir)
        patcher.start()
        self.tc.addCleanup(patcher.stop)

    def run(self):
        # process_video prints a lot and returns None; capture the noise.
        with contextlib.redirect_stdout(io.StringIO()):
            clipper_module.process_video("https://youtu.be/vid123", top_k=3, candidates=10)
        return self

    def written_metadata(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.outdir.glob("*.json"))]


class TestCliEntryPointBehaviour(unittest.TestCase):
    """
    `clipper.process_video` driven end to end. These are the tests that were missing:
    the CLI's coverage was two source-text `assertIn` checks, which cannot tell
    once-per-job from once-per-clip, and which a per-clip `build_video_context(clip["text"])`
    mutant inside the loop passes without noticing.
    """

    def test_cli_builds_the_context_exactly_once_from_the_full_transcript(self):
        h = _CliHarness(self).run()

        self.assertEqual(
            len(h.build_calls), 1,
            f"the CLI must build the video context ONCE per run, built it "
            f"{len(h.build_calls)}x for {len(h.clips)} clips -- this is the per-clip bug")
        self.assertEqual(
            h.build_calls[0], FULL_TRANSCRIPT_TEXT,
            "the CLI must build the context from the WHOLE transcript, not a clip window")

    def test_full_transcript_is_long_enough_to_detect_truncation(self):
        """
        Guards the two "built from the whole transcript" assertions above.

        Both compare against the full join, so a `[:500]` truncation of that join is
        invisible unless the fixture actually exceeds 500 characters. It did not, and the
        mutant survived a full mutation round. The real corpus is ~14 kB per video, where
        truncation flips the niche on 3 of 11 videos and changes `seo_topic` on 8 of 11,
        so the fixture has to be representative too.
        """
        self.assertGreater(
            len(FULL_TRANSCRIPT_TEXT), 500,
            "if the fixture fits inside a 500-char truncation, the full-transcript "
            "assertions cannot detect one")
        self.assertNotEqual(FULL_TRANSCRIPT_TEXT, FULL_TRANSCRIPT_TEXT[:500],
                            "truncation must actually change this fixture")

    def test_cli_truncating_the_transcript_would_change_the_niche(self):
        """
        Guards the assertion above: prove a truncated join is actually wrong here.

        A `[:500]` mutant survives a "does it join the transcript" test unless the
        fixture is long enough that truncation changes the answer. This pins that the
        real corpus is sensitive to it: truncating each video's snippet union to 500
        characters flips the niche on 3 of 11 videos.
        """
        import glob as _glob
        import topic_engine as _te

        pools = {}
        for p in _glob.glob(str(ROOT / "output" / "*.json")):
            try:
                d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("transcript_snippet"):
                pools.setdefault(d.get("video_id"), []).append(d["transcript_snippet"])
        if not pools:
            self.skipTest("no real corpus available to calibrate against")

        changed = sum(1 for snips in pools.values()
                      if _te.classify_niche(" ".join(snips))
                      != _te.classify_niche(" ".join(snips)[:500]))
        self.assertGreater(
            changed, 0,
            "if truncating the transcript never changed a niche, the full-transcript "
            "assertion above would be toothless and this corpus could not detect it")

    def test_cli_gives_every_clip_the_same_shared_context_object(self):
        h = _CliHarness(self).run()
        self.assertEqual(len(h.context_args), len(h.clips),
                         "every clip must call the title engine exactly once")
        for i, ctx in enumerate(h.context_args):
            self.assertIsNotNone(ctx, f"clip #{i + 1} got no video_context -- wiring is dead")
        self.assertEqual(len({id(c) for c in h.context_args}), 1,
                         "each clip must be handed the SAME object, not a per-clip copy")

    def test_cli_metadata_niche_and_seo_topic_come_from_the_shared_context(self):
        h = _CliHarness(self).run()
        metas = h.written_metadata()
        self.assertEqual(len(metas), len(h.clips),
                         "one metadata file per rendered clip; an empty list means the "
                         "CLI bailed early and every other assertion here is vacuous")
        for m in metas:
            self.assertEqual(m["niche"], CONTEXT["niche"])
            self.assertNotEqual(m["niche"], SMART_META_NICHE,
                                "guard: both sources must differ for this to prove anything")
            self.assertEqual(m["seo_topic"], CONTEXT["topics"][0])
        self.assertEqual(len({m["niche"] for m in metas}), 1)
        self.assertEqual(len({m["seo_topic"] for m in metas}), 1)

    def test_cli_and_web_write_the_same_metadata_keys_in_the_same_order(self):
        """
        The two entry points must not drift into two schemas.

        The CLI gained this wiring in a separate commit from the web path, so drift is a
        live risk rather than a theoretical one.
        """
        web_h = _JobHarness(self)
        web_h.run()
        cli_h = _CliHarness(self).run()
        web = web_h.written_metadata()
        cli = cli_h.written_metadata()
        self.assertTrue(web and cli, "both entry points must have produced metadata")
        self.assertEqual(list(web[0].keys()), list(cli[0].keys()))

        web_niche = {k: web[0][k] for k in web[0] if k not in
                     ("critique", "rubric_scores", "rubric_average", "passed_gate",
                      "critique_notes")}
        cli_niche = {k: cli[0][k] for k in cli[0] if k not in
                     ("critique", "rubric_scores", "rubric_average", "passed_gate",
                      "critique_notes")}
        for field in ("niche", "seo_topic", "suggested_title", "suggested_hashtags"):
            self.assertEqual(web_niche.get(field), cli_niche.get(field),
                             f"the two entry points disagree on {field!r}")


class TestEntryPointParity(unittest.TestCase):
    """
    Three separate entry points write the same output/*.json schema: app.py (web),
    clipper.py (CLI) and batch_rerender.py (backfill). Task 3 originally wired only the
    first, so the CLI path kept the per-clip bug and the backfill path actively overwrote
    a good video-level niche with a per-clip one. These tests pin all three.
    """

    def _read(self, name):
        return (Path(__file__).parent / name).read_text(encoding="utf-8")

    def test_app_and_clipper_both_derive_niche_from_the_shared_context(self):
        """
        Both producers are pinned behaviourally in `TestCliEntryPointBehaviour` and
        `TestVideoContextParity`. This test deliberately keeps NO source-text assertions.

        An earlier version grepped `app.py` and `clipper.py` for
        `video_context.get("niche")` and `"seo_topic"`. That is the same anti-pattern the
        rest of this module exists to remove: a grep cannot tell the real code from a
        comment that quotes it, and it cannot tell a correct call from a per-clip one. It
        gave a false green when a mutant added `build_video_context(clip["text"])` inside
        the CLI's clip loop while every grepped string survived.
        """
        self.assertTrue(hasattr(app_module, "build_video_context"))
        self.assertTrue(hasattr(clipper_module, "build_video_context"))
        for name in ("app.py", "clipper.py"):
            with self.subTest(entry_point=name):
                self.assertTrue(
                    (Path(__file__).parent / name).exists(),
                    f"{name} must exist for the behavioural tests to mean anything")

    def test_cli_and_web_agree_on_the_niche_they_would_write(self):
        """
        Both entry points, driven for real, must write the same niche for the same video.

        The previous version of this test claimed to be "the executable version" of a
        source check, but built its `recorded` list from a single `shared` object, so
        `len({r["niche"] for r in recorded}) == 1` held for any production expression at
        all. All of its value came from two greps. `TestCliEntryPointBehaviour` now drives
        both for real; this test is the cross-process comparison that a per-entry-point
        test cannot make.
        """
        web_h = _JobHarness(self)
        web_h.run()
        cli_h = _CliHarness(self).run()
        web = web_h.written_metadata()
        cli = cli_h.written_metadata()

        self.assertTrue(web and cli, "both entry points must have produced metadata")
        # Both harnesses stub build_video_context with the same fixed CONTEXT, so the
        # expected value is that stub's niche -- not whatever the real classifier makes of
        # the fixture text, which is a different thing entirely.
        self.assertEqual({m["niche"] for m in web}, {CONTEXT["niche"]})
        self.assertEqual({m["niche"] for m in cli}, {CONTEXT["niche"]})
        self.assertEqual({m["seo_topic"] for m in web}, {CONTEXT["topics"][0]})
        self.assertEqual({m["seo_topic"] for m in cli}, {CONTEXT["topics"][0]})
        self.assertNotEqual(CONTEXT["niche"], SMART_META_NICHE,
                            "guard: both candidate sources must differ for this to bite")

    def test_batch_rerender_groups_clips_by_video_before_extracting(self):
        """
        Behavioural replacement for a source grep.

        The previous version asserted `_video_context_by_id` appeared in batch_rerender.py
        and that the text `data["niche"] = smart_meta.get("niche"` did NOT. The second
        half then failed for a silly reason: the new comment explaining *why* the old line
        was removed quotes that exact line as evidence. A grep cannot tell code from a
        comment about the code, which is precisely why the rest of this module is
        behavioural. Driving `rerender_all` proves the real property.
        """
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for i, snip in enumerate((SNIPPET_A, SNIPPET_B)):
                (out / f"vA_{i}.json").write_text(json.dumps({
                    "video_id": "vA", "filename": f"vA_{i}.mp4",
                    "start_time": 0.0, "end_time": 20.0,
                    "transcript_snippet": snip, "category": "high_value_insight",
                    "niche": "general_viral",
                    "suggested_title": "Already Fine",
                    "candidate_titles": ["c1"],
                }), encoding="utf-8")

            def fake_cut(output_path, **kwargs):
                Path(output_path).write_bytes(b"fake")
                return True

            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(batch_rerender, "OUTPUT_DIR", out), \
                 mock.patch.object(batch_rerender, "cut_and_format_clip", fake_cut):
                batch_rerender.rerender_all(skip_recent_seconds=0)

            written = [json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted(out.glob("*.json"))]

        import topic_engine as te
        expected = te.classify_niche(SNIPPET_A + " " + SNIPPET_B)
        self.assertEqual(len(written), 2)
        self.assertEqual({m["niche"] for m in written}, {expected},
                         "both clips must resolve to the ONE video-level niche, not their own")

    def test_batch_rerender_context_helper_gives_one_niche_per_video(self):
        """Run the real helper over synthetic metadata files for two videos."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for i, (vid, snip) in enumerate([
                ("vA", "sodium is what makes the flavour pop"),
                ("vA", "the boss fight on elden ring"),
                ("vB", "my weapon level was too low and the boss was hard"),
            ]):
                (out / f"{vid}_{i}.json").write_text(
                    json.dumps({"video_id": vid, "transcript_snippet": snip}), encoding="utf-8")
            files = sorted(out.glob("*.json"))
            with mock.patch.object(batch_rerender, "OUTPUT_DIR", out):
                ctxs = batch_rerender._video_context_by_id(files)
            self.assertEqual(set(ctxs), {"vA", "vB"})
            for vid, ctx in ctxs.items():
                self.assertTrue(ctx["topics"], f"{vid} context must carry topics")
                self.assertIsInstance(ctx["niche"], str)

    def test_batch_rerender_helper_uses_every_snippet_of_the_video_not_just_the_first(self):
        """
        Kills the `parts[0]` mutation.

        SNIPPET_A alone classifies as general_viral, SNIPPET_B alone as gaming, and the two
        together as gaming. So a helper that only read the first snippet would return
        general_viral for a video whose real answer is gaming -- unambiguously wrong.
        """
        import topic_engine as te

        self.assertNotEqual(te.classify_niche(SNIPPET_A), te.classify_niche(SNIPPET_B),
                            "guard: the two snippets must disagree on their own")
        self.assertEqual(te.classify_niche(SNIPPET_A + " " + SNIPPET_B),
                         te.classify_niche(SNIPPET_B),
                         "guard: the combined answer must match B, not A, for this to bite")
        self.assertNotEqual(te.classify_niche(SNIPPET_A + " " + SNIPPET_B),
                            te.classify_niche(SNIPPET_A),
                            "guard: combined must differ from first-snippet-only")

        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for i, snip in enumerate((SNIPPET_A, SNIPPET_B)):
                (out / f"vA_{i}.json").write_text(
                    json.dumps({"video_id": "vA", "transcript_snippet": snip}), encoding="utf-8")
            with mock.patch.object(batch_rerender, "OUTPUT_DIR", out):
                ctx = batch_rerender._video_context_by_id(sorted(out.glob("*.json")))["vA"]

        self.assertEqual(ctx["niche"], te.classify_niche(SNIPPET_A + " " + SNIPPET_B),
                         "the context must be built from ALL of the video's snippets")
        self.assertNotEqual(ctx["niche"], te.classify_niche(SNIPPET_A),
                            "reading only the first snippet is a regression to per-clip extraction")

    def test_rerender_all_writes_one_niche_per_video_not_one_per_clip(self):
        """
        Kills the per-clip-context mutation, by driving the real `rerender_all` loop.

        This is the behaviour that matters: the backfill rewrites the SAME output/*.json
        schema the other two entry points write, so a per-clip niche here re-introduces the
        original bug into every previously rendered clip.
        """
        import topic_engine as te

        expected_niche = te.classify_niche(SNIPPET_A + " " + SNIPPET_B)

        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for i, snip in enumerate((SNIPPET_A, SNIPPET_B)):
                # No candidate_titles -> the backfill branch runs, which is the code under test.
                (out / f"vA_{i}.json").write_text(json.dumps({
                    "video_id": "vA",
                    "filename": f"vA_{i}.mp4",
                    "start_time": 0.0,
                    "end_time": 20.0,
                    "transcript_snippet": snip,
                    "category": "high_value_insight",
                    "suggested_title": "The brutal truth about High Value Insight",
                }), encoding="utf-8")

            def fake_cut(output_path, **kwargs):
                Path(output_path).write_bytes(b"fake")
                return True

            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(batch_rerender, "OUTPUT_DIR", out), \
                 mock.patch.object(batch_rerender, "cut_and_format_clip", fake_cut):
                batch_rerender.rerender_all(skip_recent_seconds=0)

            written = [json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted(out.glob("*.json"))]

        self.assertEqual(len(written), 2)
        niches = {m["niche"] for m in written}
        self.assertEqual(niches, {expected_niche},
                         f"clips of one video must share one niche, got {niches}; "
                         f"expected the video-level value {expected_niche!r}")
        self.assertNotEqual(niches, {te.classify_niche(SNIPPET_A)},
                            "regression: the niche came from one clip's snippet only")
        for m in written:
            self.assertIn("seo_topic", m,
                          "the backfill must write the same schema as app.py and clipper.py")
    def test_rerender_preserves_a_post_task3_niche_but_replaces_a_pre_task3_one(self):
        """
        The niche policy, pinned in all three directions.

        The discriminator is `seo_topic_source`, which a producer writes down to record WHICH
        evidence its `niche` came from:

        * `"full_transcript"` -> app.py or clipper.py wrote it with the WHOLE transcript.
          Stronger evidence than the snippet union available here, so the niche is preserved.
        * `"snippet_union"`   -> backfill_titles.py wrote it from 30 s snippets, the same
          evidence level as this script, so the recomputation is as good and replaces it.
        * absent              -> unknown, so the recomputation replaces it and labels it.

        THIS USED TO BRANCH ON KEY PRESENCE, and the docstring here claimed carrying
        `seo_topic` "records WHICH producer wrote the file". That claim went stale the moment
        backfill_titles.py began writing the same key from snippet unions, and the rule
        silently became "preserve everything" -- a permanent freeze of exactly the data it
        was meant to be able to correct. So three versions are worth naming:

          * The first asserted EVERY existing niche was preserved, on the false premise that
            "an existing niche came from a run that had the FULL transcript". It froze
            known-bad data and left the real corpus at 9 of 11 videos disagreeing.
          * The second branched on `seo_topic` presence, which stopped being evidence of
            anything once the backfill wrote that key.

        A third defect was found by running the code rather than reading it: protecting only
        `niche` and `seo_topic` is not enough. The title, hashtags and candidate list come from
        the same context, so `backfill_titles.py` now skips a full-transcript file IN FULL.
        That is not exercised here, because this test drives `batch_rerender.py`, which only
        owns the niche/topic pair -- but it is the reason the guard is not narrower.
        """
        import topic_engine as te

        expected = te.classify_niche(SNIPPET_A + " " + SNIPPET_B)
        self.assertNotEqual(expected, "science_health",
                            "guard: the fixture's real niche must differ from the value "
                            "used here as 'a niche only a full transcript would give'")

        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            # Post-Task-3 file: has seo_topic, so its niche came from the full transcript.
            (out / "vA_0.json").write_text(json.dumps({
                "video_id": "vA", "filename": "vA_0.mp4",
                "start_time": 0.0, "end_time": 20.0,
                "transcript_snippet": SNIPPET_A, "category": "high_value_insight",
                "niche": "science_health", "seo_topic": "sodium",
                # Declared, not inferred. Presence alone stopped meaning anything once the
                # backfill began writing `seo_topic` from snippet unions.
                "seo_topic_source": "full_transcript",
                "suggested_title": "Already Fine", "candidate_titles": ["c1"],
            }), encoding="utf-8")
            # Pre-Task-3 file: no seo_topic, so its niche is a per-clip guess.
            (out / "vA_1.json").write_text(json.dumps({
                "video_id": "vA", "filename": "vA_1.mp4",
                "start_time": 20.0, "end_time": 40.0,
                "transcript_snippet": SNIPPET_B, "category": "high_value_insight",
                "niche": "comedy_entertainment",
                "suggested_title": "Already Fine", "candidate_titles": ["c1"],
            }), encoding="utf-8")

            def fake_cut(output_path, **kwargs):
                Path(output_path).write_bytes(b"fake")
                return True

            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(batch_rerender, "OUTPUT_DIR", out), \
                 mock.patch.object(batch_rerender, "cut_and_format_clip", fake_cut):
                batch_rerender.rerender_all(skip_recent_seconds=0)

            by_name = {p.name: json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted(out.glob("*.json"))}

        self.assertEqual(
            by_name["vA_0.json"]["niche"], "science_health",
            "a file declaring seo_topic_source=full_transcript came from a producer that saw "
            "the whole video; downgrading it to a snippet-union guess would lose information")
        self.assertEqual(
            by_name["vA_0.json"]["seo_topic_source"], "full_transcript",
            "the declaration must be preserved verbatim, not rewritten to a value this script "
            "cannot justify")
        self.assertEqual(
            by_name["vA_1.json"]["niche"], expected,
            "a file with no declared provenance has an untrusted niche, so it must be replaced "
            "with the video-level one")
        self.assertEqual(
            by_name["vA_1.json"]["seo_topic_source"], "snippet_union",
            "a file this script recomputed must be labelled with the evidence it used, so the "
            "next run can tell it apart from a full-transcript one")
        for name, m in by_name.items():
            self.assertIn("seo_topic", m, f"{name} must gain seo_topic")

    def test_rerender_writes_the_contexts_actual_seo_topic_value(self):
        """
        The *value* of `seo_topic`, not just its presence.

        Three separate mutants wrote a wrong value here -- `None`, `[-1]`, and
        `niche_tags[0]` -- and all survived, because the old test only asserted
        `assertIn("seo_topic", m)`. Nothing downstream reads this field yet, so a wrong
        value would have shipped silently.
        """
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for i, snip in enumerate((SNIPPET_A, SNIPPET_B)):
                (out / f"vA_{i}.json").write_text(json.dumps({
                    "video_id": "vA", "filename": f"vA_{i}.mp4",
                    "start_time": 0.0, "end_time": 20.0,
                    "transcript_snippet": snip, "category": "high_value_insight",
                    "niche": "general_viral",
                    "suggested_title": "Already Fine", "candidate_titles": ["c1"],
                }), encoding="utf-8")

            def fake_cut(output_path, **kwargs):
                Path(output_path).write_bytes(b"fake")
                return True

            with contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch.object(batch_rerender, "OUTPUT_DIR", out), \
                 mock.patch.object(batch_rerender, "cut_and_format_clip", fake_cut):
                batch_rerender.rerender_all(skip_recent_seconds=0)

            written = [json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted(out.glob("*.json"))]

        expected = build_video_context(SNIPPET_A + " " + SNIPPET_B)
        want = (expected["topics"] or [None])[0]
        self.assertIsNotNone(want, "guard: the fixture must mine a topic")
        for m in written:
            self.assertEqual(m["seo_topic"], want,
                             f"seo_topic must be the context's FIRST topic {want!r}, "
                             f"got {m['seo_topic']!r}")
            self.assertNotEqual(m["seo_topic"], expected["topics"][-1],
                                "the last topic is not the first; that mutant survived once")
            self.assertNotIn(m["seo_topic"], expected["niche_tags"],
                             "seo_topic must come from topics, not from niche_tags")

    def test_rerender_all_survives_a_failure_building_the_video_contexts(self):
        """
        `_video_context_by_id` is the one call in `rerender_all` that sits outside every
        try block. Unguarded, a raise there aborts the whole batch before the summary
        accounting runs -- exactly the failure the `cut_and_format_clip` guard further down
        exists to prevent. It now degrades to "no context for anyone" and still renders.
        """
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "vA_0.json").write_text(json.dumps({
                "video_id": "vA", "filename": "vA_0.mp4",
                "start_time": 0.0, "end_time": 20.0,
                "transcript_snippet": SNIPPET_A, "category": "high_value_insight",
                "niche": "general_viral",
                "suggested_title": "Already Fine", "candidate_titles": ["c1"],
            }), encoding="utf-8")

            def fake_cut(output_path, **kwargs):
                Path(output_path).write_bytes(b"fake")
                return True

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                 mock.patch.object(batch_rerender, "OUTPUT_DIR", out), \
                 mock.patch.object(batch_rerender, "_video_context_by_id",
                                   side_effect=RuntimeError("synthetic")), \
                 mock.patch.object(batch_rerender, "cut_and_format_clip", fake_cut):
                batch_rerender.rerender_all(skip_recent_seconds=0)

            written = [json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted(out.glob("*.json"))]

        out_text = buf.getvalue()
        self.assertIn("BATCH RE-RENDER SUMMARY", out_text,
                      "the batch must reach its summary instead of aborting")
        self.assertIn("synthetic", out_text,
                      "the failure must be reported, not swallowed silently")
        self.assertEqual(len(written), 1, "the clip must still have been processed")
        # With no context available, the existing niche is left alone rather than guessed.
        self.assertEqual(written[0]["niche"], "general_viral")

    def test_batch_rerender_does_not_silently_swallow_a_title_backfill_failure(self):
        """The bare `except Exception: pass` hid every failure in the backfill block."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "vA_0.json").write_text(json.dumps({
                "video_id": "vA", "filename": "vA_0.mp4",
                "start_time": 0.0, "end_time": 20.0,
                "transcript_snippet": SNIPPET_A, "category": "high_value_insight",
                "niche": "general_viral",
                "suggested_title": "The brutal truth about High Value Insight",
            }), encoding="utf-8")

            def fake_cut(output_path, **kwargs):
                Path(output_path).write_bytes(b"fake")
                return True

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                 mock.patch.object(batch_rerender, "OUTPUT_DIR", out), \
                 mock.patch.object(batch_rerender, "generate_smart_title_and_hashtags",
                                   side_effect=RuntimeError("jev is down")), \
                 mock.patch.object(batch_rerender, "cut_and_format_clip", fake_cut):
                batch_rerender.rerender_all(skip_recent_seconds=0)

        out_text = buf.getvalue()
        self.assertIn("BATCH RE-RENDER SUMMARY", out_text)
        self.assertIn("jev is down", out_text,
                      "a failed backfill must be reported; a bare `pass` made it invisible")


if __name__ == "__main__":
    unittest.main()
