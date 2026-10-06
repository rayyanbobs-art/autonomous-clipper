"""
Whole-pipeline quality budget, measured over the real clips in output/.

THREE kinds of assertion live here, in separate classes on purpose:

  * `TestGeneratedOutput` calls the engine and judges what it produces right now. Every one
    of these must be GREEN; a failure means the ENGINE regressed.
  * `TestStoredMetadata` reads the `suggested_title` already on disk. These only pass once
    Task 8 has rewritten the 45 files; a failure before then means the corpus is STALE, not
    that anything is broken.
  * `TestCorpusVsEngine` recomputes what the engine would say today and compares it to what is
    on disk. It straddles the two, so it is deliberately in neither of the above: it was
    originally filed under `TestGeneratedOutput` and was therefore the one red test sitting in
    the class whose own docstring promised green.

Mixing the first two made Step 3 ("confirm the failures are only the stale-metadata ones")
impossible to carry out, because the report did not say which kind failed.

Two rules the assertions in this file follow, both learned the hard way here:

  1. NEVER import a limit or a transformation from the module under test and assert against
     it. `MAX_TITLE_CHARS` and `MAX_HASHTAGS` both move if someone edits the engine, so a
     budget that reads them moves too and never fires. Limits are pinned as literals below.
  2. Where a test DOES call a production helper, it is a deliberate SEAM check -- asserting
     that the orchestrator uses the shared builder rather than re-deriving the value -- and
     its docstring says so, so nobody "fixes" it into a tautology.
"""
import collections
import glob
import json
import os
import re
import unittest
from unittest import mock

from title_seo import MAX_TITLE_CHARS, validate_title
from title_tag_engine import build_video_context, generate_smart_title_and_hashtags

HERE = os.path.dirname(os.path.abspath(__file__))

# Limits pinned as LITERALS, per rule 1 above.
#   * YouTube's documented maximum title length. An external fact, not an engine choice.
#   * This project's per-Short hashtag budget. Also a project decision, not an engine choice.
# If either engine constant is raised without a deliberate decision to change the budget,
# these assertions are what will notice.
YOUTUBE_TITLE_LIMIT = 100
BUDGET_MAX_TAGS = 5

# The minimum number of candidates a clip must be offered. The engine builds 5 so that
# per-clip rotation has something to rotate through; 3 leaves margin for a niche with fewer
# patterns and still fails a "return one candidate" regression.
BUDGET_MIN_CANDIDATES = 3


def _load_clips():
    clips = []
    for path in glob.glob(os.path.join(HERE, "output", "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("transcript_snippet"):
            clips.append(data)
    return clips


CLIPS = _load_clips()
SKIP = unittest.skipUnless(CLIPS, "no clips in output/ to measure")


def _by_video():
    """video_id -> that video's clips, in the grouping the pipeline itself uses."""
    pools = collections.defaultdict(list)
    for clip in CLIPS:
        pools[clip.get("video_id")].append(clip)
    return pools


def _published_form(topic):
    """
    The tag a topic must be published as, stated as a SPECIFICATION.

    Deliberately NOT `hashtag_engine._clean_tag`. The previous version of this file asserted
    `tag.lstrip('#').lower() in topic.lower()`, which models the transformation backwards:
    `_clean_tag` CONCATENATES, so the multi-word topic 'toboggan run' publishes as
    `#tobogganrun`, and `'tobogganrun' in 'toboggan run'` is False. Measured, that predicate
    is false for all 30 multi-word topics in the corpus, so the assertion was being satisfied
    by a *different*, single-word topic and passing for the wrong reason.

    Every topic in the corpus matches `[A-Za-z0-9 ]+`, so lowercase-plus-remove-whitespace is
    the whole rule -- with one documented exception: `_clean_tag('com')` returns '' because of
    a URL-fragment rule, so a topic the engine has already decided not to publish is not
    something to assert about. The test below therefore asserts "at least one published tag is
    a topic in published form", which is the true property: `build_hashtags` caps at 5 tags,
    two of which are the niche's, so a video with 8 topics cannot and should not publish all 8.
    """
    return "#" + "".join(topic.lower().split())


# ONE context per video, built once, shared by every test.
#
# An earlier version of this file built the context PER CLIP in four of its five
# `TestGeneratedOutput` tests, while the same file's niche-parity docstring called that
# "the pre-Task-3 path the pipeline stopped using". Measured on the real corpus, switching
# these to the video-level context changes 42 of 45 titles and 43 of 45 hashtags -- so a file
# billed as the whole-pipeline gate was inspecting ~93% of the values that would never ship,
# and could not tell the difference. This is the same unpinned-seam defect that let
# `rotation=0` survive the Task 4 suite, recurring in the guard written to catch that class.
VIDEO_CONTEXTS = {
    vid: build_video_context(" ".join(c["transcript_snippet"] for c in clips))
    for vid, clips in _by_video().items()
}


def setUpModule():
    """
    Make this file hermetic. Without it the budget makes 356 live HTTPS calls.

    `generate_smart_title_and_hashtags` ends in `score_and_rank_titles_with_jev`, which POSTs
    to a third-party API whenever `get_jev_api_key()` returns a key -- and on a configured
    machine it does. Instrumented over the full run, that function is entered 356 times, so
    the stub has to cover the whole module, not just the tests that loop the corpus.

    Measured on the whole file, stub neutralised and then restored, same corpus, same run:

        with the stub     :  16 tests in   1.19 s,  4 failures
        without the stub  :  16 tests in 240.71 s,  4 failures   (146x slower)

    The verdict is identical either way, which is the point: the stub buys determinism and
    four minutes of wall clock, and costs no coverage. Nothing in this file asserts on
    `confidence`, which is the only field the two paths disagree about (0.70 live, 0.75
    heuristic).

    This is the same trap the Task 5 implementer hit independently, and the same reason
    `test_jev_features` is excluded suite-wide. A quality budget that depends on an external
    service is not a budget on this codebase.
    """
    import title_tag_engine
    global _JEV_PATCHER
    _JEV_PATCHER = mock.patch.object(title_tag_engine, "get_jev_api_key", return_value=None)
    _JEV_PATCHER.start()


def tearDownModule():
    """Stop the patch `setUpModule` started. Not `addModuleCleanup`, which is not public."""
    _JEV_PATCHER.stop()


class TestTheBudgetCanActuallyFail(unittest.TestCase):
    """
    Deliberately NOT decorated with @SKIP.

    Every corpus-dependent test in this file is skipped when `output/` cannot be found, which
    is correct -- there is nothing to measure. But it means a wrong path, a moved directory or
    an emptied `output/` turns the quality gate into `OK (skipped=14)`: green, while measuring
    nothing. That is worse than having no gate, because it is trusted.

    This was not hypothetical: pre-flighting this task staged the file outside the project,
    `HERE` resolved to the staging directory, every corpus-dependent test skipped, and the run
    reported `OK`.

    Two tests here cannot skip. This one checks the corpus was found. The other is
    `test_a_silent_clip_still_goes_through_the_engine`, which asserts on constructed inputs and
    so runs with no corpus at all. With an emptied `output/` the file reports
    `FAILED (failures=1, skipped=14)` -- it cannot go green while measuring nothing.
    """

    def test_the_corpus_was_actually_loaded(self):
        self.assertTrue(
            CLIPS,
            f"no clips loaded from {os.path.join(HERE, 'output')} -- the quality budget "
            f"would silently pass every other test in this file. Check that output/*.json "
            f"exists and that this file is being run from the project root.")
        self.assertGreaterEqual(len(CLIPS), 10,
                                f"only {len(CLIPS)} clips found; too few to be meaningful")


class TestGeneratedOutput(unittest.TestCase):
    """
    Judges what the engine produces RIGHT NOW. Every test in this class must be green;
    a failure means the ENGINE regressed.
    """

    def _meta(self, clip):
        """Generate for one clip using its VIDEO's shared context, as production does."""
        return generate_smart_title_and_hashtags(
            clip["transcript_snippet"],
            video_context=VIDEO_CONTEXTS[clip.get("video_id")])

    @SKIP
    def test_the_title_that_actually_ships_is_within_youtubes_limit(self):
        """
        The one genuinely new, non-tautological limit check in this file.

        A previous version asserted `validate_title(meta["suggested_title"])`, which cannot
        fail: `generate_seo_titles` calls `validate_title` on every candidate
        (title_seo.py:245) and drops the failures, and the chosen title IS one of the
        survivors. A mutant that widened the gate to reject "the" left the verdict
        byte-identical, because the engine just picked different templates.

        What the engine does not guarantee is the field that actually ships:
        `platform_metadata.youtube.title` is the chosen title with a " #shorts" suffix
        appended, so it is longer than anything the title gate ever saw and nothing checked
        it. The limit is pinned as a literal -- YouTube's 100 characters -- rather than read
        from `title_seo.MAX_TITLE_CHARS`, which the engine could raise and take the budget
        along with it (see rule 1 in the module docstring).
        """
        for clip in CLIPS:
            meta = self._meta(clip)
            shipped = meta["platform_metadata"]["youtube"]["title"]
            self.assertLessEqual(
                len(shipped), YOUTUBE_TITLE_LIMIT,
                f"{clip.get('filename')}: youtube.title is {len(shipped)} chars, over "
                f"YouTube's {YOUTUBE_TITLE_LIMIT}-char limit -- and it is the chosen title "
                f"plus a suffix, so the title gate never sees it. Value: {shipped!r}")

    @SKIP
    def test_the_chosen_title_is_one_of_the_validated_candidates(self):
        """
        The invariant that makes the limit check above meaningful.

        If the orchestrator ever chose a title that is not among the candidates it validated,
        the budget would be checking a string nothing else has seen. Stated directly, so it
        is not left implied -- and so that the tautological `validate_title(chosen)` assertion
        it replaces has somewhere to live other than duplicating it.
        """
        for clip in CLIPS:
            meta = self._meta(clip)
            candidates = meta.get("candidates") or []
            self.assertTrue(candidates, f"{clip.get('filename')}: no candidates returned")
            titles = [c["title"] for c in candidates]
            self.assertIn(meta["suggested_title"], titles,
                          f"{clip.get('filename')}: chosen title is not among the candidates")
            for t in titles:
                ok, reason = validate_title(t)
                self.assertTrue(ok, f"{clip.get('filename')}: candidate {t!r} -> {reason}")

    @SKIP
    def test_the_engine_agrees_with_the_limits_this_budget_pins(self):
        """
        Rule 1 made the limits literals; this is where the engine gets a chance to disagree.

        Deliberately an equality against the literals, not `assertLessEqual`, so that raising
        an engine constant is a FAILURE to be decided on rather than a silent widening of the
        budget. Both values are currently correct; the point is that they are now two
        independent statements that must be made to match.
        """
        self.assertEqual(MAX_TITLE_CHARS, YOUTUBE_TITLE_LIMIT,
                         "title_seo's own limit has drifted from the budget's pinned "
                         "YouTube limit -- decide which one is right")
        from hashtag_engine import MAX_HASHTAGS
        self.assertEqual(MAX_HASHTAGS, BUDGET_MAX_TAGS,
                         "hashtag_engine's own limit has drifted from the budget's pinned "
                         "tag budget -- decide which one is right")

    @SKIP
    def test_no_generated_title_uses_the_dead_fallback(self):
        """The 62% fallback is an ENGINE defect, so it is judged on generated titles."""
        offenders = [c.get("filename") for c in CLIPS
                     if self._meta(c)["suggested_title"].startswith(
                         "Why Nobody Tells You The Truth About")]
        self.assertEqual(offenders, [], f"{len(offenders)} generated titles use the dead template")

    @SKIP
    def test_generated_titles_are_not_all_identical(self):
        titles = [self._meta(c)["suggested_title"] for c in CLIPS]
        # Measured 0.93 against this 0.6, so the threshold is loose rather than fitted; it
        # would take a 36% drop in unique titles to fire.
        self.assertGreaterEqual(len(set(titles)) / max(len(titles), 1), 0.6,
                                "generated titles are too repetitive to be useful")

    @SKIP
    def test_every_clip_is_offered_several_candidates_to_rotate_through(self):
        """
        Per-clip rotation needs something to rotate through.

        With a single candidate the engine publishes the same title for every clip of a video
        and the distinctness budget above still passes, because it is measured across the
        whole corpus. Pinned at 3 against the 5 the engine actually builds, so it has margin in
        the right direction and cannot be satisfied by a niche with few patterns.
        """
        thin = []
        for clip in CLIPS:
            meta = self._meta(clip)
            if len(meta.get("candidates") or []) < BUDGET_MIN_CANDIDATES:
                thin.append((clip.get("filename"), len(meta.get("candidates") or [])))
        self.assertEqual(thin, [],
                         f"{len(thin)} clips were offered fewer than {BUDGET_MIN_CANDIDATES} "
                         f"title candidates, so per-clip rotation has nothing to rotate: {thin[:6]}")

    @SKIP
    def test_rotation_shifts_the_pattern_not_only_the_keyword(self):
        """
        The pre-Task-4 defect, which the collapse test below cannot see.

        Before Task 4 the engine rotated the keyword inside ONE pattern, so a clip could show
        "five different titles" that were all the same sentence shape. `rotation=0` and
        keyword-only rotation both leave at least two distinct titles per video, so the
        collapse test passes for both. The discriminator is the pattern identity: a rotated
        clip must be offered more than one framework.
        """
        single_pattern = []
        for clip in CLIPS:
            cands = self._meta(clip).get("candidates") or []
            frameworks = {c.get("framework") for c in cands}
            if len(frameworks) < 2:
                single_pattern.append((clip.get("filename"), sorted(map(str, frameworks))))
        self.assertEqual(
            single_pattern, [],
            f"{len(single_pattern)} clips were offered only one title pattern, so rotation is "
            f"only swapping keywords: {single_pattern[:6]}")

    @SKIP
    def test_the_youtube_description_is_built_from_the_title_and_tags(self):
        """
        Cheap, and it pins a field nothing else in this file touched.

        Mutating the description to an empty string survived every other assertion here, which
        is how a field that ships to YouTube can be quietly emptied.
        """
        for clip in CLIPS:
            meta = self._meta(clip)
            desc = meta["platform_metadata"]["youtube"]["description"]
            self.assertTrue(desc.strip(),
                            f"{clip.get('filename')}: youtube.description is empty")
            self.assertIn(meta["suggested_title"], desc,
                          f"{clip.get('filename')}: description does not contain the title")

    @SKIP
    def test_the_engine_used_the_context_the_caller_passed(self):
        """
        A SEAM check, and the only one that detects a TITLE-side per-clip context.
        DO NOT "FIX" THIS TEST. Read this before changing it.

        The assertion looks like a tautology and is not, but not for the reason a first
        reading suggests. `generate_seo_titles` builds its keyword list from the topics it is
        HANDED (title_seo.py:269) and assigns `keywords[i % len(keywords)]` (title_seo.py:291),
        so every candidate keyword is trivially a member of the topic list -- measured, 225 of
        225 on this corpus, 0 exceptions. Read as a claim about topic content, this test
        asserts nothing.

        What it actually asserts is that the topics the ENGINE used are the topics the CALLER
        passed: the right-hand side is `VIDEO_CONTEXTS`, built independently from the union of
        a video's snippets, and it is only equal to the engine's topics while the engine is
        reading the caller's context. Break the seam -- make `generate_candidate_titles`
        rebuild its context from the clip -- and the two diverge. That is the unpinned-seam
        defect this whole commit exists for.

        So the trap: someone spots the tautology, decides to recompute the topics from
        `topic_engine` "independently", and silently destroys the only detector for the defect
        the guard was written to catch. The name says what it checks for that reason.

        DELIBERATE CONTRACT CHANGE (2026-09-30, clip-specificity work). The right-hand
        side is now the video topics UNION the clip's own mined topics, because the engine
        now deliberately leads with clip specificity: `resolve_title_topics` merges the
        clip's usable topics (mined from the clip text the caller passed, or pre-attached
        as `clip_topics`) over the video pool, while the niche stays video-level. This is
        not the old defect returning: the old defect rebuilt the niche per clip (9/11
        videos disagreed); the niche still comes only from the caller's context, which
        `test_one_niche_per_video_is_exact` continues to pin. What changed is that a
        keyword may now legitimately come from the clip -- "The Proof that Chicken is
        Real" on a gym clip (video o1_FvfJD8fg) is exactly what this permits the engine
        to stop doing. The detector still works: any keyword from NEITHER pool means the
        engine went and found its own, and fails here.

        RESIDUAL COUPLING, stated plainly: the allowed set is computed with the
        production `build_clip_topics` itself, so a defect inside that miner (rather
        than in the engine's use of context) would pass this guard. That half is pinned
        instead by the `_is_usable_topic` and `build_clip_topics` unit tests in
        test_master_prompt.py. Two guards, two halves; neither covers the whole.
        """
        from title_master_prompt import build_clip_topics
        offenders = []
        for vid, clips in sorted(_by_video().items()):
            video_topics = {t.lower() for t in (VIDEO_CONTEXTS[vid].get("topics") or [])}
            niche = VIDEO_CONTEXTS[vid].get("niche") or "general_viral"
            for clip in clips:
                allowed = set(video_topics)
                allowed.update(
                    t.lower() for t in build_clip_topics(
                        clip.get("transcript_snippet") or "", niche))
                for cand in self._meta(clip).get("candidates") or []:
                    kw = (cand.get("keyword") or "").lower()
                    if kw not in allowed:
                        offenders.append((vid, kw, sorted(allowed)[:4]))
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} candidate keywords are in neither the caller-passed video "
            f"topics nor the clip's own mined topics, so the engine went and found its "
            f"own: {offenders[:6]}")

    @SKIP
    def test_clips_of_one_video_must_share_one_hashtag_set(self):
        """
        The parity property, and the only assertion here that detects the HASHTAG-side
        production call site reverting to per-clip extraction.

        Every other assertion in this file is an AGGREGATE over 45 clips -- distinctness ratio,
        tag count, `#Shorts` present, title validity -- and an aggregate cannot see a context
        change, because the per-clip output is individually just as valid as the shared-context
        output. It differs only *between clips of one video*. Measured on the real corpus:

            shared context (production) : 9 of 9 multi-clip videos have exactly ONE tag set
            per-clip context (mutant)   : 0 of 9

        Both are structural equalities rather than tuned ratios, and 0/9 is a wider margin
        than 9/9 needs. (An earlier draft of this docstring said "2 of 9" for the mutant; that
        figure was measured wrong and understated the result.)
        """
        disagreeing = {}
        for vid, clips in sorted(_by_video().items()):
            if len(clips) < 2:
                continue
            sets = {tuple(self._meta(c)["suggested_hashtags"]) for c in clips}
            if len(sets) != 1:
                disagreeing[vid] = sorted(sets)
        self.assertEqual(
            disagreeing, {},
            f"{len(disagreeing)} videos have clips with DIFFERENT hashtag sets, so the "
            f"shared video context is not reaching the engine: {disagreeing}")

    @SKIP
    def test_clips_of_one_video_do_not_all_collapse_to_one_title(self):
        """
        The other half of the contract: shared context, per-clip rotation.

        The context being shared is what makes the tag sets identical; the rotation being per
        clip is what stops every clip of a video publishing the same title. Losing the rotation
        is invisible to the aggregate assertions, because identical titles across the clips of
        one video still looks distinct measured over the whole 45-clip corpus. Measured:
        production gives 3-5 distinct top titles per multi-clip video, `rotation=0` gives 1 in
        all 9.
        """
        collapsed = {}
        for vid, clips in sorted(_by_video().items()):
            if len(clips) < 2:
                continue
            titles = {self._meta(c)["suggested_title"] for c in clips}
            if len(titles) < 2:
                collapsed[vid] = sorted(titles)
        self.assertEqual(
            collapsed, {},
            f"{len(collapsed)} videos have every clip on the SAME title, so per-clip "
            f"rotation is not reaching the engine: {collapsed}")

    @SKIP
    def test_generated_hashtags_are_short_and_carry_a_topic(self):
        """
        Task 5's output, budgeted, with the topic actually checked rather than "some tag
        exists": a single topic still leaves room for the niche's two tags, so a dropped topic
        is invisible to a non-emptiness check.

        The tag-count limit is pinned as a literal (see `_published_form` and rule 1). The
        topic predicate states the published form as a specification; the previous version
        modelled `_clean_tag` backwards and was false for all 30 multi-word topics in this
        corpus, so it was passing on a different, single-word topic.
        """
        checked = 0
        offenders = []
        thin = []
        for clip in CLIPS:
            ctx = VIDEO_CONTEXTS[clip.get("video_id")]
            meta = self._meta(clip)
            tags = meta["suggested_hashtags"]
            if len(tags) > BUDGET_MAX_TAGS:
                thin.append((clip.get("filename"), len(tags)))
            self.assertIn("#Shorts", tags, clip.get("filename"))
            topics = ctx.get("topics") or []
            if topics:
                checked += 1
                if not any(t == _published_form(topic) for t in tags for topic in topics):
                    offenders.append((clip.get("filename"), tags, topics[:3]))
        self.assertEqual(thin, [],
                         f"{len(thin)} clips exceeded the {BUDGET_MAX_TAGS}-tag budget: {thin[:6]}")
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} clips published no tag derived from the video's own topics: "
            f"{offenders[:4]}")
        # Anti-vacuity: without this, a corpus where every context lost its topics would pass
        # the loop above by never entering the `if topics:` body.
        self.assertGreater(checked, 0, "no clip had any topic, so the topic check was vacuous")

    @SKIP
    def test_metadata_tags_come_from_the_shared_builder(self):
        """
        A SEAM check on the MAIN path, and the gap its absence left open.

        `test_a_silent_clip_still_goes_through_the_engine` pins `youtube.tags` for 5 synthetic
        silent inputs. This does it for the 45 real clips -- the path 44 of 45 actually take.
        Reverting the main path to the hand-rolled `[t.lstrip('#') for t in ...]` strip that
        Task 5 review removed survived every other assertion in this file.

        It compares against `build_metadata_tags` on purpose: the claim is that the orchestrator
        uses the one shared builder, not that it computes the right value twice.
        """
        from hashtag_engine import build_metadata_tags
        wrong = []
        for clip in CLIPS:
            ctx = VIDEO_CONTEXTS[clip.get("video_id")]
            meta = self._meta(clip)
            expected = build_metadata_tags(
                meta["suggested_hashtags"], ctx.get("topics") or [], ctx.get("niche"))
            if meta["platform_metadata"]["youtube"]["tags"] != expected:
                wrong.append((clip.get("filename"),
                              meta["platform_metadata"]["youtube"]["tags"], expected))
        self.assertEqual(wrong, [],
                         f"{len(wrong)} clips have youtube.tags that did not come from "
                         f"build_metadata_tags: {wrong[:4]}")

    @SKIP
    def test_the_context_keeps_more_than_one_topic(self):
        """
        Stops topic mining from silently collapsing.

        A mutant that returns one topic per video instead of eight loses 87.5% of the topical
        signal, and nothing else here notices: the tag count still passes, the title is still
        valid, and the keyword seam check still holds because both sides shrank together.
        Only a floor on the topic count itself can see it.

        Stated as a floor rather than "at least 8" so it is not fitted to today's mining depth;
        it is anchored to the niche gate instead, which is where the real constraint lives.
        """
        starved = {}
        for vid, ctx in sorted(VIDEO_CONTEXTS.items()):
            union_len = sum(len(c["transcript_snippet"]) for c in _by_video()[vid])
            if union_len < 2000:
                continue
            n = len(ctx.get("topics") or [])
            if n < 2:
                starved[vid] = (n, union_len)
        self.assertEqual(
            starved, {},
            f"{len(starved)} videos with substantial transcripts yielded fewer than 2 topics, "
            f"so topical mining has collapsed: {starved}")


class TestSilentClips(unittest.TestCase):
    """
    The empty-transcript path, tested on constructed inputs.

    Split out from `TestGeneratedOutput` only for readability; every test in this file is
    green-or-red for the same reasons.
    """

    def test_a_silent_clip_still_goes_through_the_engine(self):
        """
        0 of the 45 real clips clean to an empty string (the shortest is 134 chars), so every
        corpus-dependent assertion in this file is blind to that branch. It is still reachable:
        "[Music]", "[Applause]", "[Laughter]", "(upbeat music)" and "[BLANK_AUDIO]" all clean
        to "" under both `title_tag_engine.clean_transcript_text` and
        `transcript_clean.clean_transcript`. And Task 5 review found that branch was running
        the OLD engine for two whole tasks -- a hardcoded five-tag pad, a hand-rolled
        `replace("#", "")` strip of `youtube.tags`, a hardcoded niche, and the shared
        `video_context` discarded.

        Deliberately NOT decorated with `@SKIP`: it asserts on constructed inputs, so it works
        whether or not a corpus is present, which is what stops the file going green while
        measuring nothing when `output/` is empty.

        KNOWN LIMIT, verified by mutation rather than assumed: this cannot tell "the silent
        branch behaves correctly" from "the silent branch was deleted outright" -- deleting it
        lets the main path handle the input, which honours the context just as well, and the
        test still passes. Closing that would mean asserting the branch exists, which is a
        source-shape check, not a behaviour one. The title assertions below do close the more
        costly version of the gap: padding the silent title past YouTube's limit used to
        survive, because this test checked no title at all.
        """
        from hashtag_engine import build_metadata_tags

        ctx = {"topics": ["sodium", "broth", "flavour"], "niche": "science_education",
               "niche_tags": ["#science"]}
        for text in ("[Music]", "[Applause]", "[Laughter]", "(upbeat music)",
                     "[BLANK_AUDIO]"):
            with self.subTest(text=text):
                meta = generate_smart_title_and_hashtags(text, video_context=ctx)
                tags = meta["suggested_hashtags"]
                self.assertLessEqual(len(tags), BUDGET_MAX_TAGS, f"{text}: too many tags")
                self.assertIn("#Shorts", tags, f"{text}: the reach tag was lost")
                self.assertEqual(meta["niche"], "science_education",
                                 f"{text}: the shared context niche was discarded")
                self.assertTrue(
                    any(t == _published_form(x) for t in tags for x in ctx["topics"]),
                    f"{text}: the context topics were discarded, got {tags}")
                # The title on this path, which nothing here checked before: the same
                # shipped-field limit as the main path, and the same candidate invariant.
                shipped = meta["platform_metadata"]["youtube"]["title"]
                self.assertLessEqual(len(shipped), YOUTUBE_TITLE_LIMIT,
                                     f"{text}: youtube.title is {len(shipped)} chars")
                cands = meta.get("candidates") or []
                self.assertTrue(cands, f"{text}: no candidates returned")
                self.assertIn(meta["suggested_title"], [c["title"] for c in cands],
                              f"{text}: chosen title is not among the candidates")
                self.assertEqual(
                    meta["platform_metadata"]["youtube"]["tags"],
                    build_metadata_tags(tags, ctx["topics"], "science_education"),
                    f"{text}: youtube.tags did not come from build_metadata_tags")


class TestTheBudgetsOwnFixture(unittest.TestCase):
    """
    Checks on the fixture the rest of the file depends on.

    These exist because a mutant that breaks `VIDEO_CONTEXTS` is invisible to everything else:
    the fixture is used consistently on BOTH sides of every other comparison, so a wrong
    fixture is self-consistently wrong. An earlier version of this file asserted that this was
    undetectable, on the grounds that comparing the cache against the union "would be
    circular". That was wrong, and the claim was also stated backwards -- a per-clip cache makes
    clips of a video DISAGREE, which is the opposite of what the comment said.

    It is not circular: `test_one_niche_per_video_is_exact` independently builds its own union
    context, so comparing that against the cache compares two independent derivations.
    """

    @SKIP
    def test_the_cache_is_the_union_of_a_video_not_one_of_its_clips(self):
        """
        Kills a cache that silently switched to `clips[0]`, which is a plausible "optimisation"
        and is what the pipeline must NOT do -- it is the pre-Task-3 extraction, restricted to
        one arbitrary clip.

        The union context is re-derived here from the corpus, independently of the cache, and
        compared on the two fields that differ between the two constructions.
        """
        mismatched = {}
        for vid, clips in sorted(_by_video().items()):
            union = " ".join(c["transcript_snippet"] for c in clips)
            if len(clips) < 2:
                continue
            independent = build_video_context(union)
            cached = VIDEO_CONTEXTS[vid]
            if (sorted(independent.get("topics") or [])
                    != sorted(cached.get("topics") or [])
                    or independent.get("niche") != cached.get("niche")):
                mismatched[vid] = {
                    "cached": (cached.get("niche"), cached.get("topics")),
                    "from_union": (independent.get("niche"), independent.get("topics")),
                }
        self.assertEqual(
            mismatched, {},
            f"{len(mismatched)} videos' cached context is not the union of that video's "
            f"snippets: {mismatched}")

    @SKIP
    def test_different_videos_do_not_share_one_context(self):
        """
        Kills a cache collapsed to a single global context.

        Eleven videos with eleven different topics would be a spectacular regression -- every
        Short would be titled about the same thing -- and with one global cache the parity
        assertions all still pass, because clips would agree with each other perfectly.
        """
        by_niche = collections.defaultdict(list)
        for vid, ctx in VIDEO_CONTEXTS.items():
            by_niche[ctx.get("niche")].append(vid)
        # `general_viral` is a legitimate landing spot for a minority of videos, so the check
        # is that the contexts are not ALL identical, which a single global cache guarantees.
        distinct = {tuple(sorted(ctx.get("topics") or [])) for ctx in VIDEO_CONTEXTS.values()}
        self.assertGreater(
            len(distinct), 1,
            f"every one of the {len(VIDEO_CONTEXTS)} videos has an identical topic set, so the "
            f"cache has collapsed to one global context: {by_niche}")


class TestStoredMetadata(unittest.TestCase):
    """
    Reads what is already on disk. These only pass once Task 8 has rewritten the corpus, so a
    failure before then means STALE METADATA, not a broken engine.
    """

    @SKIP
    def test_no_stored_title_uses_the_dead_fallback(self):
        """Baseline: 28 of 45 titles (62%) used this shape."""
        offenders = [c.get("filename") for c in CLIPS
                     if c.get("suggested_title", "").startswith(
                         "Why Nobody Tells You The Truth About")]
        self.assertEqual(offenders, [], f"{len(offenders)} clips still on the dead template")

    @SKIP
    def test_no_stored_bare_spoken_quote_titles(self):
        """
        Currently 1 of 45, not the 8 an earlier draft of the plan recorded as the baseline.

        The predicate is NARROWER than the name suggests, and deliberately so: a stored title
        that starts with a quote mark and ends with a digit or one of the emoji the generator
        appends. A bare spoken quote with no trailing emoji is not caught, so read this as
        "no stored title is a raw transcript quote *shaped like one*", not as a general
        quote detector. The single offender happens to end in an emoji, so today the test is
        red for the right reason -- but that is luck, not design, and a future offender without
        a trailing emoji would pass.
        """
        offenders = []
        for clip in CLIPS:
            title = clip.get("suggested_title", "")
            if title.startswith('"') and title.rstrip().endswith(
                    tuple("0123456789\U0001F92F\U0001F525\U0001F3AD\U0001F4A1\U0001F602")):
                offenders.append(clip.get("filename"))
        self.assertEqual(offenders, [], f"{len(offenders)} bare-quote titles remain")

    @SKIP
    def test_stored_titles_within_api_limit(self):
        for clip in CLIPS:
            self.assertLessEqual(len(clip.get("suggested_title", "")), YOUTUBE_TITLE_LIMIT,
                                 clip.get("filename"))

    @SKIP
    def test_stored_titles_are_not_all_identical(self):
        titles = [c.get("suggested_title", "") for c in CLIPS]
        self.assertGreaterEqual(len(set(titles)) / max(len(titles), 1), 0.6,
                                "stored titles are too repetitive to be useful")

    @SKIP
    def test_every_stored_clip_has_a_seo_topic(self):
        """The schema Task 3 added. Before the Task 3 backfill fix, 0 of 45 had it."""
        missing = [c.get("filename") for c in CLIPS if "seo_topic" not in c]
        self.assertEqual(missing, [],
                         f"{len(missing)} clips have no seo_topic key -- the backfill that "
                         f"adds it has not run, or is inert")


class TestCorpusVsEngine(unittest.TestCase):
    """
    Recomputes what the engine would say today and compares it to what is on disk.

    Deliberately its own class. This test was originally filed under `TestGeneratedOutput`,
    whose docstring promises every test is green -- and it is one of the four that are
    correctly red until Task 8 runs, so it made the report unreadable.
    """

    @SKIP
    def test_one_niche_per_video_is_exact(self):
        """
        Zero disagreements -- but only where a recomputation is actually POSSIBLE.

        Two kinds of clip end up in `output/`, carrying different kinds of evidence:

        * **Pipeline-written.** `app.py:101` builds the context from the FULL transcript and
          writes `seo_topic_source: "full_transcript"`. The transcript is not stored, so
          nothing on disk can reproduce that value.
        * **Backfilled.** `backfill_titles.py` builds it from the union of the stored 30 s
          snippets, and writes `seo_topic_source: "snippet_union"`.

        Comparing BOTH against a snippet-union recomputation is right for the backfilled clips
        -- and it is what caught the inert-backfill bug in the Task 3 review. It is wrong for
        the pipeline-written ones. On the first real pipeline clip it produced:

            stored    (from ~12 min of transcript) : niche='gaming'  seo_topic='chicken'
            recomputed(from one 30 s snippet)      : niche='general_viral'  seo_topic='form'

        which is not a pipeline defect. It is the test comparing a full-transcript value
        against a 30-second one, and the stored value is the better of the two.

        So `seo_topic_source` is the discriminator, written down rather than inferred. An
        earlier attempt branched on the PRESENCE of `seo_topic`, which looked equivalent and
        was not: the backfill writes that key too, so presence stopped distinguishing anything
        -- and the anti-vacuity assertion below caught exactly that, reporting that the test
        "asserted nothing". A guard that has quietly stopped guarding is worse than no guard.
        """
        pools = _by_video()
        self.assertGreaterEqual(len(pools), 5,
                                f"only {len(pools)} videos in the corpus; too few to judge")
        multi = {}
        compared = 0
        authoritative = 0
        unknown = 0
        for vid, clips in sorted(pools.items()):
            stored = {c.get("niche") for c in clips}
            if len(stored) > 1:
                multi[vid] = ("stored clips disagree", sorted(stored))
                continue
            sources = {c.get("seo_topic_source") for c in clips}
            if sources == {"full_transcript"}:
                # A producer saw the WHOLE transcript. Nothing on disk can recompute that,
                # because the full transcript is not stored -- and trying to is what made
                # this test wrong. Measured on the real clip: stored niche='gaming' mined
                # from ~12 min of speech, versus 'general_viral' from the 30 s window a
                # recomputation can see. The stored value is the BETTER of the two.
                authoritative += 1
                continue
            if "full_transcript" in sources:
                multi[vid] = ("clips disagree on seo_topic_source", sorted(map(str, sources)))
                continue
            if None in sources:
                # Absent means UNKNOWN, not snippet_union: a file written before the key
                # existed could have come from either producer, and pretending otherwise
                # would compare a full-transcript value against a 30 s one.
                unknown += 1
                continue
            union = " ".join(c["transcript_snippet"] for c in clips)
            by_video_niche = {build_video_context(union)["niche"]}
            compared += 1
            if by_video_niche != stored:
                multi[vid] = ("stored != recomputed", sorted(by_video_niche | stored))
        # Anti-vacuity. An earlier version branched on the PRESENCE of `seo_topic`, which
        # looked equivalent and was not -- the backfill writes that key too. This assertion is
        # what caught the result: "all 12 videos authoritative, so this test asserted
        # nothing", i.e. the guard had quietly stopped guarding.
        self.assertGreater(compared, 0,
                           f"no video was comparable: {authoritative} full-transcript, "
                           f"{unknown} unknown provenance, so this test asserted nothing")
        self.assertEqual(multi, {},
                         f"{len(multi)} videos fail niche parity ({compared} recomputed, "
                         f"{authoritative} full-transcript, {unknown} unknown): {multi}")


if __name__ == "__main__":
    unittest.main()
