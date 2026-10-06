import unittest
from unittest.mock import patch

import hashtag_engine as H
import title_tag_engine
from hashtag_engine import build_hashtags, build_metadata_tags, MAX_HASHTAGS
from topic_engine import BLOCKED_TOPIC_WORDS, NICHE_TAGS


class TestHashtagEngine(unittest.TestCase):

    def test_set_is_short_and_relevant(self):
        tags = build_hashtags(["sodium", "broth", "flavour"], "science_education")
        self.assertLessEqual(len(tags), MAX_HASHTAGS)
        self.assertGreaterEqual(len(tags), 3)
        self.assertIn("#Shorts", tags)

    def test_topic_drives_the_tags(self):
        a = build_hashtags(["sodium", "broth"], "science_education")
        b = build_hashtags(["throttling", "laptop"], "tech_ai")
        self.assertNotEqual(a, b)
        self.assertTrue(any("sodium" in t.lower() or "broth" in t.lower() for t in a), a)
        self.assertTrue(any("throttl" in t.lower() or "laptop" in t.lower() for t in b), b)

    def test_no_blocked_or_junk_tags(self):
        """
        The blocked-word rule applies to TOPIC-DERIVED tags only, not to the reach tags
        the engine chose on purpose.

        "shorts" is in `BLOCKED_TOPIC_WORDS` because the platform name appears in nearly
        every transcript, so it must never be Mined as a topic. That says nothing about
        `#Shorts`, which is the one mandatory reach tag and is added without going through
        `_clean_tag`. An earlier version of this test asserted the rule over the whole
        output and therefore failed on `#Shorts` itself — the same conflation that broke
        Task 4's blocked-word test.
        """
        chosen = {"#Shorts", "#fyp"}
        for topics in ([], ["hold", "get", "bro", "yeah"], ["sodium"],
                       ["$4.99", "https://x"], ["https", "www", "com"]):
            tags = build_hashtags(topics, "general_viral")
            self.assertIn("#Shorts", tags, topics)
            for tag in tags:
                body = tag.lstrip("#")
                self.assertTrue(body.isalnum(), tag)
                if tag in chosen:
                    continue
                self.assertNotIn(body.lower(), BLOCKED_TOPIC_WORDS, tag)
                self.assertNotIn(body.lower(), {"yeah", "bro", "hold", "get", "thing"}, tag)

    def test_url_fragments_never_become_tags(self):
        """
        A transcript can contain a bare URL. `#https` is a valid-looking tag that helps
        nobody, and the real corpus contains 20 tokens that would produce one.

        (`_clean_tag` already rejects a whole URL like "https://x" because the digits
        and punctuation survive normalisation and fail the isalnum/digit checks. The
        gap is a bare scheme or host token on its own, which the transcript splits out.)
        """
        for topics in (["https", "www", "http", "com", "org", "youtube"],
                       ["link in bio", "subscribe"]):
            for tag in build_hashtags(topics, "general_viral"):
                body = tag.lstrip("#").lower()
                for bad in ("http", "https", "www", "com", "org", "net", "linkinbio"):
                    self.assertNotEqual(body, bad, f"URL fragment leaked as {tag}")

    def test_no_duplicate_tags(self):
        tags = build_hashtags(["sodium", "sodium", "broth"], "science_education")
        self.assertEqual(len(tags), len(set(tags)), tags)

    def test_shorts_and_one_reach_tag_are_always_present(self):
        for niche in list(NICHE_TAGS) + ["unknown_niche"]:
            tags = build_hashtags(["sodium"], niche)
            self.assertIn("#Shorts", tags, niche)

    def test_niche_tag_is_included_when_the_niche_is_known(self):
        tags = build_hashtags(["sodium"], "gaming")
        for expected in NICHE_TAGS["gaming"][:1]:
            self.assertIn(expected, tags)

    def test_empty_topics_still_yields_a_valid_set(self):
        tags = build_hashtags([], "general_viral")
        self.assertTrue(tags)
        self.assertIn("#Shorts", tags)

    def test_metadata_tags_are_broader_than_hashtags(self):
        """
        The `tags` field is readable text, not hashtags: no "#", spaces preserved.

        (An earlier version ended with `assertGreater(len(set(tags)), 0)`, which is
        implied by the `assertTrue(tags)` above it and can never fail. Replaced with
        assertions that carry weight.)
        """
        tags = build_metadata_tags(
            build_hashtags(["sodium", "broth"], "science_education"),
            ["sodium", "broth"], "science_education")
        self.assertTrue(tags)
        for t in tags:
            self.assertNotIn("#", t, t)
            self.assertTrue(t.isascii(), t)
            self.assertTrue(t.strip(), "metadata tags must not be blank")
        # The niche is human-readable, which is the point of this field.
        self.assertIn("science education", tags)
        # And it is genuinely broader than the hashtag set it was built from.
        hashtags = build_hashtags(["sodium", "broth"], "science_education")
        self.assertGreater(len(tags), len(hashtags),
                           f"tags {tags} should outnumber hashtags {hashtags}")
        self.assertEqual(len(tags), len(set(t.lower() for t in tags)),
                         f"metadata tags must be case-insensitively unique: {tags}")


class TestHashtagEngineReachability(unittest.TestCase):
    """
    The plan's nine tests above pin the CONTRACT. These pin REACHABILITY.

    Motivation, not ceremony: Task 4 shipped a 29-entry pattern bank of which 2 could
    never fire, because a template's own wording tripped its own gate. Nothing caught it
    except review. The three tuning constants in `build_hashtags` -- `_ALWAYS`,
    `_MAX_NICHE_TAGS` and `_URL_FRAGMENTS` -- are the same kind of thing: each is a rule
    that is trivially deletable and would leave a still-green suite behind. So each one
    gets a test that fails if the rule is removed.

    Two of these deliberately assert the REASON rather than the outcome. `test_url_...`
    shows a control word of identical shape that DOES become a tag, so the test cannot
    pass merely because everything is being rejected.
    """

    ALL_NICHES = list(NICHE_TAGS) + ["unknown_niche", "", None]

    def test_every_niche_entry_is_reachable_and_emits_its_own_tags(self):
        """
        Sweeps ALL niches, not two. Asserts the engine emits that niche's own first TWO
        tags -- so a `NICHE_TAGS` entry that went stale (or a lookup that always returned
        `general_viral`) fails here instead of passing a suite that only checked #Shorts.
        """
        seen_firsts = set()
        for niche, niche_tag_list in NICHE_TAGS.items():
            tags = build_hashtags(["sodium"], niche)
            for expected in niche_tag_list[:2]:
                self.assertIn(expected, tags, f"{niche} lost {expected}: {tags}")
            seen_firsts.add(niche_tag_list[0])
            # The topic must not be crowded out by the niche's own tags.
            self.assertIn("#sodium", tags, f"{niche} crowded out its topic: {tags}")
        # Distinct niches must not collapse onto one shared tag set.
        self.assertEqual(len(seen_firsts), len(NICHE_TAGS),
                         "two niches share a first tag; the lookup may be collapsing")

    def test_unknown_and_empty_niche_fall_back_to_general_viral(self):
        """The `.get(niche, NICHE_TAGS[...])` default branch, for all three non-keys."""
        for niche in ("unknown_niche", "", None):
            tags = build_hashtags(["sodium"], niche)
            for expected in NICHE_TAGS["general_viral"][:2]:
                self.assertIn(expected, tags, f"fallback broken for niche={niche!r}: {tags}")

    def test_niche_budget_caps_at_two(self):
        """
        `_MAX_NICHE_TAGS = 2` is load-bearing: eight of the nine niches list THREE tags,
        so without the cap all three would be emitted and a topic would be squeezed out.
        (`general_viral` lists only two, so the "3rd tag is dropped" half cannot apply
        there -- it is asserted per-niche, driven by the actual list length.)
        """
        self.assertTrue(any(len(v) > 2 for v in NICHE_TAGS.values()),
                        "premise broken: no niche has a 3rd entry for the cap to drop")
        for niche, niche_tag_list in NICHE_TAGS.items():
            tags = build_hashtags([], niche)          # no topics -> full niche budget
            self.assertEqual(sum(1 for t in tags if t in niche_tag_list), 2,
                             f"{niche} niche-tag count: {tags}")
            for expected in niche_tag_list[:2]:
                self.assertIn(expected, tags, f"{niche} lost {expected}: {tags}")
            if len(niche_tag_list) > 2:
                self.assertNotIn(niche_tag_list[2], tags,
                                 f"{niche} emitted a 3rd niche tag: {tags}")

    def test_topic_slots_are_reserved_against_niche_tags(self):
        """
        The reservation, which is the whole point of the rewrite: with 3 topics the
        niche yields ONE tag, not three, so every topic still gets a slot. Under the old
        engine 3 niche tags were appended first and consumed the entire quota.
        """
        niche_tag_list = NICHE_TAGS["fitness_health"]
        few = build_hashtags(["sodium"], "fitness_health")
        many = build_hashtags(["sodium", "broth", "molecule"], "fitness_health")
        self.assertEqual(sum(1 for t in few if t in niche_tag_list), 2)
        self.assertEqual(sum(1 for t in many if t in niche_tag_list), 1,
                         f"niche tags did not yield to topics: {many}")
        for topic in ("#sodium", "#broth", "#molecule"):
            self.assertIn(topic, many, f"reserved topic slot lost {topic}: {many}")
        # `topics[:3]`: only the first three topics are ever considered. The 4th and 5th
        # are dropped even though they are perfectly good topics -- a 4th would otherwise
        # have to displace either a reserved topic or the mandatory reach tag.
        five = build_hashtags(["sodium", "broth", "molecule", "toboggan", "thaw"],
                              "fitness_health")
        self.assertNotIn("#toboggan", five, f"4th topic should not be considered: {five}")
        self.assertNotIn("#thaw", five, f"5th topic should not be considered: {five}")
        # With room in the budget (fewer topics) a later topic IS reachable, which proves
        # the cap above is about the 3-topic budget, not about rejecting #toboggan itself.
        roomy = build_hashtags(["sodium", "toboggan"], "fitness_health")
        self.assertIn("#toboggan", roomy, roomy)

    def test_max_tags_is_honoured_on_every_path(self):
        """
        `max_tags` was silently ignored on one of `title_seo`'s two exit paths in Task 4,
        so it is swept here across EVERY exit: each topic shape x each niche x max_tags
        0..8. The `0` and empty-topics cases are called out in their own asserts because
        they are the two a naive implementation gets wrong.
        """
        topic_shapes = (
            ("good", ["sodium", "broth", "molecule"]),
            ("junk", ["hold", "get", "bro", "yeah"]),
            ("url", ["https", "www", "com"]),
            ("empty", []),
            ("none", None),
        )
        for max_tags in range(0, 9):
            for label, topics in topic_shapes:
                for niche in self.ALL_NICHES:
                    tags = build_hashtags(topics, niche, max_tags)
                    self.assertLessEqual(
                        len(tags), max_tags,
                        f"max_tags={max_tags} exceeded on the {label} path, "
                        f"niche={niche!r}: {tags}")
            self.assertEqual(build_hashtags(["sodium"], "gaming", 0), [],
                             "max_tags=0 must yield nothing")
            self.assertEqual(build_hashtags([], "gaming", 0), [],
                             "max_tags=0 on the empty-topics path must yield nothing")
        # One tag is always the reach tag when there is any room at all.
        for max_tags in range(1, 9):
            self.assertEqual(build_hashtags([], "gaming", max_tags)[0], "#Shorts",
                             f"max_tags={max_tags} lost the mandatory reach tag")

    def test_oversized_budget_yields_the_whole_candidate_pool(self):
        """
        The two `max_tags` guards are individually equivalent -- removing EITHER one
        leaves every other test green -- because the candidate pool is capped at 7
        (1 reach + 2 niche + 3 topics + #fyp), so for the default `max_tags=5` and above
        the `add()` guard never fires and the final `tags[:max_tags]` never truncates.

        This test pins the pool bound itself, which is the property both guards rest on.
        It is the assertion that would start failing if `_MAX_NICHE_TAGS` or the `[:3]`
        topic slice were widened, at which point the "harmless" guards become load-bearing.
        """
        many_topics = ["sodium", "broth", "molecule", "toboggan", "thaw",
                       "cave", "tent", "kayak", "maple", "ember"]
        for max_tags in range(1, 21):
            tags = build_hashtags(many_topics, "outdoors_survival", max_tags)
            self.assertLessEqual(len(tags), max_tags)
            # 1 reach + 2 niche + 3 topics + 1 reach-bonus is the whole pool.
            self.assertLessEqual(len(tags), 7, f"candidate pool grew: {tags}")
        # A generous budget therefore saturates at 7 and stops -- it does not invent more.
        self.assertEqual(
            build_hashtags(many_topics, "outdoors_survival", 50),
            ["#Shorts", "#outdoors", "#survival", "#sodium", "#broth", "#molecule", "#fyp"])

    def test_url_guard_rejects_fragments_a_control_word_survives(self):
        """
        Asserts the REASON, not just the outcome.

        Without this, `_URL_FRAGMENTS` could be deleted and a test that only checked
        "no URL-looking tags" could still pass, because the same filter that drops `#http`
        would be dropping everything. The control is a word of identical shape --
        lowercase, >=3 chars, unblocked, no digits -- that is NOT in the list and DOES
        become a tag. So the rejection is attributable to the guard, not to the filter.
        """
        control = "soup"           # same shape as "http", absent from _URL_FRAGMENTS
        self.assertNotIn(control, H._URL_FRAGMENTS, "premise broken: control is a fragment")
        self.assertIn(f"#{control}", build_hashtags([control], "general_viral"),
                      "control word was rejected, so the URL test proves nothing")
        for frag in ("http", "https", "www", "com", "org", "link", "subscribe", "url"):
            self.assertNotIn(f"#{frag}", build_hashtags([frag], "general_viral"),
                             f"#{frag} leaked; the control above proves this is the guard")

    def test_digit_and_too_short_bodies_are_rejected(self):
        """
        The remaining two `_clean_tag` guards, each with a case that ONLY it can catch.

        Both look redundant next to `BLOCKED_TOPIC_WORDS` -- "5" and "x" are not in it --
        and both are unobservable through `mine_topic_phrases`, which drops digits and
        sub-3-char tokens upstream. They are defence in depth for a public function that
        accepts an arbitrary topic list, so each needs a case the other rules miss.
        Deleting either guard leaves every other test in this file green.
        """
        for body in ("ps5", "gta5", "4chan"):
            self.assertEqual(H._clean_tag(body), "",
                             f"digit-bearing {body!r} became a tag")
            self.assertNotIn(f"#{body}", build_hashtags([body], "tech_ai"))
        for body in ("x", "ab", "to"):
            self.assertNotIn(body, BLOCKED_TOPIC_WORDS,
                             "premise broken: the word is now blocked, so the "
                             "length guard is what is under test")
            self.assertEqual(H._clean_tag(body), "",
                             f"sub-3-char {body!r} became a tag")

    def test_reach_bonus_appears_only_with_spare_budget(self):
        """
        `#fyp` is the last addition, so it must never displace a topic. With junk topics
        (nothing survives `_clean_tag`) the budget is free and it must appear; with 3 real
        topics it must not, because the 5 slots are already full.
        """
        spare = build_hashtags(["hold", "get", "bro", "yeah"], "general_viral")
        self.assertIn("#fyp", spare, f"reach bonus unreachable: {spare}")
        full = build_hashtags(["sodium", "broth", "molecule"], "general_viral")
        self.assertNotIn("#fyp", full, f"reach bonus displaced a topic: {full}")


class TestTitleTagEngineSeam(unittest.TestCase):
    """
    Proves the WIRING, not just the engine.

    Every other test here calls `build_hashtags` directly, which says nothing about
    whether the production entry point actually calls it. In Task 4 a `rotation=0` passed
    at the call site and the entire suite stayed green. So the seam is pinned here by
    substituting a spy and inspecting what it was actually handed.
    """

    CTX = {"topics": ["creatine", "macros", "lifting"], "niche": "fitness_health"}

    def _spy(self):
        """Records every build_hashtags call, then delegates to the real function."""
        calls = []
        real = H.build_hashtags

        def spy(topics, niche="general_viral", max_tags=H.MAX_HASHTAGS):
            calls.append((list(topics), niche, max_tags))
            return real(topics, niche, max_tags)

        return calls, spy

    def _offline(self):
        """
        Suppresses the live Jev call.

        `score_and_rank_titles_with_jev` reads a real API key from the environment, so
        without this these tests would make a network request each -- 8 s for five tests,
        and a different winning title on any given day. It also made the suite depend on
        an external service for a test that is about local wiring. Returning no key
        selects the documented heuristic fallback, so the rest of the path is unchanged.
        """
        return patch.object(title_tag_engine, "get_jev_api_key", lambda: None)

    def test_entry_point_passes_the_shared_video_context_through(self):
        calls, spy = self._spy()
        with self._offline(), patch.object(title_tag_engine, "build_hashtags", spy):
            meta = title_tag_engine.generate_smart_title_and_hashtags(
                "This gym workout and muscle training session was brutal, the protein diet matters",
                video_context=dict(self.CTX))
        self.assertEqual(len(calls), 1, f"expected exactly one call, got {calls}")
        topics, niche, max_tags = calls[0]
        self.assertEqual(topics, self.CTX["topics"],
                         "the call site re-derived topics instead of using the shared context")
        self.assertEqual(niche, self.CTX["niche"],
                         "the call site did not pass the context's niche")
        self.assertEqual(max_tags, H.MAX_HASHTAGS)
        # The clip's own words must NOT appear: the topics are the VIDEO's, not the clip's.
        self.assertNotIn("#brutal", meta["suggested_hashtags"])
        self.assertEqual(meta["suggested_hashtags"],
                         H.build_hashtags(self.CTX["topics"], self.CTX["niche"]))

    def test_returned_hashtags_are_exactly_what_the_engine_built(self):
        with self._offline():
            meta = title_tag_engine.generate_smart_title_and_hashtags(
                "a talk about sodium and broth", video_context=dict(self.CTX))
        self.assertEqual(meta["suggested_hashtags"],
                         H.build_hashtags(self.CTX["topics"], self.CTX["niche"]))
        self.assertEqual(meta["niche"], self.CTX["niche"],
                         "the returned niche must be the resolved one, not the Jev guess")

    def test_youtube_tags_carry_the_metadata_not_a_naive_strip(self):
        """
        The `youtube.tags` field must hold `build_metadata_tags`' broader, readable
        output. The old code ended with `raw_tags = [t.lstrip("#") ...]`, which would
        have silently discarded the metadata engine's work; this pins that it is gone.
        """
        with self._offline():
            meta = title_tag_engine.generate_smart_title_and_hashtags(
                "a talk about sodium and broth", video_context=dict(self.CTX))
        yt_tags = meta["platform_metadata"]["youtube"]["tags"]
        self.assertEqual(yt_tags, H.build_metadata_tags(
            meta["suggested_hashtags"], self.CTX["topics"], self.CTX["niche"]))
        self.assertNotEqual(yt_tags, [t.lstrip("#") for t in meta["suggested_hashtags"]],
                            "youtube.tags is a naive lstrip again; the metadata engine "
                            "is not reaching the platform metadata")
        self.assertGreater(len(yt_tags), len(meta["suggested_hashtags"]),
                           "the tags field must be broader than the hashtag set")

    def test_empty_context_falls_back_without_raising(self):
        """The `video_context.get(...) or default` guards, on a context with no keys."""
        calls, spy = self._spy()
        with self._offline(), patch.object(title_tag_engine, "build_hashtags", spy):
            meta = title_tag_engine.generate_smart_title_and_hashtags(
                "a talk about sodium and broth", video_context={})
        self.assertEqual(calls[0][0], [])
        self.assertEqual(calls[0][1], "general_viral")
        self.assertIn("#Shorts", meta["suggested_hashtags"])
        self.assertEqual(meta["niche"], "general_viral")

    def test_no_legacy_hashtag_constants_remain(self):
        """
        The deleted constants. `title_tag_engine.MAX_HASHTAGS` was 7 while
        `hashtag_engine.MAX_HASHTAGS` is 5 -- two same-named constants with different
        values is a live trap for the next reader, so their absence is pinned.
        """
        for name in ("NICHE_HASHTAGS", "MAX_HASHTAGS", "RESERVED_HASHTAGS",
                     "OPTIONAL_HASHTAGS"):
            self.assertFalse(hasattr(title_tag_engine, name),
                             f"title_tag_engine.{name} is back; it duplicates and "
                             f"contradicts hashtag_engine")
        self.assertEqual(H.MAX_HASHTAGS, 5)


class TestMetadataTagReachability(unittest.TestCase):
    """`build_metadata_tags` branches, including the three that the plan's tests skip."""

    def test_singular_strip_keeps_both_forms(self):
        """The `clean[:-1]` branch. Also pins the len>4 guard via a word it must skip."""
        out = build_metadata_tags([], ["molecules"], "gaming")
        self.assertIn("molecules", out, out)
        self.assertIn("molecule", out, out)
        # "cars" is len<=4: the guard must prevent "car".
        short = build_metadata_tags([], ["cars"], "gaming")
        self.assertIn("cars", short, short)
        self.assertNotIn("car", short, short)

    def test_unusable_topics_are_skipped_not_emitted(self):
        """The `if not clean: continue` branch -- "$4.99" and a URL normalise to empty."""
        out = build_metadata_tags([], ["$4.99", "https://x.com/a", "sodium"], "gaming")
        self.assertIn("sodium", out, out)
        for junk in ("$4.99", "https", "x.com", "com"):
            self.assertNotIn(junk, out, f"unusable topic leaked as {junk}: {out}")

    def test_none_inputs_are_tolerated(self):
        """Both `or []` guards, so a caller passing None cannot crash the pipeline."""
        self.assertEqual(build_metadata_tags(None, None, "gaming"), ["gaming"])
        self.assertEqual(build_metadata_tags([], None, "gaming"), ["gaming"])
        self.assertEqual(build_metadata_tags(None, ["sodium"], "gaming"), ["gaming", "sodium"])

    def test_blank_niche_is_not_emitted(self):
        """The `if v` half of the guard, distinct from the dedup half."""
        self.assertEqual(build_metadata_tags(["#Shorts"], ["sodium"], "   "),
                         ["sodium", "Shorts"])

    def test_output_is_capped_at_fifteen(self):
        """The `[:15]` slice. 1 niche + 6 topics x 2 forms + 4 hashtags = 17 candidates."""
        out = build_metadata_tags(
            ["#aaa", "#bbb", "#ccc", "#ddd"],
            ["molecules", "carrots", "potatoes", "tomatoes", "onions", "peppers"],
            "gaming")
        self.assertEqual(len(out), 15, out)
        # The cap must not be silently wrong in the other direction.
        self.assertEqual(len(build_metadata_tags([], ["sodium"], "gaming")), 2)

    def test_only_the_first_six_topics_are_read(self):
        """
        The `[:6]` boundary, asserted at the boundary rather than through the cap.

        Mutation check: widening this to `[:12]` changes NO observable output, because
        1 niche + 6 topics x 2 forms = 13 already reaches the `[:15]` cap, so a 7th topic
        could never appear anyway. The slice is therefore unobservable from the outside
        and the mutant is equivalent -- but the boundary is still pinned here so a future
        change to the cap does not silently widen it.
        """
        six = ["molecules", "carrots", "potatoes", "tomatoes", "onions", "peppers"]
        out = build_metadata_tags(["#aaa"], six + ["beans"], "gaming")
        for kept in six:
            self.assertIn(kept, out, f"{kept} was lost: {out}")
        self.assertNotIn("beans", out, f"the 7th topic leaked past [:6]: {out}")
        self.assertNotIn("bean", out, f"the 7th topic's singular leaked: {out}")

    def test_dedup_is_case_insensitive_across_the_whole_list(self):
        """
        A clash is only observable if the two forms are cased differently. The plan's
        own test asserts case-insensitive uniqueness but feeds a list with no case clash
        in it, so a case-SENSITIVE `v not in seen` would pass it.
        """
        out = build_metadata_tags(["#Shorts", "#SHORTS", "#Shorts"], [], "gaming")
        self.assertEqual([t for t in out if t.lower() == "shorts"], ["Shorts"], out)

    def test_hashtag_prefix_is_stripped_but_words_are_kept(self):
        out = build_metadata_tags(["#Shorts"], [], "general viral")
        self.assertIn("Shorts", out, out)
        self.assertNotIn("#Shorts", out, out)
        # A niche with a space survives -- it is the field's human-readable value.
        spaced = build_metadata_tags([], [], "science_education")
        self.assertIn("science education", spaced, spaced)


class TestEmptyTranscriptPath(unittest.TestCase):
    """
    The empty-transcript fallback is a SECOND producer of the metadata, and it used to be the
    OLD engine.

    A clip whose whole text is a stage direction cleans to "" — "[Music]", "[Applause]",
    "[Laughter]", "(upbeat music)" and "[BLANK_AUDIO]" all do — and that path short-circuited
    with a hardcoded five-tag pad plus a `replace("#", "")` strip of its own making, while
    throwing away the `video_context` it had just been handed. Five generic tags with no
    relation to the content, on the exact bug class this engine exists to remove, and nothing
    noticed: a mutant that merely MODERNISED those five tags passed the whole 261-test suite.
    """

    def _call(self, text, ctx):
        from title_tag_engine import generate_smart_title_and_hashtags
        return generate_smart_title_and_hashtags(text, video_context=ctx)

    def test_stage_direction_clips_go_through_the_engine(self):
        ctx = {"topics": ["sodium", "broth", "flavour"], "niche": "science_education",
               "niche_tags": ["#science"]}
        for text in ("[Music]", "[Applause]", "[Laughter]", "(upbeat music)",
                     "[BLANK_AUDIO]"):
            with self.subTest(text=text):
                meta = self._call(text, ctx)
                tags = meta["suggested_hashtags"]
                self.assertLessEqual(len(tags), MAX_HASHTAGS)
                self.assertIn("#Shorts", tags,
                              f"{text}: the mandatory reach tag must survive")
                # The whole point: the context it was HANDED is used.
                self.assertEqual(meta["niche"], "science_education",
                                 f"{text}: the context niche was discarded")
                self.assertTrue(any(t.lstrip("#") in ("sodium", "broth", "flavour")
                                    for t in tags),
                                f"{text}: context topics were discarded, got {tags}")

    def test_the_empty_path_shares_the_metadata_producer(self):
        """
        Every path that writes `youtube.tags` must write it the same way.

        This is the seam that caught B-1: the old fallback was a second, divergent producer,
        so the `build_metadata_tags` guarantees did not apply to it.
        """
        from hashtag_engine import build_metadata_tags

        ctx = {"topics": ["sodium", "broth"], "niche": "science_education",
               "niche_tags": ["#science"]}
        meta = self._call("[Applause]", ctx)
        expected = build_metadata_tags(meta["suggested_hashtags"],
                                      ctx["topics"], "science_education")
        self.assertEqual(meta["platform_metadata"]["youtube"]["tags"], expected,
                         "the empty-transcript path must not use a hand-rolled strip")
        for t in meta["platform_metadata"]["youtube"]["tags"]:
            self.assertNotIn("#", t, f"{t!r} still carries a hash in the tags field")

    def test_mutating_the_generic_pad_would_now_be_visible(self):
        """The regression test for the regression test: prove the path is not inert."""
        from title_tag_engine import generate_smart_title_and_hashtags
        ctx = {"topics": [], "niche": "general_viral", "niche_tags": []}
        meta = generate_smart_title_and_hashtags("[Applause]", video_context=ctx)
        self.assertLessEqual(len(meta["suggested_hashtags"]), MAX_HASHTAGS)
        self.assertNotIn(
            "#explore", meta["suggested_hashtags"],
            "the hardcoded five-tag pad is back on the empty-transcript path")


class TestSingularStripHeuristic(unittest.TestCase):
    """
    The singular strip is a heuristic and its false positives are real, measured on the
    corpus. A naive `endswith("s") and len > 4` published junk search terms for 3 of 11
    videos: thanks->thank, paris->pari, press->pres. That was a REGRESSION: the old
    `lstrip` path did no singularisation at all, so the field previously had no such garbage.
    """

    def test_it_still_produces_real_singulars(self):
        for plural, singular in (("molecules", "molecule"), ("carrots", "carrot"),
                                ("ingredients", "ingredient"), ("sessions", "session")):
            with self.subTest(word=plural):
                tags = build_metadata_tags([], [plural], "gaming")
                self.assertIn(plural, tags)
                self.assertIn(singular, tags)

    def test_the_es_class_is_a_known_unfixable_limitation(self):
        """
        Record what the heuristic cannot do, so nobody mistakes it for solved.

        English plural morphology is not decidable from the string alone: `molecules` wants
        only the "s" dropped, `potatoes` wants "es" dropped, and `species` must not be touched
        at all. A length-and-suffix heuristic cannot tell these apart, and getting it right
        needs a word list, which this plan explicitly rejects as a new dependency.

        The measured consequence on the real corpus is **zero**: across all 11 videos the
        miner produces no topic ending in "es" at all, and the only plain-s topics over the
        old 4-character threshold are `paris` (refused by the length rule) plus `boys` and
        `sits`, both exactly 4 characters. So the current rule strips 0 real topics and
        publishes 0 junk.

        These assertions therefore pin the *known* behaviour rather than a desired one. If
        the miner ever starts producing "es" words, this test is the tripwire that says the
        heuristic needs revisiting -- at which point the fix is a word list, not another
        suffix rule.
        """
        # `potatoes` -> `potatoe`: wrong, and not fixable without a dictionary.
        self.assertIn("potatoe", build_metadata_tags([], ["potatoes"], "gaming"))
        # `species` -> `specie`: wrong, and not fixable without a dictionary.
        self.assertIn("specie", build_metadata_tags([], ["species"], "gaming"))
        # The guard is that neither of those shapes occurs on the corpus at all.
        from hashtag_engine import _plausible_plural
        self.assertTrue(_plausible_plural("molecules"))
        self.assertTrue(_plausible_plural("potatoes"))

    def test_it_refuses_the_observed_false_positives(self):
        for word in ("thanks", "paris", "press", "class", "boss"):
            with self.subTest(word=word):
                tags = build_metadata_tags([], [word], "gaming")
                self.assertIn(word, tags, f"{word} must still be published as mined")
                self.assertNotIn(word[:-1], tags,
                                 f"{word} -> {word[:-1]} is not a word; the strip must refuse")

    def test_the_length_boundary_is_pinned(self):
        """
        The `>= 7` threshold is the whole defence against `paris` and `thanks`, so pin it
        on both sides rather than only on the words that happen to be in the corpus.
        """
        from hashtag_engine import _plausible_plural

        # 6 chars, ends in s, not ss: below the threshold -> refused.
        self.assertFalse(_plausible_plural("thanks"))
        self.assertFalse(_plausible_plural("cousin"[:5] + "s"))
        # 7 chars, ends in s, not ss: at the threshold -> allowed.
        self.assertTrue(_plausible_plural("carrots"))
        # ends in ss at any length: refused, because ss is not a plural suffix.
        self.assertFalse(_plausible_plural("class"))
        self.assertFalse(_plausible_plural("business"[:5] + "ss"))
        # not ending in s at all.
        self.assertFalse(_plausible_plural("sodium"))
        self.assertFalse(_plausible_plural(""))

    def test_corpus_words_do_not_produce_junk(self):
        """
        The review's three measured regressions, plus the near misses.

        `paris` is the one the OLD rule actually shipped on real data, so it is the case that
        matters. `chess`/`chaos`/`gas` are extra guards on the same class.
        """
        junk = []
        for word in ("thanks", "paris", "press", "class", "boss", "chess", "gas", "chaos"):
            tags = build_metadata_tags([], [word], "gaming")
            if word[:-1] in tags:
                junk.append((word, word[:-1]))
        self.assertEqual(junk, [], f"singular strip produced non-words: {junk}")


class TestUntestedEngineDecisions(unittest.TestCase):
    """
    The eight gaps a review found by mutation. Each is a decision the engine makes that no
    test covered, so each could be reversed silently.
    """

    def test_multiword_topics_are_concatenated_not_underscored(self):
        """
        The engine's headline design decision, with zero coverage until now.

        `normalize_for_tag` joins words with "_", so a multi-word topic would become the
        INVALID hashtag "#explosive_proof" -- "_" is not alphanumeric. Concatenating gives
        "#explosiveproof", which is the standard convention. Two videos on the real corpus
        have multi-word mined topics, and one actually ships the concatenated form, so this
        is live in production, not hypothetical.
        """
        from hashtag_engine import _clean_tag

        self.assertEqual(_clean_tag("explosive proof"), "#explosiveproof")
        self.assertEqual(_clean_tag("elden ring"), "#eldenring")
        self.assertEqual(_clean_tag("speed run"), "#speedrun")
        for tag in (_clean_tag("explosive proof"), _clean_tag("elden ring")):
            self.assertTrue(tag.lstrip("#").isalnum(),
                            f"{tag!r} is not a valid hashtag")
            self.assertNotIn("_", tag, f"{tag!r} keeps normalize_for_tag's underscore")

    def test_falsy_topics_inside_the_list_are_skipped(self):
        """
        The `if t` filter is the only thing making a None element survivable, and it was
        untested. Removing it turns this into an AttributeError on `None.replace`.
        """
        tags = build_hashtags(["sodium", None, "broth", "", "flavour"], "science_education")
        self.assertIn("#Shorts", tags)
        self.assertTrue(any("sodium" in t.lower() for t in tags), tags)
        self.assertTrue(any("broth" in t.lower() for t in tags), tags)
        for t in tags:
            self.assertTrue(t.startswith("#"), f"{t!r} is not a hashtag")

    def test_url_filtering_does_not_swallow_ordinary_topics(self):
        """
        An over-broad `_URL_FRAGMENTS` entry would silently delete real topics.

        The first version of this test asserted only that `build_hashtags([w], ...)[1:]` was
        non-empty, which is worthless: with one topic the niche tags still fill the list, so
        dropping the topic entirely left `['#viral', '#trending']` and the assertion passed.
        Adding `food` to `_URL_FRAGMENTS` was an equivalent mutant under that test. Assert the
        topic's OWN tag instead.

        Every word below is verified clean against the other two guards
        (`BLOCKED_TOPIC_WORDS` and the 3-character minimum), so the only thing that can
        remove it is `_URL_FRAGMENTS`.
        """
        from topic_engine import BLOCKED_TOPIC_WORDS
        from hashtag_engine import _URL_FRAGMENTS, _clean_tag

        ordinary = ["food", "fire", "space", "game", "run", "cooking", "soup", "tomato",
                    "onion", "hiking", "camping", "guitar", "sunset", "kitchen", "garden",
                    "bicycle", "painting", "welding", "climbing", "kayaking"]
        for word in ordinary:
            with self.subTest(word=word):
                # Precondition: nothing but _URL_FRAGMENTS could remove this word.
                self.assertNotIn(word, BLOCKED_TOPIC_WORDS, f"{word} is blocked elsewhere")
                self.assertNotIn(word, _URL_FRAGMENTS)
                self.assertEqual(_clean_tag(word), f"#{word}",
                                 f"{word} is altered by a guard other than the URL filter")

                tags = build_hashtags([word], "general_viral")
                self.assertIn(f"#{word}", tags,
                              f"{word} vanished from {tags} -- an over-broad "
                              f"_URL_FRAGMENTS entry is eating a real topic")

        # And a genuine URL fragment must still be refused.
        for word in ("https", "com", "www", "http"):
            with self.subTest(url_fragment=word):
                tags = build_hashtags([word], "general_viral")
                self.assertNotIn(f"#{word}", tags, f"{word} should be filtered")

    def test_multiword_url_fragments_are_still_filtered(self):
        """A phrase like 'dot com' must be refused, not just the bare tokens."""
        for phrase in ("dot com", "watch later", "link up", "press play"):
            with self.subTest(phrase=phrase):
                tags = build_hashtags([phrase], "general_viral")
                for token in ("dot", "com", "watch", "link", "press"):
                    self.assertNotIn(f"#{token}", tags,
                                     f"{phrase!r} leaked {token!r} via {tags}")

    def test_the_hashtag_slice_into_metadata_tags_is_pinned(self):
        """
        `hashtags[:4]` was the one magic number in the file with no test, while `[:6]` and
        `[:15]` both had one. Pin it, and pin that the 15-cap is the real ceiling.
        """
        from hashtag_engine import build_metadata_tags as bmt

        many = [f"#tag{i}" for i in range(10)]
        self.assertLessEqual(len(bmt(many, [], "gaming")), 15)
        # 4 hashtags, no topics -> niche + 4 = 5 entries. A `[:3]` slice would give 4.
        self.assertEqual(len(bmt(["#Shorts", "#viral", "#a", "#b"], [], "gaming")), 5)
        # 6 hashtags, no topics -> niche + 4 = 5, because the slice binds before the cap.
        self.assertEqual(len(bmt(["#1", "#2", "#3", "#4", "#5", "#6"], [], "gaming")), 5)

    def test_a_negative_max_tags_returns_nothing(self):
        """
        Negative `max_tags` must return `[]`, and this is the ONLY thing pinning the final
        `tags[:max_tags]` slice.

        `add()` already refuses to exceed `max_tags`, so for `max_tags >= 0` the slice and
        the `add()` guard are provably equivalent — a review brute-forced 2366 cases and
        found 0 differences, and removing either is an equivalent mutant. They diverge only
        for negative values, where `tags[:-2]` is a *negative* slice: without the final
        slice a `max_tags` of -2 would emit one tag where the correct answer is none. That
        boundary was untested, which is why both mutants lived.
        """
        for max_tags in (-1, -2, -3, -10):
            with self.subTest(max_tags=max_tags):
                self.assertEqual(
                    build_hashtags(["sodium", "broth", "flavour"], "gaming",
                                   max_tags=max_tags),
                    [],
                    f"max_tags={max_tags} must yield no tags at all")
                self.assertEqual(
                    build_hashtags([], "gaming", max_tags=max_tags),
                    [],
                    f"max_tags={max_tags} must yield no tags even with no topics")

    def test_a_none_niche_does_not_become_the_literal_tag_none(self):
        """`str(niche)` turned None into the tag "None", which is worse than raising."""
        from hashtag_engine import build_metadata_tags as bmt

        for niche in (None, "", 0):
            with self.subTest(niche=niche):
                tags = bmt([], ["sodium"], niche)
                self.assertNotIn("None", tags, f"niche={niche!r} produced the tag 'None'")
                self.assertIn("sodium", tags)

    def test_every_promised_reach_tag_survives_a_small_budget(self):
        """
        The residual hole in the `#Shorts` guarantee.

        `TestBug08` pins `#Shorts` and that is sufficient *today*, because `len(_ALWAYS) == 1`.
        But the original bug was "a promised reach tag silently lost to a quota guard", and
        the moment a second tag is added to `_ALWAYS`, a `max_tags` of 1 would drop it with
        nothing failing. Assert the general invariant instead of the single tag.
        """
        from hashtag_engine import _ALWAYS

        self.assertTrue(_ALWAYS, "_ALWAYS must promise at least one reach tag")
        for max_tags in range(1, MAX_HASHTAGS + 1):
            with self.subTest(max_tags=max_tags):
                tags = build_hashtags(["sodium", "broth"], "gaming", max_tags=max_tags)
                for promised in _ALWAYS[:max_tags]:
                    self.assertIn(promised, tags,
                                  f"max_tags={max_tags} dropped the promised reach tag "
                                  f"{promised!r} (got {tags})")
                self.assertLessEqual(len(tags), max_tags)


if __name__ == "__main__":
    unittest.main()
