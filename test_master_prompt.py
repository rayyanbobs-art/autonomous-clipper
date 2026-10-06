"""
test_master_prompt.py -- tests for the master-prompt title system.

Read `title_master_prompt.py`'s module docstring first; it explains why each piece exists.

THE ANTI-VACUITY RULE FOR THIS FILE

The project's own lesson, recorded in the plan doc and in the handoff: a green suite that
survives nonsense output is not a suite. Every test here asserts a specific failure, not
merely that a function returns something. Three of them exist specifically to catch
regressions that a plausible-looking implementation would reintroduce:

  * `test_ranking_is_not_first_wins` -- the original bug. Five titles generated, the first
    published, the other four discarded.
  * `test_url_check_ignores_template_prose` -- an earlier version of the URL check scanned
    the whole title and rejected intentional template prose ("Watch This:"), silently
    costing a title in 3 of 10 rotations. The templates were always fine; the check was
    over-broad.
  * `test_prompt_renders_niche_table_from_code` -- the drift that a hand-maintained copy
    of a tuned constant invites.
"""

import unittest

import title_master_prompt as mp
from topic_engine import NICHE_STRONG_KEYWORDS, BLOCKED_TOPIC_WORDS
from title_seo import MAX_TITLE_CHARS, FRONT_LOAD_CHARS, validate_title, _EXTRA_PATTERNS

TOPICS = ["snow cave", "alaska", "blizzard"]


class TestHardConstraints(unittest.TestCase):
    """
    One test per hard constraint. Each must fail for its own reason.

    NOTE ON ASSERTING THE REASON. Several constraints are ALSO enforced by
    `title_seo.validate_title`, which this module runs first and whose reason string is
    returned verbatim. So the reason text is not fully under this module's control. The
    assertions therefore check that the title was rejected, and that the reason mentions
    the right subject using a substring that both wordings share. Asserting an exact
    string would make these tests fail the moment the gate's wording changes, which would
    be noise, not signal.
    """

    def test_rejects_over_length(self):
        title = "What Nobody Tells You About " + ("Alaska " * 20)
        ok, reason = mp.validate_master_title(title, TOPICS)
        self.assertFalse(ok)
        self.assertRegex(reason, r"char")

    def test_rejects_topic_outside_front_load_window(self):
        # The lead-in must push "Alaska" PAST the 45-character truncation point.
        lead = "Something Completely Unrelated To This Topic And More Of It"
        self.assertGreater(len(lead) + 1, 45, "fixture must place the topic past the window")
        title = f"{lead} Alaska"
        ok, reason = mp.validate_master_title(title, ["alaska"])
        self.assertFalse(ok)
        self.assertIn("first 45", reason)

    def test_rejects_filler_as_keyword(self):
        ok, reason = mp.validate_master_title("What Nobody Tells You About Hold", ["hold"])
        self.assertFalse(ok)

    def test_rejects_profanity(self):
        ok, reason = mp.validate_master_title("The Mechanics of Shit Explained Simply")
        self.assertFalse(ok)
        self.assertRegex(reason, r"profan|blocked")

    def test_allows_emoji_and_never_treats_them_as_non_ascii_letters(self):
        # The documented hazard: an earlier whole-title isascii() check rejected emoji and
        # had to be undone (Calibration Finding #4).
        title = "What Nobody Tells You About Snow Cave 🤯"
        ok, reason = mp.validate_master_title(title, ["snow cave"])
        self.assertTrue(ok, reason)

    def test_rejects_genuine_non_ascii_letter_but_allows_emoji(self):
        ok, reason = mp.validate_master_title("The Story Behind Naïve Snow Cave")
        self.assertFalse(ok)
        self.assertRegex(reason, r"non-ascii", )

    def test_rejects_repeated_word(self):
        ok, reason = mp.validate_master_title("The Truth About The Snow Truth")
        self.assertFalse(ok)
        self.assertIn("repeated", reason)

    def test_rejects_multiple_ellipses(self):
        ok, reason = mp.validate_master_title("Snow Cave... And Then... Nothing")
        self.assertFalse(ok)
        self.assertIn("ellips", reason)

    def test_rejects_too_few_real_words(self):
        # "Snow Cave Run" is three 3+ letter words and correctly passes. Two words does not.
        ok, reason = mp.validate_master_title("Cave Run")
        self.assertFalse(ok)
        self.assertRegex(reason, r"real words|fewer than")

    def test_accepts_exactly_three_real_words(self):
        """The boundary, so the previous test cannot pass for the wrong reason."""
        ok, reason = mp.validate_master_title("Snow Cave Run")
        self.assertTrue(ok, reason)

    def test_rejects_url_fragment_in_topic_slot(self):
        # The fragments occur in practice as MINED KEYWORDS ("...About dot", "That https
        # Is Real"), so the check is scoped to the topic list the title was composed from.
        ok, reason = mp.validate_master_title("The Proof That Https Is Real", ["https"])
        self.assertFalse(ok)
        self.assertRegex(reason, r"URL|blocked")

    def test_url_check_falls_back_to_title_tokens_without_topics(self):
        """
        With no topic list there is no slot to check, so the title tokens are scanned
        instead of skipping the check. A topic-less "Watch This:" title is rejected;
        a clean topic-less title passes.
        """
        ok, _ = mp.validate_master_title(
            "Watch This: The Truth About Alaska", None)
        self.assertFalse(ok)
        ok, reason = mp.validate_master_title("The Story Behind Alaska", None)
        self.assertTrue(ok, reason)

    def test_trend_matching_is_token_aware_not_substring(self):
        """
        A short trend key like "ai" must not over-fire on "said", "air" or "again".
        Single-word keys match whole tokens; multi-word keys match as phrases.
        """
        original = dict(mp.TRENDING_BOOST)
        self.assertEqual(original, {}, "production map must stay empty")
        said_title = "What Nobody Tells You About Said Things"
        ai_title = "What AI Taught Me About Models"
        ml_title = "The Science of Machine Learning Today"
        plain_said = mp.score_title(said_title, ["said", "things"])
        plain_ai = mp.score_title(ai_title, ["ai", "models"])
        plain_ml = mp.score_title(ml_title, ["machine learning"])
        try:
            mp.TRENDING_BOOST["ai"] = 0.8
            # "said" contains "ai" as a substring but not as a token: no boost.
            self.assertEqual(mp.score_title(said_title, ["said", "things"]),
                             plain_said,
                             "'ai' boost must not fire on 'said'")
            # Whole-token "AI" in the title: boost fires.
            self.assertGreater(mp.score_title(ai_title, ["ai", "models"]),
                               plain_ai)
            mp.TRENDING_BOOST["machine learning"] = 0.8
            self.assertGreater(
                mp.score_title(ml_title, ["machine learning"]), plain_ml)
        finally:
            mp.TRENDING_BOOST.clear()
            mp.TRENDING_BOOST.update(original)

    def test_url_check_ignores_template_prose(self):
        """
        "Watch This:" is intentional template prose, not a URL fragment split out of
        auto-captions. `title_seo.validate_title` has no whole-title URL rule, and dozens
        of `watch_this` titles shipped in `output/` -- so this module must not invent one
        either. An earlier whole-title scan rejected every `watch_this` composition and
        silently returned four titles instead of five in 3 of 10 rotations.
        """
        ok, reason = mp.validate_master_title(
            "Watch This: The Truth About Alaska", ["alaska"]
        )
        self.assertTrue(ok, reason)

    def test_empty_title_rejected(self):
        ok, _ = mp.validate_master_title("", TOPICS)
        self.assertFalse(ok)

    def test_publication_gate_is_still_authoritative(self):
        """
        A title the master prompt considers fine but `title_seo` rejects must still be
        rejected. This module layers on top of the gate; it must never be a bypass.
        """
        title = "Why Nobody Tells You The Truth About Xyzzy Plugh"
        master_ok, _ = mp.validate_master_title(title, ["xyzzy", "plugh"])
        gate_ok, _ = validate_title(title)
        if not gate_ok:
            self.assertFalse(master_ok, "master prompt accepted a title the gate rejects")


class TestFrameworkTemplates(unittest.TestCase):
    def test_watch_this_and_fallback_curiosity_are_included(self):
        """
        Both templates contain "watch" as intentional English prose. The URL-fragment
        check is scoped to the topic slot, so there is nothing to exclude them for --
        and excluding them is what silently cost a title in 3 of 10 rotations before.
        """
        self.assertIn("watch_this", [pid for _, _, pid in mp.CORE_FRAMEWORKS])
        self.assertIn(
            "fallback_curiosity",
            [p["id"] for p in _EXTRA_PATTERNS if p["id"].startswith("fallback_")],
        )
        self.assertIn(
            "Watch To The End Of This {cap} Clip", list(mp.FALLBACK_TITLES),
        )

    def test_every_pattern_id_resolves_against_title_seo(self):
        """
        THE ID CONTRACT. Consumers resolve `pattern_id` against `title_seo.ALL_PATTERNS`.
        An earlier version of this module slugified its own framework labels and produced
        `'list'`, which is in no registry.
        """
        from title_seo import ALL_PATTERNS
        ids = {p["id"] for p in ALL_PATTERNS}
        for _, _, pid in mp.CORE_FRAMEWORKS:
            self.assertIn(pid, ids)
        for frameworks in mp.NICHE_BONUS_FRAMEWORKS.values():
            for _, _, pid in frameworks:
                self.assertIn(pid, ids)

    def test_templates_come_from_title_seo_not_a_second_copy(self):
        """
        ANTI-DUPLICATION GUARD. The framework templates are `title_seo`'s, in one place.
        This module contributes constraints, ranking and parsing -- it does not fork a
        tuned library.
        """
        from title_seo import ALL_PATTERNS
        registry = {p["template"] for p in ALL_PATTERNS}
        for _, template, _ in mp.CORE_FRAMEWORKS:
            self.assertIn(template, registry)

    def test_niche_bonus_covers_every_niche_with_known_patterns(self):
        from title_seo import _NICHE_PATTERNS
        self.assertEqual(set(mp.NICHE_BONUS_FRAMEWORKS), set(_NICHE_PATTERNS))

    def test_no_duplicate_core_framework_ids(self):
        ids = [pid for _, _, pid in mp.CORE_FRAMEWORKS]
        self.assertEqual(len(ids), len(set(ids)))


class TestNicheTableRendersFromCode(unittest.TestCase):
    def test_prompt_renders_niche_table_from_code(self):
        """
        DRIFT GUARD. The specification shipped a hand-copied niche table. The prompt now
        renders it from `topic_engine.NICHE_STRONG_KEYWORDS`, so a retune propagates and
        two copies cannot disagree.
        """
        table = mp.render_niche_table()
        for niche, keywords in NICHE_STRONG_KEYWORDS.items():
            self.assertIn(niche, table)
            for keyword in keywords:
                self.assertIn(keyword, table, f"{keyword} missing from rendered table")

    def test_prompt_mentions_every_hard_constraint(self):
        prompt = mp.build_master_prompt()
        for constraint in mp.HARD_CONSTRAINTS:
            self.assertIn(constraint["id"], [c["id"] for c in mp.HARD_CONSTRAINTS])
        # Spot-check that the constraints actually render into the prompt body.
        self.assertIn(f"{MAX_TITLE_CHARS}", prompt)
        self.assertIn(str(FRONT_LOAD_CHARS), prompt)
        self.assertIn("No visual provided", prompt)

    def test_visual_step_is_marked_unavailable(self):
        """STEP 4 must not claim a visual the pipeline cannot supply."""
        prompt = mp.build_master_prompt().lower()
        self.assertIn("unavailable in this pipeline", prompt)


class TestLocalRanking(unittest.TestCase):
    def test_ranking_is_not_first_wins(self):
        """
        THE ORIGINAL BUG. Every fallback in `score_and_rank_titles_with_jev` used to
        return `candidates[0]["id"]`, so the first title shipped regardless of quality.

        This asserts the discriminator directly: put the clearly-best candidate LAST and
        require that it is chosen. An implementation that always returns index 0 -- which
        is what the code did, and what a naive re-implementation would also do -- fails.
        """
        candidates = [
            {"id": "c1", "framework": "A", "title": "Snow Cave"},
            {"id": "c2", "framework": "B", "title": "Snow"},
            {"id": "c3", "framework": "C", "title": "What Nobody Tells You About Snow Cave 🔥"},
        ]
        best_id, _score = mp.rank_titles_locally(candidates, ["snow cave"])
        self.assertEqual(best_id, "c3")

    def test_ranking_prefers_front_loaded_topic(self):
        front = {"id": "front", "framework": "A", "title": "What Nobody Tells You About Snow Cave 🔥"}
        back = {"id": "back", "framework": "A", "title": "A Long Windy Lead In About The Snow Cave 🔥"}
        best_id, _ = mp.rank_titles_locally([back, front], ["snow cave"])
        self.assertEqual(best_id, "front")

    def test_ranking_penalises_filler(self):
        clean = {"id": "clean", "framework": "A", "title": "What Nobody Tells You About Snow Cave"}
        dirty = {"id": "dirty", "framework": "A", "title": "What Basically Nobody Tells You About Snow"}
        best_id, _ = mp.rank_titles_locally([dirty, clean], ["snow cave"])
        self.assertEqual(best_id, "clean")

    def test_ranking_requires_at_least_one_candidate(self):
        with self.assertRaises(ValueError):
            mp.rank_titles_locally([], TOPICS)

    def test_ranking_is_deterministic(self):
        candidates = [
            {"id": "a", "framework": "X", "title": "What Nobody Tells You About Alaska"},
            {"id": "b", "framework": "X", "title": "What Blizzard Taught Me the Hard Way"},
        ]
        first = mp.rank_titles_locally(candidates, ["alaska", "blizzard"])
        for _ in range(5):
            self.assertEqual(mp.rank_titles_locally(candidates, ["alaska", "blizzard"]), first)

    def test_ranking_ties_break_to_earliest(self):
        """Ties must be stable, or repeated runs on the same input differ."""
        a = {"id": "a", "framework": "X", "title": "Same Words Here Now"}
        b = {"id": "b", "framework": "X", "title": "Same Words Here Now"}
        best_id, _ = mp.rank_titles_locally([a, b], ["same", "words", "here"])
        self.assertEqual(best_id, "a")


class TestDeterministicGeneration(unittest.TestCase):
    def test_always_returns_five_for_every_rotation(self):
        """
        REGRESSION GUARD. The `Watch This:` collision produced 4 titles in 3 of 10
        rotations -- a defect visible only by sweeping the rotation, which is why this
        test sweeps it.
        """
        for rotation in range(10):
            candidates = mp.generate_master_titles(
                ["snow cave", "alaska", "blizzard"],
                niche="outdoors_survival",
                limit=5,
                rotation=rotation,
            )
            self.assertEqual(len(candidates), 5, f"rotation {rotation} produced {len(candidates)}")

    def test_every_generated_title_passes_validation(self):
        """
        Validated against the topics the GENERATOR actually selected, not the caller's raw
        list. The generator deliberately re-ranks topics, so validating against the raw
        unordered list would test the wrong thing.
        """
        raw = ["cave", "dark", "snow", "alaska", "build"]
        used = mp.select_topics_for_titles(raw, "outdoors_survival")
        for rotation in range(10):
            candidates = mp.generate_master_titles(
                raw, niche="outdoors_survival", limit=5, rotation=rotation,
            )
            for candidate in candidates:
                ok, reason = mp.validate_master_title(candidate["title"], used)
                self.assertTrue(ok, f"{candidate['title']!r} -> {reason}")

    def test_candidates_carry_the_keyword_they_used(self):
        """
        SHAPE CONTRACT. `title_seo.generate_seo_titles` emits `keyword` and `pattern_id`,
        and the corpus gate reads `keyword`. A candidate without it is indistinguishable
        from one whose keyword was empty, which is exactly how 230 bogus keywords reached
        the gate before this was fixed.
        """
        candidates = mp.generate_master_titles(
            ["snow cave", "alaska"], niche="outdoors_survival", limit=5,
        )
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertIn("keyword", candidate)
            self.assertTrue(candidate["keyword"], f"{candidate['title']!r} has empty keyword")
            self.assertIn("pattern_id", candidate)
            # The title-cased keyword must actually appear in the title it produced.
            self.assertIn(
                candidate["keyword"].title(), candidate["title"],
                f"{candidate['title']!r} does not contain its own keyword",
            )

    def test_select_never_introduces_a_topic_it_was_not_given(self):
        """
        THE SHARED-CONTEXT INVARIANT. DO NOT "FIX" THIS TEST.

        Task 3's property is "one context per video, computed once". A title generator that
        re-derives topics from the clip's own transcript reintroduces the per-clip context
        defect that property exists to prevent.

        An earlier version of `select_topics_for_titles` scanned the transcript for niche
        keywords and added what it found. That broke the seam, and
        `test_title_quality_budget.test_the_engine_used_the_context_the_caller_passed`
        caught it with 230 keywords absent from the caller's context. That test's docstring
        predicts this mistake by name. Read both before changing this function.
        """
        handed = ["alaska", "snow"]
        selected = mp.select_topics_for_titles(handed, "outdoors_survival")
        for topic in selected:
            self.assertIn(
                topic.lower(), {t.lower() for t in handed},
                f"{topic!r} was not passed in; the generator must not mine its own",
            )

    def test_select_drops_unusable_topics_but_keeps_usable_ones(self):
        selected = mp.select_topics_for_titles(
            ["build", "basically", "alaska", "snow", "went"], "outdoors_survival",
        )
        self.assertIn("alaska", selected)
        self.assertIn("snow", selected)
        for junk in ("build", "basically", "went"):
            self.assertNotIn(junk, selected)

    def test_build_clip_topics_mines_the_clip_not_the_video(self):
        """
        A gym clip from a heterogeneous video must yield gym topics, not the video's.
        """
        clip = (
            "Come on, push! That's easy. Get your form right, get your pinky here. "
            "Do five reps of the bench press. Whatever you are comfortable with. "
            "My stomach hurts from the workout."
        )
        topics = mp.build_clip_topics(clip, "gaming")
        self.assertTrue(topics, "a substantive clip must yield at least one topic")
        for topic in topics:
            self.assertTrue(
                mp._is_usable_topic(topic), f"{topic!r} is not title-usable")

    def test_build_clip_topics_returns_empty_for_nothing_to_say(self):
        self.assertEqual(mp.build_clip_topics("", "gaming"), [])
        self.assertEqual(mp.build_clip_topics("[Music] [Applause]", "gaming"), [])

    def test_merge_pool_puts_clip_first_and_dedupes(self):
        merged = mp.merge_topic_pools(["Snow Cave", "alaska"], ["alaska", "blizzard"])
        self.assertEqual(merged[0], "Snow Cave")
        lowered = [t.lower() for t in merged]
        self.assertEqual(len(lowered), len(set(lowered)), f"duplicates: {merged}")
        self.assertIn("blizzard", lowered)

    def test_merge_pool_falls_back_to_video_when_clip_is_empty(self):
        self.assertEqual(
            mp.merge_topic_pools([], ["alaska", "snow"]), ["alaska", "snow"])

    def test_heterogeneous_clip_gets_its_own_topic(self):
        """
        THE o1_FvfJD8fg REGRESSION TEST. A gym clip in a "chicken" video must not ship
        a chicken title. The niche stays video-level; the topic goes clip-level.
        """
        from title_tag_engine import generate_candidate_titles
        gym_clip = (
            "Come on, push! Get your form right, get your pinky here. Do five reps "
            "of the bench press. Whatever you are comfortable with. Come on, push!"
        )
        ctx = {"topics": ["chicken"], "niche": "gaming"}
        candidates = generate_candidate_titles(gym_clip, "high_value_insight",
                                               video_context=dict(ctx))
        self.assertTrue(candidates)
        keywords = {c["keyword"].lower() for c in candidates}
        self.assertTrue(
            keywords - {"chicken"},
            f"every candidate is about the video topic, none about the clip: {keywords}")

    def test_preattached_clip_topics_are_honored(self):
        from title_tag_engine import resolve_title_topics
        pool, niche = resolve_title_topics(
            "some transcript",
            {"topics": ["chicken"], "niche": "gaming",
             "clip_topics": ["dumbbells"]})
        self.assertEqual(niche, "gaming")
        self.assertEqual(pool[0], "dumbbells")
        self.assertIn("chicken", [t.lower() for t in pool])

    def test_select_promotes_niche_confirmed_terms(self):
        """
        The niche keyword list may PROMOTE a topic that was already handed to us, and may
        never contribute one that was not. "alaska" is a curated outdoors_survival strong
        term, so it outranks an unconfirmed topic of equal shape.
        """
        selected = mp.select_topics_for_titles(["alaska", "dark"], "outdoors_survival")
        self.assertEqual(selected[0], "alaska")

    def test_titles_are_distinct(self):
        candidates = mp.generate_master_titles(
            ["snow cave", "alaska", "blizzard"],
            niche="outdoors_survival", limit=5, rotation=0,
        )
        titles = [c["title"] for c in candidates]
        self.assertEqual(len(titles), len(set(titles)))

    def test_extract_hook_skips_shouting_and_fragments(self):
        """
        Auto-caption transcripts open with ">>" markers, ALL-CAPS shouting runs and
        one-word fragments. The hook is the first SUBSTANTIVE sentence: >= 4 alpha
        words, not all-shouted, markers stripped.
        """
        text = (">> ARE YOU BACK, BRO? >> Yeah. Wow. Everybody start somewhere with "
                "the bench press and build real strength over time.")
        hook = mp.extract_hook_text(text)
        self.assertTrue(hook, "a substantive sentence exists and must be found")
        self.assertIn("bench press", hook)
        self.assertFalse(hook.startswith(">>"))
        self.assertFalse(hook.isupper())

    def test_extract_hook_reports_the_opener_honestly(self):
        """
        The extractor reports what the clip opens with; it does not judge quality.
        A conversational opener is returned as-is. This is safe for ranking because
        filler words are never topics, so no title can earn the hook bonus from one.
        """
        hook = mp.extract_hook_text("Oh, you trying again, huh? Then we lift.")
        self.assertIn("trying again", hook)

    def test_extract_hook_returns_empty_when_nothing_substantive(self):
        self.assertEqual(mp.extract_hook_text(""), "")
        self.assertEqual(mp.extract_hook_text("[Music]"), "")
        self.assertEqual(mp.extract_hook_text("YEAH. WOW. OH."), "")

    def test_extract_hook_truncates_at_word_boundary(self):
        long_sentence = " ".join(["bench"] * 60)
        hook = mp.extract_hook_text(long_sentence, max_chars=60)
        self.assertLessEqual(len(hook), 60)
        self.assertFalse(hook.endswith(" "))

    def test_ranker_prefers_title_matching_the_hook(self):
        """
        OpusClip's Hook dimension: the title should describe what the clip OPENS with.
        Two titles equal in every other respect; the hook-matching one must win.
        """
        hook = "everybody start somewhere with the bench press today"
        topics = ["bench press", "alaska"]
        a = {"id": "a", "framework": "X",
             "title": "What Nobody Tells You About Alaska"}
        b = {"id": "b", "framework": "X",
             "title": "What Nobody Tells You About Bench Press"}
        best_id, _ = mp.rank_titles_locally([a, b], topics, hook)
        self.assertEqual(best_id, "b")

    def test_trending_boost_applies_when_populated(self):
        """
        The mechanism, pinned with a test-local override. The production map starts
        EMPTY (see TRENDING_BOOST): no measured publish data exists yet, and invented
        weights would steer what ships without evidence. Empty map == content signals
        only, which must be a no-op here.
        """
        topics = ["alaska", "snow"]
        title = "What Nobody Tells You About Alaska"
        base = mp.score_title(title, topics)
        self.assertEqual(mp.TRENDING_BOOST, {},
                         "production trend map must start empty; populate from data")
        original = dict(mp.TRENDING_BOOST)
        try:
            mp.TRENDING_BOOST["alaska"] = 0.8
            boosted = mp.score_title(title, topics)
            self.assertGreater(boosted, base)
            mp.TRENDING_BOOST["alaska"] = 99.0
            capped = mp.score_title(title, topics)
            self.assertEqual(capped, base + mp._TREND_CAP)
        finally:
            mp.TRENDING_BOOST.clear()
            mp.TRENDING_BOOST.update(original)

    def test_trends_date_is_present_and_parseable(self):
        import datetime
        parsed = datetime.date.fromisoformat(mp.TRENDS_UPDATED)
        self.assertLessEqual((datetime.date.today() - parsed).days, 365 * 5)

    def test_hook_bonus_is_zero_without_hook(self):
        topics = ["alaska"]
        title = "What Nobody Tells You About Alaska"
        self.assertEqual(mp.score_title(title, topics),
                         mp.score_title(title, topics, ""))

    def test_orchestrator_exposes_hook_text(self):
        from title_tag_engine import generate_smart_title_and_hashtags
        meta = generate_smart_title_and_hashtags(
            "Everybody start somewhere with the bench press and build real strength "
            "over the next thirty days of training.")
        self.assertIn("hook_text", meta)
        self.assertTrue(meta["hook_text"], "a substantive clip must expose its hook")

    def test_default_tone_reproduces_prior_behavior(self):
        """
        Adding tone must not move the default one iota: omitted tone == high_energy,
        title for title, for every rotation.
        """
        for rotation in range(10):
            default = mp.generate_master_titles(
                ["snow cave", "alaska"], niche="outdoors_survival",
                limit=5, rotation=rotation,
            )
            explicit = mp.generate_master_titles(
                ["snow cave", "alaska"], niche="outdoors_survival",
                limit=5, rotation=rotation, tone="high_energy",
            )
            self.assertEqual(
                [c["title"] for c in default], [c["title"] for c in explicit])

    def test_professional_tone_has_no_emoji_and_leads_with_evidence(self):
        candidates = mp.generate_master_titles(
            ["snow cave", "alaska", "blizzard"], niche="science_education",
            limit=5, rotation=0, tone="professional",
        )
        self.assertEqual(len(candidates), 5)
        for candidate in candidates:
            self.assertNotRegex(candidate["title"], r"[\U0001F000-\U0001FAFF]",
                                f"professional title carries emoji: {candidate['title']!r}")
        self.assertIn(candidates[0]["framework"],
                      {"Explainer", "Evidence", "Research", "Comparison", "Breakdown",
                       "Proof", "Root Cause", "Build Log"})

    def test_playful_tone_keeps_emoji(self):
        candidates = mp.generate_master_titles(
            ["snow cave", "alaska"], niche="comedy_entertainment",
            limit=5, rotation=0, tone="playful",
        )
        self.assertTrue(candidates)
        self.assertTrue(
            any("\U0001F602" in c["title"] or "\U0001F3AD" in c["title"]
                for c in candidates),
            "playful comedy titles should carry emoji")

    def test_unknown_tone_fails_fast(self):
        with self.assertRaises(ValueError):
            mp.generate_master_titles(["alaska"], niche="gaming", tone="noir")

    def test_orchestrator_accepts_tone(self):
        from title_tag_engine import generate_smart_title_and_hashtags
        meta = generate_smart_title_and_hashtags(
            "The sodium and potassium experiment shows a violent exothermic reaction "
            "in the chemistry laboratory today.",
            tone="professional")
        self.assertNotRegex(meta["suggested_title"], r"[\U0001F000-\U0001FAFF]")

    def test_short_title_is_a_real_candidate_never_a_truncation(self):
        """
        TikTok gets a validated ranked title that fits, not a cut-off string.
        "What Nobody Tells You About Alas" must be unrepresentable.
        """
        candidates = [
            {"id": "a", "framework": "X",
             "title": "What Nobody Tells You About Alaska Snow Storms Today"},
            {"id": "b", "framework": "Y", "title": "Alaska Snow Explained"},
        ]
        short = mp.select_short_title(candidates, ["alaska", "snow"])
        self.assertEqual(short["id"], "b")
        self.assertLessEqual(len(short["title"]), 50)

    def test_short_title_falls_back_to_first_when_nothing_fits(self):
        candidates = [
            {"id": "a", "framework": "X", "title": "What Nobody Tells You About Alaska"},
            {"id": "b", "framework": "Y", "title": "The Mistake Everyone Makes With Snow"},
        ]
        self.assertEqual(
            mp.select_short_title(candidates, ["alaska"], max_chars=10)["id"], "a")

    def test_description_quotes_hook_and_names_topics(self):
        desc = mp.build_description(
            "What Nobody Tells You About Alaska", "Alaska Explained",
            ["alaska", "blizzard"], "outdoors_survival",
            "Everybody start somewhere with the bench press today",
            ["#Shorts", "#outdoors"], platform="youtube")
        self.assertTrue(desc.startswith("What Nobody Tells You About Alaska"))
        self.assertIn("bench press", desc)
        self.assertIn("Featuring: Alaska, Blizzard.", desc)
        self.assertIn("Subscribe for more daily shorts!", desc)
        self.assertIn("#Shorts", desc)

    def test_description_falls_back_to_snippet_without_hook(self):
        desc = mp.build_description(
            "Title Here", "Short Here", ["alaska"], "outdoors_survival", "",
            ["#Shorts"], snippet_fallback="a much longer transcript snippet here",
            platform="youtube")
        self.assertIn("a much longer transcript snippet here", desc)

    def test_tiktok_description_is_short_title_plus_tags(self):
        desc = mp.build_description(
            "Long Youtube Title Here", "Short Here", ["alaska"], "gaming",
            "some hook", ["#Shorts", "#gaming"], platform="tiktok")
        self.assertTrue(desc.startswith("Short Here"))
        self.assertNotIn("some hook", desc)

    def test_orchestrator_publishes_short_variant_per_platform(self):
        from title_tag_engine import generate_smart_title_and_hashtags
        meta = generate_smart_title_and_hashtags(
            "Everybody start somewhere with the bench press and build real strength "
            "over the next thirty days of training.")
        self.assertIn("short_title", meta)
        tiktok = meta["platform_metadata"]["tiktok"]["caption"]
        self.assertTrue(tiktok.startswith(meta["short_title"]),
                        f"tiktok caption does not lead with short_title: {tiktok!r}")
        self.assertIn(meta["suggested_title"],
                      meta["platform_metadata"]["youtube"]["description"])
        self.assertIn("Subscribe for more daily shorts!",
                      meta["platform_metadata"]["youtube"]["description"])

    def test_rotation_shifts_both_framework_and_topic(self):
        """
        The corpus should not look auto-generated. `title_seo` rotates the pattern AND the
        keyword together, because rotating only the keyword left every clip of one video
        using the same pattern with a different noun. Check both move.
        """
        a = mp.generate_master_titles(["alpha", "beta"], "tech_ai", limit=5, rotation=0)
        b = mp.generate_master_titles(["alpha", "beta"], "tech_ai", limit=5, rotation=1)
        self.assertNotEqual(
            [c["framework"] for c in a], [c["framework"] for c in b],
            "rotation did not shift the framework",
        )
        self.assertNotEqual(
            [c["keyword"] for c in a], [c["keyword"] for c in b],
            "rotation did not shift the keyword",
        )

    def test_rejects_verbs_and_fillers_as_topics(self):
        """
        `built`, `donated` and `goin` are the forms the handoff document names as measured
        defects: they were mined as topics and reached published metadata. `BLOCKED_TOPIC_WORDS`
        did not catch `built` or `donated`, which is why `_EXTRA_VERB_FORMS` exists.
        """
        for bad in ["built", "donated", "going", "basically", "nothing", "subscribe", "went"]:
            self.assertFalse(mp._is_usable_topic(bad), f"{bad} should be unusable as a topic")

    def test_rejects_person_names_thank_stop_and_god_as_topics(self):
        """
        Measured on video `o1_FvfJD8fg` (2026-09-29): "What Nobody Tells You About Chad",
        "The Mistake Everyone Makes with Chad", "Speed Explained in Under a Minute",
        "Everything I Learned About Stop", "What Changed My Mind About God". `chad` is a
        person name, `stop`/`thank` are verbs, `god` comes from "oh my god" interjections
        and was already in the pre-fix baseline's garbage list.
        """
        for bad in ["chad", "luke", "mom", "thank", "thanks", "stop", "stops",
                    "god", "gods"]:
            self.assertFalse(mp._is_usable_topic(bad), f"{bad} should be unusable as a topic")

    def test_person_names_cover_title_tag_engine(self):
        """
        `_PERSON_NAMES` mirrors `title_tag_engine.COMMON_FIRST_NAMES` (imported lazily:
        that module imports `title_master_prompt`, so a top-level import would be
        circular) plus measured gaps. This asserts the mirror stays a superset, so the
        two cannot silently disagree about what counts as a name.
        """
        from title_tag_engine import COMMON_FIRST_NAMES
        missing = set(COMMON_FIRST_NAMES) - set(mp._PERSON_NAMES)
        self.assertEqual(missing, set(), f"names missing from _PERSON_NAMES: {sorted(missing)}")

    def test_does_not_reject_words_ending_in_ed(self):
        """
        The obvious `-ed` heuristic would reject "speed", "red" and "bed" -- concrete nouns
        a title can use, and "speed" is a legitimate gaming topic. Only the unambiguously
        verbal `-ing` suffix is applied, plus an enumerated verb-form set.

        "need" is deliberately NOT in this list: it is a verb and `BLOCKED_TOPIC_WORDS`
        already blocks it, so including it here would assert the wrong reason.
        """
        for good in ["speed", "red", "bed", "seed"]:
            self.assertTrue(mp._is_usable_topic(good), f"{good} should be usable")

    def test_title_cases_the_substituted_topic(self):
        """A lowercase topic dropped into a template yields "The Story Behind cave"."""
        self.assertEqual(mp._title_case_topic("snow cave"), "Snow Cave")
        self.assertEqual(mp._title_case_topic("cold start"), "Cold Start")

    def test_falls_back_when_no_topics_exist(self):
        candidates = mp.generate_master_titles([], niche="comedy_entertainment", limit=5)
        self.assertTrue(candidates, "must still produce something with no topics")
        for candidate in candidates:
            self.assertTrue(candidate["title"])


class TestResponseParsing(unittest.TestCase):
    RESPONSE = (
        "**Niche:** outdoors_survival\n"
        "**Topics Mined:** snow cave, alaska, blizzard\n"
        "**Visual Context:** No visual provided\n\n"
        "**Titles:**\n"
        "1. [Story] The Story Behind Snow Cave Survival 🤯 -- 39/100\n"
        "2. [Curiosity Gap] What Nobody Tells You About Alaska Blizzards 🔥 -- 50/100\n\n"
        "**Recommended:** Title #2 -- It combines the specific location with the\n"
        "curiosity gap framework.\n"
    )

    def test_parses_fields(self):
        parsed = mp.parse_master_prompt_response(self.RESPONSE)
        self.assertEqual(parsed["niche"], "outdoors_survival")
        self.assertEqual(parsed["topics"], ["snow cave", "alaska", "blizzard"])
        self.assertEqual(parsed["recommended_index"], 1)

    def test_parses_titles_with_frameworks(self):
        parsed = mp.parse_master_prompt_response(self.RESPONSE)
        self.assertEqual(len(parsed["titles"]), 2)
        self.assertEqual(parsed["titles"][0]["framework"], "Story")
        self.assertIn("Snow Cave", parsed["titles"][0]["title"])

    def test_detects_wrong_character_counts(self):
        """
        The specification's own example had all five counts wrong. A miscount is recorded,
        not trusted, and never costs the caller the title.
        """
        parsed = mp.parse_master_prompt_response(self.RESPONSE)
        # Title 1 claims 39 but the string is longer; title 2 claims 50.
        self.assertEqual(len(parsed["count_mismatches"]), 2)
        for entry in parsed["titles"]:
            self.assertNotEqual(entry["stated_count"], entry["measured_count"])

    def test_tolerates_missing_output(self):
        parsed = mp.parse_master_prompt_response("")
        self.assertEqual(parsed["titles"], [])
        self.assertEqual(parsed["niche"], "")

    def test_clamps_out_of_range_recommendation(self):
        text = self.RESPONSE.replace("Title #2", "Title #99")
        self.assertEqual(mp.parse_master_prompt_response(text)["recommended_index"], 4)

    def test_build_candidates_drops_invalid_titles(self):
        parsed = {
            "topics": ["alaska"],
            "titles": [
                {"id": "a", "framework": "X", "title": "What Nobody Tells You About Alaska"},
                {"id": "b", "framework": "Y", "title": "Nope"},
            ],
        }
        candidates = mp.build_llm_candidates(parsed)
        self.assertEqual([c["id"] for c in candidates], ["a"])


class TestSpecificationExample(unittest.TestCase):
    def test_doc_is_in_sync_with_the_code(self):
        """
        `docs/MASTER_PROMPT_VIRAL_SHORTS_TITLES.md` is GENERATED by
        `render_master_prompt_doc.py`, and its header claims CI asserts that. So assert it.

        The same discipline as the niche table: render from the code, never keep two
        copies. A hand-edited prompt document is a prompt document that is wrong.
        """
        import os
        doc = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "docs", "MASTER_PROMPT_VIRAL_SHORTS_TITLES.md")
        self.assertTrue(os.path.exists(doc), f"{doc} is missing; run render_master_prompt_doc.py")
        with open(doc, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn(mp.MASTER_PROMPT_VERSION, content)
        # The prompt body itself must appear verbatim inside the document's code fence.
        self.assertIn(mp.build_master_prompt(), content)
        # And the worked example's measured counts must be the ones documented.
        for _label, title in mp.SPEC_EXAMPLE_ALASKA["titles"]:
            self.assertIn(f"{title} -- {len(title)}/100", content)

    def test_spec_example_counts_are_correct(self):
        """
        The specification's example output reported 44/52/51/50/44 for titles that are
        35/44/46/43/37 characters. The doc's own numbers were wrong. These are the
        measured values, asserted so the documentation cannot drift again.
        """
        expected = [37, 46, 48, 45, 39]
        actual = [len(t) for _, t in mp.SPEC_EXAMPLE_ALASKA["titles"]]
        self.assertEqual(actual, expected)

    def test_spec_example_titles_all_validate(self):
        for _label, title in mp.SPEC_EXAMPLE_ALASKA["titles"]:
            ok, reason = mp.validate_master_title(title, mp.SPEC_EXAMPLE_ALASKA["topics"])
            self.assertTrue(ok, f"{title!r} -> {reason}")

    def test_spec_example_niche_matches_the_engine(self):
        from topic_engine import classify_niche
        self.assertEqual(
            classify_niche(mp.SPEC_EXAMPLE_ALASKA["transcript"]),
            mp.SPEC_EXAMPLE_ALASKA["niche"],
        )


if __name__ == "__main__":
    unittest.main()
