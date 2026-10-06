import pathlib
import re
import unittest

import title_seo as ts_module

from title_seo import (
    generate_seo_titles,
    validate_title,
    SEO_PATTERNS,
    ALL_PATTERNS,
    MAX_TITLE_CHARS,
    FRONT_LOAD_CHARS,
    _rotation_for,
)
from topic_engine import BLOCKED_TOPIC_WORDS
from title_seo import _ordered_patterns as _ordered_patterns_for

# The exact tokens the old casing-driven per-clip extractor emitted on this corpus. These
# are what produced "Why Nobody Tells You The Truth About Hold".
GARBAGE_KEYWORDS = {"hold", "get", "lock", "put", "bro", "yeah", "god", "messi"}


def _titles(topics, niche="science_education", category="shocking_revelation"):
    return generate_seo_titles(topics, niche=niche, category=category)


class TestTitleSeo(unittest.TestCase):

    def test_no_title_contains_a_blocked_word(self):
        """
        The blocked-word check applies to the KEYWORD SLOT, not the whole title.

        `BLOCKED_TOPIC_WORDS` has 430 entries and includes ordinary English function words
        ("the", "of", "is", "that", "about") because those are junk when *mined as a topic*.
        The templates legitimately contribute them: "The Science Of {kw}" contains both
        "The" and "Of". An earlier draft of this test scanned the entire title against that
        blocklist and therefore failed on almost every generated title -- the same conflation
        `validate_title`'s docstring warns about, where an over-broad gate silently
        invalidated 100% of output. Check the keyword, and let `validate_title` police the
        rest of the title via `_TITLE_BLOCKED`.
        """
        for topic_list in (
            ["sodium", "broth", "flavour"],
            ["hold", "get", "bro", "yeah"],
            ["throttling", "laptop", "thermal"],
        ):
            for cand in _titles(topic_list):
                kw_words = {w.lower() for w in cand["keyword"].split()}
                blocked = kw_words & BLOCKED_TOPIC_WORDS
                self.assertFalse(blocked, f"{cand['title']!r} keyword {cand['keyword']!r} leaks {blocked}")
                # The specific garbage the old casing-driven extractor emitted, which is
                # the actual regression this task exists to prevent.
                self.assertNotIn(cand["keyword"].lower(), GARBAGE_KEYWORDS,
                                 f"{cand['title']!r} used the old junk entity")
                ok, reason = validate_title(cand["title"])
                self.assertTrue(ok, f"{cand['title']!r} rejected by the gate: {reason}")

    def test_every_title_respects_the_100_char_api_limit(self):
        for topic_list in (["sodium"], ["a very long topical phrase about thermal management"]):
            for cand in _titles(topic_list):
                self.assertLessEqual(len(cand["title"]), MAX_TITLE_CHARS, cand["title"])

    def test_searchable_core_is_front_loaded(self):
        for cand in _titles(["sodium", "broth"]):
            head = cand["title"][:FRONT_LOAD_CHARS].lower()
            self.assertTrue(
                any(t in head for t in ("sodium", "broth")),
                f"no keyword in first {FRONT_LOAD_CHARS} chars: {cand['title']!r}")

    def test_titles_differ_from_each_other(self):
        titles = [c["title"] for c in _titles(["sodium", "broth", "flavour"])]
        self.assertEqual(len(titles), len(set(titles)), f"duplicate titles: {titles}")

    def test_degenerate_topics_still_produce_valid_titles(self):
        # Review Focus: a one-word-repeated transcript yields no topics at all.
        for topics in ([], ["yeah"], ["the"], ["hold"]):
            out = generate_seo_titles(topics, niche="general_viral", category="filler_banter")
            self.assertTrue(out, f"no titles for {topics}")
            for cand in out:
                ok, reason = validate_title(cand["title"])
                self.assertTrue(ok, f"{cand['title']!r} rejected: {reason}")

    def test_non_ascii_topics_do_not_leak_into_titles(self):
        """
        The property is "no non-ASCII LETTER", not "the string is ASCII".

        `str.isascii()` is the wrong probe: the templates append an emoji by design, so
        every generated title is non-ASCII and the test would fail every time. Rejecting
        emoji is a mistake `validate_title`'s docstring records as already made once.
        This mirrors the rule the gate actually applies, which is what keeps
        "Cr\u00e8me br\u00fbl\u00e9e" out while leaving the emoji alone.
        """
        for cand in _titles(["Cr\u00e8me br\u00fbl\u00e9e", "Na\u00efve"]):
            bad = [ch for ch in cand["title"] if ch.isalpha() and ord(ch) > 127]
            self.assertFalse(bad, f"{cand['title']!r} leaks non-ascii letters {bad}")
            # And the accented topics must not have become the keyword.
            self.assertNotIn("\u00e8", cand["keyword"])
            self.assertNotIn("\u00ef", cand["keyword"])

    def test_every_candidate_has_the_same_keys_whether_or_not_a_keyword_survived(self):
        """
        Regression: `_fallback_titles` originally emitted only {id, framework, title},
        silently violating the declared contract. Task 5 indexes `candidate["keyword"]`,
        so a batch whose topics all sanitise away would raise KeyError at upload time --
        long after generation, and only for that user.
        """
        declared = {"id", "framework", "title", "pattern_id", "keyword"}
        for topics in (["sodium", "broth"], [], ["yeah", "the", "hold"],
                       ["Cr\u00e8me br\u00fbl\u00e9e", "Na\u00efve"], ["fuck", "shit", "bitch"]):
            out = generate_seo_titles(topics, niche="science_education",
                                      category="shocking_revelation")
            self.assertTrue(out, f"no candidates at all for {topics}")
            for cand in out:
                self.assertEqual(set(cand), declared,
                                 f"candidate shape differs for {topics}: {sorted(cand)}")
                self.assertTrue(cand["keyword"], "keyword must never be empty")

    def test_profanity_never_reaches_a_title(self):
        # Review Focus: the video is bleeped, so the title must not leak it either.
        for cand in _titles(["fuck", "shit", "bitch"]):
            for word in cand["title"].lower().split():
                self.assertNotIn(word, {"fuck", "shit", "bitch", "fucking"}, cand["title"])

    def test_candidate_payload_carries_the_keyword_and_pattern(self):
        """
        `pattern_id` is checked against ALL patterns, not just `SEO_PATTERNS`.

        `SEO_PATTERNS` is the 10 core patterns; `_NICHE_PATTERNS` points at 14 more in
        `_EXTRA_PATTERNS` and `_ordered_patterns` deliberately emits those FIRST, so a
        science video leads with `science_explained`. Validating against `SEO_PATTERNS`
        alone fails on exactly the niches the engine is most tuned for.
        """
        for cand in _titles(["sodium", "broth"]):
            self.assertIn("id", cand)
            self.assertIn("framework", cand)
            self.assertIn("title", cand)
            self.assertIn("keyword", cand)
            self.assertTrue(cand["keyword"])
            self.assertIn(cand["pattern_id"], {p["id"] for p in ALL_PATTERNS})

    def test_validation_gate_rejects_known_bad_titles(self):
        bad = [
            ('Why Nobody Tells You The Truth About Hold...', "blocked topic word"),
            ('Why Nobody Tells You The Truth About Yeah 😳', "filler"),
            ('The Mistake Everyone Makes With Bro', "blocked topic word"),
            ('X' * 140, "too long"),
            ("", "empty"),
        ]
        for title, _expected in bad:
            ok, reason = validate_title(title)
            self.assertFalse(ok, f"{title!r} should have been rejected")
            self.assertTrue(reason)

    def test_validation_gate_accepts_good_titles(self):
        good = [
            "The Sodium Trick Chefs Use To Cut Salt",
            "How Sodium Changes The Flavour Of Everything",
            "Why Salt Makes Food Taste Better",
        ]
        for title in good:
            ok, reason = validate_title(title)
            self.assertTrue(ok, f"{title!r} rejected: {reason}")

    def test_clips_of_the_same_video_get_different_titles(self):
        """
        Review Focus: every clip of a video shares one video context, so without a
        per-clip rotation all its clips would receive an identical title.

        Verified by sweeping 400 synthetic clip texts: 400/400 produce a fully disjoint
        title set on the pattern path, so this assertion is not luck.
        """
        topics = ["sodium", "broth", "flavour"]
        a = [c["title"] for c in _titles(topics)]
        b = [c["title"] for c in generate_seo_titles(
            topics, niche="science_education", category="compelling_story",
            rotation=_rotation_for("a different clip from the same video"))]
        self.assertNotEqual(a, b, "rotation must change the generated set")
        self.assertFalse(set(a) & set(b), "rotation must produce a disjoint title set")

    def test_rotation_is_deterministic(self):
        """Re-running the engine must not shuffle an already-published title."""
        t1 = _rotation_for("the same clip text")
        t2 = _rotation_for("the same clip text")
        self.assertEqual(t1, t2)
        self.assertNotEqual(_rotation_for("clip one"), _rotation_for("clip two"))

    def test_every_pattern_is_actually_reachable(self):
        """
        Every pattern in the bank must be able to emit a title that passes the gate.

        This is the guard the `{cpn}` fix was one class short of. That guard checks the
        bank's *structure* (format keys), so it cannot see a template that is structurally
        perfect and semantically dead -- and two were: `How {kw} Actually Works` and
        `What The Research Actually Says About {kw}` both contain the literal word
        "Actually", which is in `_TITLE_BLOCKED`, so `validate_title` refused them and they
        never emitted for any niche at any rotation. The bank advertised 29 patterns and
        27 could fire.

        The root cause is worth stating, because it will recur: `_sanitize_phrase` already
        filters `_TITLE_BLOCKED` out of the keyword slot, so the whole-title `_TITLE_BLOCKED`
        loop in `validate_title` can only ever fire on the TEMPLATE's own words. A gate that
        can only catch its own constants is a gate that will catch its own constants.

        Formatted with a probe keyword that survives sanitisation, every pattern must pass.
        """
        probe = "sodium"
        dead = {}
        for pattern in ALL_PATTERNS:
            title = pattern["template"].format(kw=probe, kw2=probe, n=5, cap=probe)
            ok, reason = validate_title(title)
            if not ok:
                dead[pattern["id"]] = {"title": title, "reason": reason}
        self.assertEqual(dead, {},
                         f"patterns that can never emit because their own wording fails the "
                         f"gate: {dead}")

    def test_gate_blocklist_does_not_contain_words_the_templates_need(self):
        """
        The specific trap, named, so the fix is not reverted by someone tidying the lists.

        `_sanitize_phrase` already strips `_TITLE_BLOCKED` words out of the KEYWORD slot, so
        the whole-title `_TITLE_BLOCKED` / `_FILLER_RE` loops inside `validate_title` can
        only ever fire on a template's own wording. Both `how_it_works` and `evidence` were
        dead on arrival for exactly this reason: "Actually" is in `_TITLE_BLOCKED`, and the
        first repair attempt used "Really", which is in `_FILLER_RE`. A template author who
        does not know this will silently kill the pattern they just added.
        """
        # Every word each template contributes, ignoring placeholders.
        template_words = set()
        for pattern in ALL_PATTERNS:
            for word in re.findall(r"[A-Za-z]+", pattern["template"]):
                if word.lower() not in ("kw", "kw2", "n", "cap"):
                    template_words.add(word.lower())

        # Words validate_title refuses anywhere in a title, by either rule.
        refused = set(ts_module._TITLE_BLOCKED)
        refused |= {m for m in re.findall(r"[a-z]+", ts_module._FILLER_RE.pattern)}
        # Standalone filler words only: a substring match inside a real word is fine, and
        # these are alternation branches, so compare whole tokens.
        filler_alternatives = set()
        for part in re.split(r"[|()?:]", ts_module._FILLER_RE.pattern):
            part = part.replace("\\", "").strip("^$ .*+?")
            if part and part.isalpha():
                filler_alternatives.add(part)

        collisions = sorted(template_words & (refused | filler_alternatives))
        # `so`, `well`, `like` etc. only matter if they appear as whole words in a template.
        collisions = [c for c in collisions
                      if c in template_words
                      and re.search(rf"\b{re.escape(c)}\b",
                                    " ".join(p["template"] for p in ALL_PATTERNS),
                                    re.IGNORECASE)]
        self.assertEqual(
            collisions, [],
            f"templates contain words validate_title refuses, so those patterns can never "
            f"emit: {collisions}. Either reword the template or remove the word from "
            f"_TITLE_BLOCKED / _FILLER_RE -- do not weaken the gate.")

    def test_the_whole_bank_is_reachable_across_every_niche(self):
        """
        Sweep enough niches and rotations that no niche's pattern list is dead.

        A per-pattern reachability check (above) proves a template can pass in isolation.
        This proves each niche actually *reaches* its own patterns, which is a different
        question: `science_education` lists 12 patterns and every step that hits a dead one
        is a wasted iteration.
        """
        from topic_engine import NICHE_TAGS

        never = []
        for niche in list(NICHE_TAGS) + ["general_viral"]:
            emitted = set()
            for rotation in range(0, 120):
                for cand in generate_seo_titles(["sodium", "broth", "flavour"],
                                                niche=niche, rotation=rotation):
                    emitted.add(cand["pattern_id"])
            # The niche's OWN ordered list -- not every pattern in the bank. A niche does
            # not and should not reach `build_log`; that belongs to tech_ai.
            wanted = {p["id"] for p in _ordered_patterns_for(niche)}
            missing = sorted(wanted - emitted)
            if not (emitted & wanted):
                never.append((niche, "emitted none of its own patterns"))
            elif missing:
                never.append((niche, f"never emitted {missing}"))
        self.assertEqual(never, [], f"niches with unreachable patterns: {never}")

    def test_keyword_actually_appears_in_the_title_it_names(self):
        """
        `keyword` must be searchable in the title it is attached to.

        Nothing pinned this: a mutant that sets `"keyword": "viral"` — a constant — passes
        the whole suite, because the 5-key shape test only checks the key is present and
        non-empty. Task 5 builds hashtags from this field, so a constant would put `#viral`
        on every clip and quietly reintroduce the old padding bug.
        """
        for topics in (["sodium", "broth", "flavour"], ["throttling", "laptop"],
                       ["elden ring", "netherite"]):
            for cand in generate_seo_titles(topics, niche="science_education",
                                            category="high_value_insight"):
                self.assertIn(
                    cand["keyword"].lower(), cand["title"].lower(),
                    f"keyword {cand['keyword']!r} is not searchable in {cand['title']!r}")

    def test_limit_is_honoured_on_both_paths(self):
        """
        `limit=0` must return zero titles, not five.

        The no-topic branch returned `_fallback_titles(...)` unsliced, so every limit except
        5 gave 5 titles back. The production caller hardcodes `limit=5` so nothing broke
        today, but Task 5 is the next consumer of this API and the signature promises
        `limit: int = 5` is honoured.
        """
        for limit in (0, 1, 2, 3, 5, 8):
            with self.subTest(limit=limit):
                no_topic = generate_seo_titles([], niche="general_viral", limit=limit)
                self.assertLessEqual(len(no_topic), limit,
                                     f"no-topic path ignored limit={limit}")
                patterned = generate_seo_titles(["sodium", "broth"],
                                                niche="science_education", limit=limit)
                self.assertLessEqual(len(patterned), limit,
                                     f"pattern path ignored limit={limit}")

    def test_integration_two_clips_of_one_video_get_different_titles(self):
        """
        The per-clip rotation must be wired all the way through `title_tag_engine`.

        This is the property Task 3 and Task 4 both exist for, and it was unpinned at the
        seam: a mutant replacing `rotation=_rotation_for(transcript_text)` with
        `rotation=0` passes the entire suite, because every other test either calls
        `generate_seo_titles` directly with an explicit rotation, or checks one clip.
        Nothing exercised the wiring itself.
        """
        from title_tag_engine import build_video_context, generate_candidate_titles

        full = " ".join([
            "The sodium content in that broth is really high.",
            "Sodium is what makes the flavour pop.",
            "Later we talk about the boss fight on Elden Ring.",
            "My weapon level was too low and the run was brutal.",
        ])
        ctx = build_video_context(full)
        self.assertTrue(ctx["topics"], "guard: the fixture must mine topics")

        clip_a = "sodium is what makes the flavour pop and the broth tastes better"
        clip_b = "the boss fight on elden ring with netherite gear was brutal"

        a = [c["title"] for c in generate_candidate_titles(clip_a, video_context=ctx)]
        b = [c["title"] for c in generate_candidate_titles(clip_b, video_context=ctx)]

        self.assertTrue(a and b, "both clips must get titles")
        self.assertNotEqual(a, b, "two clips of one video got an identical title set")
        self.assertFalse(set(a) & set(b),
                         f"clips of one video share titles: {sorted(set(a) & set(b))}")

    def test_integration_the_context_niche_reaches_the_titles(self):
        """
        The niche from the shared context must select the niche-flavoured patterns.

        Swept over rotations rather than checked once: `_ordered_patterns` puts a niche's
        own patterns FIRST, but `rotation` shifts the index, so for any single clip the
        gaming patterns can sit outside the five that are returned. (An earlier version of
        this test asserted they appear in one call's output and failed for exactly that
        reason -- the test was wrong, not the code.)
        """
        from title_tag_engine import build_video_context, generate_candidate_titles

        gaming = build_video_context(
            "The elden ring boss fight with netherite gear on steam deck was brutal. "
            "My dex build was too low and the dodge roll kept failing in phase two.")
        self.assertEqual(gaming["niche"], "gaming", "guard: fixture must classify as gaming")

        gaming_patterns = {p["id"] for p in _ordered_patterns_for("gaming")}
        self.assertTrue(gaming_patterns & {"run_showcase", "comparison"},
                        "guard: the gaming niche must actually list gaming patterns")

        # Must be a GAMING-EXCLUSIVE pattern, not merely one of the ordered list: that list
        # also contains all 10 core patterns, which any niche can emit. An earlier version
        # asserted membership in the whole list and therefore passed even when the seam
        # ignored the context's niche entirely.
        exclusive = {p["id"] for p in ALL_PATTERNS
                     if p["id"] in {"run_showcase", "comparison"}}
        self.assertTrue(exclusive, "guard: gaming must list an exclusive pattern")

        seen = set()
        for i in range(40):
            for c in generate_candidate_titles(f"the boss fight number {i} was brutal",
                                               video_context=gaming):
                seen.add(c["pattern_id"])
        self.assertTrue(
            seen & exclusive,
            f"no gaming-exclusive pattern ever appeared, so the context's niche is being "
            f"ignored; got {sorted(seen)}")

    def test_the_api_limit_is_pinned_by_value(self):
        """
        `MAX_TITLE_CHARS` must be 100, and a 101-char title must actually be refused.

        `test_every_title_respects_the_100_char_api_limit` compares generated titles against
        `MAX_TITLE_CHARS` itself, so raising the constant to 200 -- or neutering the check
        to 500 -- leaves the suite green while the YouTube title limit silently moves. This
        pins the number and probes the boundary from both sides.
        """
        self.assertEqual(MAX_TITLE_CHARS, 100,
                         "the YouTube title limit is 100 chars; do not change this to make "
                         "a test pass")
        # Must be a realistic title: a single 100-char word trips "fewer than 3 words", and
        # a repeated word trips "repeats a word", so neither probes the length boundary.
        # Distinct dictionary-ish words, padded to exactly 100 characters.
        words = ["Sodium", "Broth", "Flavour", "Kitchen", "Recipe", "Ferment", "Brine",
                 "Aging", "Umami", "Acidity", "Reduction", "Skimmer", "Pan", "Heat",
                 "Seared", "Resting", "Juices", "Sauce", "Plate", "Serve"]
        at_limit = ""
        for w in words:
            if len(at_limit) + 1 + len(w) > 100:
                break
            at_limit = f"{at_limit} {w}".strip()
        at_limit = at_limit + "x" * (100 - len(at_limit))
        self.assertEqual(len(at_limit), 100, "guard: the probe must be exactly 100 chars")
        ok, reason = validate_title(at_limit)
        self.assertTrue(ok, f"a 100-char title must be allowed: {reason}")
        ok, reason = validate_title(at_limit + "x")
        self.assertFalse(ok, "a 101-char title must be refused")
        self.assertIn("exceeds", reason)

    def test_every_gate_rule_has_its_own_rejection(self):
        """
        Each rule in `validate_title` must be the *only* reason a given string is refused.

        Without this, deleting any single rule leaves the suite green, because every rule
        overlaps with the `_TITLE_BLOCKED` word check. Each case below is constructed to
        trip exactly one rule, so a rule that stops firing is visible.
        """
        # (title, substring the REASON must contain)
        #
        # The reason substring is the point. Asserting only "some rule fired" is why
        # deleting the empty-title branch or the no-real-word branch used to leave the suite
        # green: "" and "1 2 3" are both still rejected afterwards, just by a different rule,
        # so the deletion was invisible. Each case is built to trip exactly one rule, and
        # the reason proves which one.
        cases = [
            ("", "empty"),
            ("   ", "empty"),
            ("x" * (MAX_TITLE_CHARS + 1), "exceeds"),
            ("Sodium Sodium Broth Flavour", "repeats"),
            ("Sodium... Broth... Flavour", "ellipses"),
            ("Sodium", "fewer than 3 words"),
            ("a b c d e f", "no real word"),
            ("Cr\u00e8me br\u00fbl\u00e9e Na\u00efve Here", "non-ascii"),
            ("Yeah Sodium Broth Flavour", "blocked word"),
            ("So Sodium Broth Flavour", "filler word"),
        ]
        for title, expected in cases:
            with self.subTest(rule=expected):
                ok, reason = validate_title(title)
                self.assertFalse(ok, f"{title!r} was accepted")
                self.assertIsNotNone(reason, f"{title!r} rejected with no reason")
                self.assertIn(expected, reason,
                              f"{title!r} was rejected by {reason!r}, not by the rule this "
                              f"case is meant to isolate ({expected!r})")

        # And a clean title must still pass, so the rules are not simply "reject everything".
        for title in ("The Sodium Trick Chefs Use To Cut Salt",
                      "How Sodium Changes The Flavour Of Everything",
                      "Why Salt Makes Food Taste Better"):
            with self.subTest(clean=title):
                ok, reason = validate_title(title)
                self.assertTrue(ok, f"{title!r} rejected: {reason}")

    def test_url_fragments_never_reach_a_title(self):
        """
        Auto-captions split URLs into ordinary words, and those words then became titles.

        `mine_topic_phrases` rejects a whole URL, but each bare token — `https`, `com`,
        `watch`, `dot` — is alphanumeric and unblocked, so without an explicit filter the
        engine produced "What Nobody Tells You About dot" and "The Proof That https Is Real".
        `hashtag_engine` grew the same guard independently; this pins the title side.
        """
        for topics in (["https", "com", "watch", "dot", "www"],
                       ["link in bio", "subscribe here", "click the link"],
                       ["sodium", "https", "broth"]):
            for cand in generate_seo_titles(topics, niche="science_education",
                                            category="shocking_revelation"):
                title_words = {w.strip(".,!?").lower() for w in cand["title"].split()}
                for bad in ("https", "http", "www", "com", "dot", "link", "subscribe"):
                    self.assertNotIn(bad, title_words,
                                     f"URL fragment {bad!r} leaked into {cand['title']!r}")
                self.assertTrue(cand["keyword"].strip())

    def test_multi_word_topics_keep_more_than_one_word(self):
        """
        `_sanitize_phrase` caps a keyword at 3 words; reducing that to 1 silently produced
        worse titles and nothing noticed, because every test used single-word topics.
        """
        out = generate_seo_titles(["elden ring netherite gear"], niche="gaming",
                                  category="high_value_insight")
        self.assertTrue(out)
        for cand in out:
            self.assertGreaterEqual(
                len(cand["keyword"].split()), 2,
                f"a 3-word topic was truncated to {cand['keyword']!r} in {cand['title']!r}")

    def test_the_pattern_bank_has_no_duplicate_ids_or_templates(self):
        """
        A duplicated template is two ids producing the same string, which the per-candidate
        `seen` set then silently drops -- costing a slot every time rotation lands on it.
        """
        ids = [p["id"] for p in ALL_PATTERNS]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate pattern ids: {ids}")

        dupes = [t for t in set(p["template"] for p in ALL_PATTERNS)
                 if sum(1 for p in ALL_PATTERNS if p["template"] == t) > 1]
        self.assertEqual(dupes, [], f"duplicate templates: {dupes}")

    def test_the_fallback_text_reflects_the_niche(self):
        """
        A constant fallback tag means every video in every niche gets the same generic
        titles, which is the padding bug this whole task set out to remove -- reintroduced
        on the no-topic path.
        """
        # Asserted against NICHE_TAGS rather than guessed, because the first tag of a niche
        # is not always the niche's own name: business_money leads with `#business`.
        from topic_engine import NICHE_TAGS

        for niche in ("gaming", "fitness_health", "business_money", "outdoors_survival"):
            expected = NICHE_TAGS[niche][0].lstrip("#").lower()
            with self.subTest(niche=niche):
                out = generate_seo_titles([], niche=niche, category="filler_banter")
                self.assertTrue(out, f"no fallback titles for {niche}")
                blob = " ".join(c["title"] for c in out).lower()
                self.assertIn(expected, blob,
                              f"fallback titles for {niche} do not mention it: {blob[:160]}")
                self.assertNotIn("viral moment", blob,
                                 f"{niche} fell back to the generic #viral tag")

    def test_generate_candidate_titles_returns_exactly_five(self):
        """
        The `limit=5` at the call site is otherwise unpinned: changing it to 3 left the
        suite green, and Task 5 and the UI both assume five candidates.
        """
        from title_tag_engine import build_video_context, generate_candidate_titles

        ctx = build_video_context(
            "The sodium content in that broth is really high and the flavour pops. "
            "The elden ring boss fight with netherite gear was brutal on steam deck.")
        for i in range(12):
            candidates = generate_candidate_titles(f"clip {i} about sodium and broth",
                                                   video_context=ctx)
            self.assertEqual(len(candidates), 5,
                             f"clip {i} got {len(candidates)} candidates, expected 5")

    def test_every_template_uses_only_format_keys_that_are_supplied(self):
        """
        Structural guard on the pattern bank itself.

        A transcription slip turned one template's `{kw}` into `{cpn}`, and the whole suite
        stayed green: every test in this file used the `science_education` or `general_viral`
        niche, and `survival_story` is only reachable from `outdoors_survival`. The engine
        raised `KeyError: 'cpn'` on 16 of 40 rotations for that niche and on 3 real corpus
        clips, while `Ran 14 tests ... OK` was printed.

        This checks the bank, not the output, so it fails the moment a template names a key
        that `generate_seo_titles` does not pass.
        """
        import re as _re

        supplied = {"kw", "kw2", "n", "cap"}
        offenders = {}
        for pattern in ALL_PATTERNS:
            used = set(_re.findall(r"\{(\w+)\}", pattern["template"]))
            unknown = used - supplied
            if unknown:
                offenders[pattern["id"]] = {"template": pattern["template"],
                                            "unknown": sorted(unknown)}
        self.assertEqual(offenders, {},
                         f"templates name format keys generate_seo_titles never supplies, "
                         f"so they raise KeyError at runtime: {offenders}")

    def test_no_niche_and_rotation_combination_raises(self):
        """
        Every niche x a wide sweep of rotations must produce titles, never raise.

        The `{cpn}` corruption was invisible to output-shape assertions precisely because
        no test varied the niche. Sweeping both dimensions is what surfaces a template that
        only some code paths reach.
        """
        from topic_engine import NICHE_TAGS

        niches = list(NICHE_TAGS) + ["general_viral", "unknown_niche", ""]
        errors = {}
        for niche in niches:
            for rotation in range(0, 30):
                try:
                    out = generate_seo_titles(["sodium", "broth", "flavour"],
                                              niche=niche, rotation=rotation)
                except Exception as exc:
                    errors.setdefault(f"{type(exc).__name__}: {exc}", []).append(
                        (niche, rotation))
                    continue
                self.assertTrue(out, f"no titles for niche={niche!r} rotation={rotation}")
                for cand in out:
                    self.assertTrue(cand["title"].strip(),
                                    f"empty title for niche={niche!r} rotation={rotation}")
        self.assertEqual(errors, {},
                         f"niche/rotation combinations that raise: "
                         f"{ {k: v[:3] for k, v in errors.items()} }")

    def test_searchable_core_is_front_loaded_even_for_a_long_keyword(self):
        """
        Front-loading must survive the worst case in the bank, not just short keywords.

        `test_searchable_core_is_front_loaded` uses `sodium` and `broth` — 6 and 5
        characters. With those, every template prefix is short enough that the keyword lands
        inside `FRONT_LOAD_CHARS`, so the test passes no matter how long a template grows.
        Nothing measured the boundary.

        `FRONT_LOAD_CHARS` is a design target that no production code reads (deliberately —
        see its docstring for why enforcing it would be an over-broad gate), so this test is
        the ONLY thing standing between a template edit and silently unfront-loaded titles.
        The first version of this test used a 19-character keyword and found `mistake_money`,
        whose 39-character prefix pushed the keyword out for *any* keyword over 6 characters
        — a genuine defect on real data, since real keywords reach 12. Shortening the prefix
        is the fix; lowering FRONT_LOAD_CHARS is not.
        """
        # 12 characters: the longest keyword the real miner actually produced across the
        # 45-clip corpus. Probing an unreachable length would test an impossibility rather
        # than a property -- with a 45-char window and the bank's longest unavoidable
        # 24-char prefix, a 25-character keyword simply cannot front-load, and demanding it
        # would be demanding the impossible.
        long_kw = "toboggan run"
        self.assertEqual(len(long_kw), 12,
                         "guard: the probe must match the real corpus's longest keyword")

        offenders = []
        for niche in ("science_education", "gaming", "outdoors_survival",
                      "business_money", "general_viral"):
            for cand in generate_seo_titles([long_kw] * 3, niche=niche,
                                            category="high_value_insight"):
                head = cand["title"][:FRONT_LOAD_CHARS].lower()
                if cand["keyword"].lower() not in head:
                    offenders.append((cand["pattern_id"], cand["title"],
                                       cand["title"].lower().index(cand["keyword"].lower())))
        self.assertEqual(
            offenders, [],
            f"these patterns push a realistic keyword past the {FRONT_LOAD_CHARS}-char "
            f"window, i.e. they do not front-load: "
            f"{[(p, pos) for p, _, pos in offenders]}. Shorten the template's prefix "
            f"rather than lowering FRONT_LOAD_CHARS.")

    def test_front_load_chars_is_a_target_not_an_enforced_invariant(self):
        """
        Pin the honest relationship, so nobody later reads the constant as a guarantee.

        If this ever starts failing because the engine *does* enforce the window, the
        docstring on `FRONT_LOAD_CHARS` needs rewriting -- enforcement would be an
        over-broad gate, and the recorded failure mode of this project is an over-broad gate
        silently invalidating 100% of output.
        """
        src = pathlib.Path(ts_module.__file__).read_text(encoding="utf-8")
        body = src[src.index("def generate_seo_titles"):]
        self.assertNotIn("FRONT_LOAD_CHARS", body,
                         "generate_seo_titles now reads FRONT_LOAD_CHARS, so the constant has "
                         "become an enforced gate. Rewrite its docstring and re-check that "
                         "no title is being rejected for a soft preference.")

    def test_title_pool_is_large_enough_for_a_whole_video(self):
        """
        The pattern path must offer more distinct titles than the largest video needs.

        Otherwise rotation cannot do its job and clips collide. Measured on the real
        corpus: 27-36 distinct titles reachable per video against a 5-clip maximum, so
        there is headroom -- but the margin is what this test protects.
        """
        pool = set()
        for rotation in range(40):
            for cand in generate_seo_titles(["sodium", "broth", "flavour"],
                                            niche="science_education",
                                            category="shocking_revelation",
                                            rotation=rotation):
                pool.add(cand["title"])
        self.assertGreaterEqual(len(pool), 10,
                                f"only {len(pool)} distinct titles reachable; a 5-clip "
                                f"video would repeat")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------
# NOUN-SLOT GUARD
#
# The mined keyword is a bare word of unknown part of speech -- a noun, a verb, a participle
# or a person's name -- and nothing in this pipeline can tell which. So a title template is
# safe ONLY if a bare SINGULAR NOUN is grammatical in its `{kw}` slot: the word governing
# that slot must be a preposition, or the template must supply a finite verb the keyword
# serves as the subject or object of.
#
# Five patterns broke that rule and shipped ungrammatical titles on 17 of the 45 clips:
#
#     before_after    "{kw} Before And After You Know It"    'bobby Before And After...'
#     i_tried         "I Tried {kw} For {n} Days"            'I Tried motion For 5 Days'
#     why_it_happens  "The Real Reason {kw} Happens"         missing a 'that' clause
#     survival_story  "Surviving {kw} Changed How I See Everything"  needs a gerund
#     setup_punch     "When {kw} Goes Exactly As Planned"    needs a verb
#
# and two more were grammatical only for a noun carrying an article:
#
#     how_it_works    "How {kw} Works In Practice"            'How boss Works In Practice'
#     watch_this      "Watch This Before You Judge {kw}"      needs a clause after 'before'
#
# All seven were rewritten to noun slots, keeping their ids -- see the note on ids below.
#
# `validate_title` CANNOT do this job. All 17 of the bad titles PASSED it: it checks fillers,
# blocked words, length and ASCII, not whether the sentence is English. Do not add a grammar
# expectation to it on the assumption that this guard then becomes redundant.
#
# ON IDS: the rewrite deliberately kept every pattern id. `_NICHE_PATTERNS` maps niches to
# ids and every stored clip carries a `pattern_id`, so renaming `before_after` to
# `changed_my_mind` would silently repoint the niche mapping and orphan 9 stored clips.
# ---------------------------------------------------------------------------------------

# pattern id -> the word immediately governing `{kw}`. Values starting with '@' mark the
# cases where the template supplies the verb instead; those are covered by the frame test
# below rather than by a preposition. The five `fallback_*` ids use `{cap}`, not `{kw}`,
# and only fire when a video has no topic at all.
NOUN_SAFE_PATTERNS = {'count_facts': 'about', 'nobody_tells_you': 'about', 'the_mistake': 'with', 'science_explained': 'of', 'evidence': 'about', 'income_breakdown': 'behind', 'mistake_money': 'on', 'hard_truth': 'about', 'before_after': 'about', 'i_tried': 'about', 'setup_punch': 'about', 'survival_story': 'behind', 'why_it_happens': '@subject', 'how_it_works': 'of', 'watch_this': 'about', 'explained_plainly': '@subject', 'the_proof': '@subject', 'build_log': '@object', 'comparison': '@head', 'run_showcase': '@compound', 'transformation': 'of', 'lesson': '@subject', 'reaction': 'for', 'story': '@subject', 'fallback_search': '{cap}', 'fallback_explainer': '{cap}', 'fallback_story': '{cap}', 'fallback_value': '{cap}', 'fallback_curiosity': '{cap}'}

# The exact frames that shipped bad titles, keyed to measured output rather than to a general
# grammar rule -- so this catches a reintroduction, and does not pretend to catch everything.
BROKEN_FRAMES = [
    (r"\bI Tried \{kw\}", "I Tried {kw} needs a verb"),
    (r"\bSurviving \{kw\}", "Surviving {kw} needs a gerund"),
    (r"\bHow \{kw\} Works", "How {kw} Works needs a clause"),
    (r"\bWhen \{kw\} Goes", "When {kw} Goes needs a verb"),
    (r"\{kw\} Before And After", "{kw} Before And After needs a verb or noun phrase"),
    (r"The Real Reason \{kw\} Happens", "missing a 'that' clause"),
    (r"Before You Judge \{kw\}", "Before You Judge {kw} needs a clause"),
]

# Keywords chosen to break a template: real corpus topics, person names, bare verbs and
# participles, bare count nouns, and mass nouns.
ADVERSARIAL_KEYWORDS = [
    "sodium", "oven", "bacon", "netherite", "squad",   # real topics
    "messi", "nolan", "zeke", "ronaldo",                # person names
    "motion", "throw", "buy", "open", "cooked",         # verbs and participles
    "boys", "city", "car", "much", "million",           # bare count nouns
]


class TestNounSlotDiscipline(unittest.TestCase):
    """
    The keyword has no part of speech, so every template slot must accept a bare noun.

    Kept separate from the `validate_title` tests on purpose: the gate is a filler/blocked-word
    /length check and all 17 of the bad titles passed it.
    """

    def test_every_pattern_in_the_bank_is_declared_noun_safe(self):
        bank = {p["id"] for p in ALL_PATTERNS}
        declared = set(NOUN_SAFE_PATTERNS)
        self.assertEqual(
            bank - declared, set(),
            f"{len(bank - declared)} pattern(s) are in the bank but not declared noun-safe: "
            f"{sorted(bank - declared)}. Add each to NOUN_SAFE_PATTERNS with the word that "
            f"governs its slot, or fix the template -- do not leave one undeclared.")
        self.assertEqual(
            declared - bank, set(),
            f"{len(declared - bank)} declared pattern(s) are no longer in the bank: "
            f"{sorted(declared - bank)}. Remove them, or the declaration is lying.")

    def test_no_pattern_uses_a_known_broken_frame(self):
        offenders = []
        for p in ALL_PATTERNS:
            for pattern, why in BROKEN_FRAMES:
                if re.search(pattern, p["template"]):
                    offenders.append((p["id"], p["template"], why))
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} pattern(s) use a frame that cannot take a bare word, and every "
            f"one of them has shipped ungrammatical titles: {offenders}")

    def test_the_declared_governor_matches_its_template(self):
        """
        A curated declaration drifts when a template is edited and the list is not.

        This cannot check that "about" really governs a noun -- that needs a POS tagger, which
        this plan deliberately does not have. It does check that the declared word is still the
        one sitting immediately before `{kw}`, which is the failure a hand-maintained list
        actually has. The '@'-marked cases are exempt: they assert nothing about position.
        """
        drifted = []
        for p in ALL_PATTERNS:
            gov = NOUN_SAFE_PATTERNS.get(p["id"])
            if gov is None or gov.startswith("@") or gov == "{cap}":
                continue
            if "{kw}" not in p["template"]:
                continue
            if not p["template"].split("{kw}")[0].strip().lower().endswith(gov):
                drifted.append((p["id"], gov, p["template"]))
        self.assertEqual(
            drifted, [],
            f"{len(drifted)} declaration(s) no longer match their template -- either the "
            f"template changed and lost its preposition, or the list is stale: {drifted}")

    def test_every_pattern_renders_for_adversarial_keywords(self):
        """
        Reachability and a size floor, not a grammar judgement.

        'car' is not the right English for a template that wants a gerund, and nothing here can
        decide that without a POS tagger. So this asserts what IS checkable: every
        topic-bearing pattern is renderable for every adversarial keyword, and clears the
        three-word floor `validate_title` enforces. The grammatical judgement lives in
        NOUN_SAFE_PATTERNS.
        """
        rendered = 0
        too_short = []
        for p in ALL_PATTERNS:
            tpl = p["template"]
            if "{kw}" not in tpl and "{cap}" not in tpl:
                continue
            for kw in ADVERSARIAL_KEYWORDS:
                title = (tpl.replace("{kw2}", kw).replace("{kw}", kw)
                         .replace("{n}", "5").replace("{cap}", kw.capitalize())
                         .replace("{category}", "high_value_insight"))
                rendered += 1
                if len(title.split()) < 3:
                    too_short.append((p["id"], title))
        self.assertEqual(too_short, [],
                         f"{len(too_short)} rendered titles have fewer than 3 words: "
                         f"{too_short[:6]}")
        self.assertGreaterEqual(rendered, 300,
                                f"only {rendered} renders -- did the bank shrink?")
