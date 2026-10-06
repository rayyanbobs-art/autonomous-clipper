import inspect
import unittest

from transcript_clean import clean_tokens
from topic_engine import (
    BLOCKED_TOPIC_WORDS,
    STAGE_DIRECTION_WORDS,
    mine_topic_phrases,
    classify_niche,
    NICHE_KEYWORDS,
    NICHE_STRONG_KEYWORDS,
    NICHE_TAGS,
)

HOLD_TRANSCRIPT = (
    ">> Y very astute. >> You feel me? >> Brain. We We Hold on. >> Hold on. "
    "That's what I say. He cooking [laughter] though cuz he kind of right. "
    ">> Hold on. IT CAUGHT ME OFF GUARD. >> NO, NO, NO"
)

SODIUM_TRANSCRIPT = (
    "So, can I can you see what sodium looks like? Is it is it got a look to it? "
    ">> What does sodium look like? That's a good question. I like crack. "
    "Come on, bro. The sodium content in that broth is really high. "
    "Sodium is what makes the flavour pop when you reduce the pot."
)

GAMING_TRANSCRIPT = (
    "The boss fight on Elden Ring took me six hours. My weapon level was too low so "
    "every hit did almost no damage. I finally switched to a katana build and the "
    "gameplay loop clicked. Npc aggro in Elden Ring is brutal if you dont block."
)


class TestTopicEngine(unittest.TestCase):

    def test_never_returns_a_verb_or_interjection_as_a_topic(self):
        topics = [p for p, _ in mine_topic_phrases(HOLD_TRANSCRIPT)]
        for junk in ("hold", "hold on", "we we", "bro", "cuz", "though"):
            self.assertNotIn(junk, topics)

    def test_topic_phrases_are_hashtag_safe(self):
        for phrase, _ in mine_topic_phrases(SODIUM_TRANSCRIPT + " " + GAMING_TRANSCRIPT):
            self.assertTrue(phrase.replace(" ", "_").isascii(), phrase)
            self.assertNotIn(">>", phrase)
            self.assertNotIn("'", phrase)
            self.assertNotIn("(", phrase)
            # NB: do NOT assert `.isalnum()` on the space-substituted form. "_" is not
            # alphanumeric in Python, so any multi-word phrase would fail an assertion
            # that can never pass for any implementation. The underscore-free hashtag form
            # is asserted in Task 5, where `_clean_tag` performs the join -- asserting it
            # here would also make Task 2 depend on a module built in Task 5.
            for word in phrase.split():
                self.assertTrue(word.isalnum(), f"{word!r} in {phrase!r}")

    def test_finds_the_actual_subject_of_a_conversation(self):
        topics = [p for p, _ in mine_topic_phrases(SODIUM_TRANSCRIPT)]
        self.assertIn("sodium", topics)

    def test_rank_order_is_stable_and_deterministic(self):
        a = mine_topic_phrases(GAMING_TRANSCRIPT)
        b = mine_topic_phrases(GAMING_TRANSCRIPT)
        self.assertEqual(a, b)

    def test_repeated_topic_beats_a_one_off_word(self):
        text = "The thermal throttling issue is real. Thermal throttling again. " \
               "Thermal throttling keeps coming back on this laptop."
        topics = [p for p, _ in mine_topic_phrases(text)]
        self.assertLess(topics.index("throttling"), topics.index("laptop"))

    def test_empty_transcript_returns_empty_list(self):
        self.assertEqual(mine_topic_phrases(""), [])
        self.assertEqual(mine_topic_phrases("   "), [])

    def test_degenerate_transcript_does_not_crash(self):
        # Review Focus: a video whose transcript is shorter than one clip window.
        for junk in ("yeah", "yeah yeah yeah", ">>>", "[]", "...", "a"):
            self.assertIsInstance(mine_topic_phrases(junk), list)
            self.assertIsInstance(classify_niche(junk), str)

    def test_niche_is_stable_for_one_video(self):
        """
        The real guarantee is that ONE context is computed per video and reused for every
        clip, so classification never has to be slice-stable. Assert the two properties
        that actually matter: determinism, and that a full-length window holds.

        Do NOT assert a 120-char slice of SODIUM+GAMING classifies as "gaming": that
        window lies entirely inside the SODIUM half, contains no gaming keyword, and
        "general_viral" is the CORRECT answer for it. An earlier version of this test
        asserted that and was simply wrong.
        """
        text = SODIUM_TRANSCRIPT + " " + GAMING_TRANSCRIPT
        first = classify_niche(text)
        for _ in range(3):
            self.assertEqual(classify_niche(text), first)
        self.assertEqual(first, "gaming")
        self.assertEqual(classify_niche(text[:400]), first)

    def test_alphanumeric_niche_keywords_are_reachable(self):
        """Regression: clean_tokens dropped digits, so "ps5" could never match."""
        gaming_with_ps5 = (GAMING_TRANSCRIPT + " The ps5 version runs the same game "
                           "and the ps5 build is the one everyone criticises.")
        self.assertEqual(classify_niche(gaming_with_ps5), "gaming")
        # ...but a numeric name must still never become a TOPIC.
        topics = [p for p, _ in mine_topic_phrases(gaming_with_ps5)]
        self.assertNotIn("ps5", topics)


class TestNicheGateIsReal(unittest.TestCase):
    """
    A review showed the min-score gate, the margin gate and the corroboration gate were
    ALL untested: deleting any of them left the suite green, so 3 of 11 real videos were
    mis-filed as `gaming` and inherited a 100% `#gaming` hashtag set. These pin each gate.
    """

    def test_weak_keywords_alone_cannot_claim_a_niche(self):
        """"game"/"play" are ordinary English; they are not evidence of gaming."""
        for text, why in [
            ("my manager said the team must play smarter and win the game plan", "office"),
            ("level the flour then level the sugar then level the butter", "baking"),
            ("i play chess every day. my play on this is simple.", "chess"),
            ("the football is a game. you play football. the game was good.", "football"),
        ]:
            self.assertNotEqual(classify_niche(text), "gaming", f"{why}: {text!r}")

    def test_a_strong_keyword_does_claim_the_niche(self):
        self.assertEqual(
            classify_niche("the elden ring boss fight respawned and the gameplay loop clicked"),
            "gaming")
        self.assertEqual(
            classify_niche("minecraft netherite gear and the boss fight respawn"),
            "gaming")

    def test_min_score_gate_fires(self):
        """One weak keyword repeated 3x scores 2.732 and must NOT win on score alone."""
        self.assertLess(1.0 + (3 ** 0.5), 3.0)
        self.assertEqual(classify_niche("level 1 level 2 level 3"), "general_viral")

    def test_margin_gate_fires(self):
        """
        The margin rule is reachable but only in a narrow band, and the corpus fixture below
        confirms it never fires on the 11 real videos. This pins it with a case where the
        OTHER two gates both pass, so only the margin can be responsible for the answer.

        gaming  = "minecraft" (strong, 1x) + "game" (weak, 1x)        -> 4.00, strong=1
        outdoors = "toboggan" (strong, 5x)                            -> 3.24
        margin: 4.00 < 3.24 * 1.25 = 4.05  -> too close  -> general_viral
        """
        def probe(n_toboggan: int) -> str:
            return ". ".join(["minecraft game"] + ["toboggan run"] * n_toboggan)

        # 4x toboggan = 3.00; 3.00 * 1.25 = 3.75 < 4.00, so gaming wins.
        self.assertEqual(classify_niche(probe(4)), "gaming")
        # 5x toboggan = 3.24; 3.24 * 1.25 = 4.05 > 4.00, so the margin refuses.
        self.assertEqual(classify_niche(probe(5)), "general_viral")

    def test_multiword_evidence_survives_outside_the_top_topics(self):
        """
        A multi-word keyword is matched against the raw token bigrams, NOT the mined topic
        list. This transcript buries "machine learning" below the top-8 topics, so a build
        that matched against the mined list would score nothing and answer general_viral.
        """
        filler = " ".join(
            f"topic{chr(97 + i)} topic{chr(97 + i)} topic{chr(97 + i)}" for i in range(12))
        text = f"{filler} machine learning machine learning machine learning {filler}"
        self.assertEqual(classify_niche(text), "tech_ai")
        # Sanity: the phrase genuinely is buried, so the test means what it claims.
        topics = [p for p, _ in mine_topic_phrases(text, limit=8)]
        self.assertNotIn("machine learning", topics, f"not actually buried: {topics}")

    def test_corroboration_gate_is_what_stops_a_lone_keyword(self):
        """A high score from weak keywords alone is not enough."""
        text = "the level and the boss and the play and the game"
        self.assertEqual(classify_niche(text), "general_viral")

    def test_repeated_weak_keyword_alone_is_refused(self):
        # 1 + sqrt(4) == 3.0, which clears _NICHE_MIN_SCORE, but no strong keyword fired.
        self.assertEqual(classify_niche("level 1 level 2 level 3 level 4"), "general_viral")

    def test_empty_and_tokenless_transcripts_are_general_viral(self):
        for junk in ("", "   ", ">>", "the of and a to"):
            self.assertEqual(classify_niche(junk), "general_viral", repr(junk))


class TestCorpusFixture(unittest.TestCase):
    """
    Pins the classification of the real corpus so that any future retune of
    NICHE_KEYWORDS / NICHE_STRONG_KEYWORDS / the thresholds is VISIBLE in a test diff.

    These strong-keyword lists are tuned to 11 videos and are overfit by construction.
    This fixture is the tripwire.
    """

    # video_id -> expected niche. Derived from the 45 clips in output/ at plan time, and
    # each entry was checked against the transcript rather than assumed.
    #
    # Four of these are the CORRECTIONS this fixture exists to protect. Before the strong-
    # keyword gate, gaming wrongly claimed dV0OgeSbYPM (football commentary), jm-sJUUani8
    # (a workplace comedy skit) and qteIOgjfDIw (livestream donation callouts), and
    # motivation_mindset wrongly claimed v9QtM6qnG50 (an investment pitch that says
    # "almost ten MILLION dollars" and "Market!!"; it was winning on weak focus/goal/life).
    EXPECTED = {
        "4mTLpuQpB80": "general_viral",        # small-business product pitch, ambiguous
        "7APGcnUv2zQ": "science_education",   # sodium / chemistry conversation
        "A6v5Vj6h_fQ": "outdoors_survival",    # toboggan, snow, alaska
        "dV0OgeSbYPM": "general_viral",        # football commentary, NOT gaming
        "JheRzFxeSBg": "general_viral",
        "jm-sJUUani8": "general_viral",        # comedy skit about a manager, NOT gaming
        "kJu5VMN3yow": "gaming",               # minecraft / netherite
        "KrLj6nc516A": "business_money",       # dollars, million
        "pySIRc4QjsY": "general_viral",
        "qteIOgjfDIw": "general_viral",        # livestream donations, NOT gaming
        "v9QtM6qnG50": "business_money",       # investment pitch, NOT motivation
    }

    @classmethod
    def setUpClass(cls):
        import glob
        import json
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        pools = {}
        for path in glob.glob(os.path.join(here, "output", "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            snippet = data.get("transcript_snippet")
            if not snippet:
                continue
            pools.setdefault(data.get("video_id"), []).append(snippet)
        cls.pools = pools

    def test_corpus_is_present(self):
        if not self.pools:
            self.skipTest("no output/*.json corpus available")
        self.assertGreaterEqual(len(self.pools), 5)

    @unittest.skipUnless(
        __import__("os").path.isdir(
            __import__("os").path.join(
                __import__("os").path.dirname(__import__("os").path.abspath(__file__)),
                "output")),
        "no output/ directory")
    def test_each_real_video_classifies_as_expected(self):
        wrong = {}
        for vid, snippets in self.pools.items():
            pool = " ".join(snippets)
            got = classify_niche(pool)
            want = self.EXPECTED.get(vid)
            if want is not None and got != want:
                wrong[vid] = f"expected {want}, got {got}"
        self.assertEqual(wrong, {}, f"corpus classification drift: {wrong}")


class TestReactionAndDedup(unittest.TestCase):
    """
    A review found `haa`, `hoo` and `noo` reaching the topic list, which is how
    "5 Facts About Haa That Change Everything" became reachable.
    """

    REACTIONS = "ohhhh noooo hahaha whoooa hAA hoO noo haa hehe yoo HAAA"

    def test_vocal_reactions_never_become_topics(self):
        topics = [p for p, _ in mine_topic_phrases(self.REACTIONS)]
        for bad in ("haa", "hoo", "noo", "hahaha", "haaa", "yoo", "hehe", "whoooa"):
            self.assertNotIn(bad, topics, f"{bad!r} leaked into {topics}")

    def test_real_words_ending_in_doubled_vowels_survive(self):
        """The doubled-vowel rule must not eat legitimate topics."""
        for word in ("book", "coffee", "letter", "moon", "pool", "tool", "wheel"):
            topics = [p for p, _ in mine_topic_phrases(f"the {word} on the {word} again "
                                                      f"and the {word} here")]
            self.assertIn(word, topics, f"{word!r} was wrongly blocked")

    def test_morphological_variants_collapse(self):
        topics = [p for p, _ in mine_topic_phrases(
            "the car cost a lot. cars are expensive. the car is here. cars cost more.")]
        self.assertIn("car", topics)
        self.assertNotIn("cars", topics, f"car/cars both kept: {topics}")

    def test_sub_phrase_and_super_phrase_collapse_both_ways(self):
        a = [p for p, _ in mine_topic_phrases(
            "scratch piece here. scratch piece there. scratch piece again. "
            "piece one. piece two.")]
        self.assertLessEqual(len(a), 2, f"too many slots for one concept: {a}")
        b = [p for p, _ in mine_topic_phrases(
            "a salt mine. a salt mine here. a salt mine there. salt everywhere.")]
        self.assertNotIn("salt mine", b, "super-phrase and sub-phrase both kept")
        self.assertNotIn("mine", b, "super-phrase and sub-phrase both kept")

    def test_dedup_does_not_swallow_unrelated_short_words(self):
        """'art' inside 'party' is not the same subject."""
        topics = [p for p, _ in mine_topic_phrases(
            "party party party party and art art art art in the end")]
        self.assertIn("party", topics)
        self.assertIn("art", topics, f"'art' was swallowed by 'party': {topics}")

    def test_bigrams_must_be_adjacent(self):
        joined = [p for p, _ in mine_topic_phrases(
            "sodium salt pepper sodium salt pepper and more words here now")]
        self.assertNotIn("sodium pepper", joined, f"non-adjacent pair invented: {joined}")

    def test_limit_zero_and_negative_return_empty(self):
        text = "the sodium content in that broth is really high"
        self.assertEqual(mine_topic_phrases(text, limit=0), [])
        self.assertEqual(mine_topic_phrases(text, limit=-1), [])
        self.assertEqual(mine_topic_phrases(text, limit=1), mine_topic_phrases(text, limit=1)[:1])

    def test_phrases_parameter_was_removed(self):
        """
        A review found `classify_niche(x, phrases=[...])` crashed with
        `ValueError: too many values to unpack` on the plain string list that
        build_video_context produces. The parameter was then found to be dead -- nothing
        read the value -- so it is gone rather than defended. This pins the removal.
        """
        self.assertNotIn("phrases", inspect.signature(classify_niche).parameters)
        self.assertEqual(
            list(inspect.signature(classify_niche).parameters), ["transcript_text"])

    def test_blocklists_are_not_empty(self):
        """A review deleted all 44 STAGE_DIRECTION_WORDS with the suite green."""
        self.assertGreater(len(STAGE_DIRECTION_WORDS), 20)
        for word in ("laugh", "laughs", "chuckle", "sigh", "gasp", "cough", "whisper"):
            self.assertIn(word, STAGE_DIRECTION_WORDS, word)
        for word in ("hold", "get", "the", "and", "not", "what", "yeah"):
            self.assertIn(word, BLOCKED_TOPIC_WORDS, word)

    def test_every_niche_has_a_strong_keyword_list(self):
        for niche in NICHE_KEYWORDS:
            self.assertIn(niche, NICHE_STRONG_KEYWORDS, f"{niche} has no strong list")
            self.assertTrue(NICHE_STRONG_KEYWORDS[niche], f"{niche} strong list is empty")
            for kw in NICHE_STRONG_KEYWORDS[niche]:
                self.assertIn(kw, NICHE_KEYWORDS[niche],
                              f"{niche}: strong keyword {kw!r} not in NICHE_KEYWORDS")
            self.assertEqual(len(NICHE_STRONG_KEYWORDS[niche]),
                             len(set(NICHE_STRONG_KEYWORDS[niche])),
                             f"{niche}: duplicate strong keywords")

    def test_the_polysemous_keywords_are_deliberately_kept(self):
        """
        A review recommended deleting boss/play/level from the gaming list. They are kept on
        purpose: deleting them throws away real signal and the strong-keyword gate is what
        actually stops a false positive. This pins that decision so it cannot drift by
        accident.
        """
        for kw in ("boss", "play", "level", "game"):
            self.assertIn(kw, NICHE_KEYWORDS["gaming"], kw)
        for kw in ("minecraft", "gameplay", "elden", "ps5", "npc"):
            self.assertIn(kw, NICHE_STRONG_KEYWORDS["gaming"], kw)

    def test_stretched_reactions_need_the_stretch_rule(self):
        """_INTERJECTION cannot catch these; only _STRETCHED can."""
        for word in ("sooo", "gooo", "yeeees", "nooooo", "wheee"):
            topics = [p for p, _ in mine_topic_phrases(
                f"{word} {word} the sodium content in the broth is high")]
            self.assertNotIn(word, topics, f"{word!r} leaked: {topics}")

    def test_short_interjections_need_the_interjection_rule(self):
        """None of these are in BLOCKED_TOPIC_WORDS, so only _INTERJECTION catches them."""
        for word in ("whoa", "omg", "huh", "heh", "hoho"):
            topics = [p for p, _ in mine_topic_phrases(
                f"{word} {word} the sodium content in the broth is high")]
            self.assertNotIn(word, topics, f"{word!r} leaked: {topics}")

    def test_repetition_uses_sublinear_gain(self):
        """
        A review showed the sqrt gain was unpinned, so both a `constant 1.0` and a `linear`
        mutant survived. The score curve is 1 + sqrt(n): at n=3 that is 2.73 (below the 3.0
        minimum) and at n=4 exactly 3.0 (at it). That single boundary pins the curve.

        Each repetition needs surrounding words, because clean_transcript collapses a run
        of the SAME word ("grind grind grind" -> "grind") before it ever reaches the miner.
        """
        def probe(n: int) -> str:
            return "we grind every single day. " * n

        self.assertEqual(classify_niche(probe(3)), "general_viral",
                         "3 repetitions must score 2.73 and fall short of the minimum")
        self.assertEqual(classify_niche(probe(4)), "motivation_mindset",
                         "4 repetitions must score 3.0 and clear the minimum")

    def test_empty_tokens_with_supplied_phrases_is_general_viral(self):
        """The empty-token guard was untested."""
        self.assertEqual(classify_niche(""), "general_viral")
        self.assertEqual(classify_niche("   "), "general_viral")

    def test_multiword_keywords_are_matched_from_the_token_stream(self):
        """
        The multi-word branch was dead code because no keyword contained a space. Two now
        do, and they are matched against raw token bigrams -- NOT against the mined topic
        list, which only surfaces the top 8 and would miss them.
        """
        text = ("we trained a machine learning model overnight. the neural network ran "
                "for hours. machine learning needs data. neural network again.")
        self.assertEqual(classify_niche(text), "tech_ai")
        # Pinned so the branch cannot go dead again: a multi-word keyword that is NOT in
        # the general list can never be matched, whatever else is true of it.
        for kw in ("machine learning", "neural network", "open world"):
            owner = [n for n, kws in NICHE_KEYWORDS.items() if kw in kws]
            self.assertEqual(len(owner), 1, f"{kw!r} should belong to exactly one niche")
            self.assertIn(kw, NICHE_STRONG_KEYWORDS[owner[0]], f"{kw!r} must be strong")

    def test_digits_still_reach_the_niche_counter(self):
        """
        The digit allowance is only observable when an alphanumeric keyword is the SOLE
        source of strong evidence. Elsewhere the strong gate masks it.
        """
        # "ps5" is the only strong gaming keyword here; "game" is weak.
        self.assertEqual(classify_niche("the ps5 game ran"), "gaming")
        # ...and a purely numeric transcript is still general_viral.
        self.assertEqual(classify_niche("we counted 45 60 90 12 7 3 8 5 11 2"), "general_viral")

    def test_open_world_is_a_multiword_gaming_keyword(self):
        self.assertIn("open world", NICHE_KEYWORDS["gaming"])
        self.assertIn("open world", NICHE_STRONG_KEYWORDS["gaming"])
        self.assertEqual(classify_niche("an open world game with lots of exploration"),
                         "gaming")

    def test_niche_recognises_a_domain(self):
        self.assertEqual(classify_niche(GAMING_TRANSCRIPT), "gaming")
        self.assertEqual(
            classify_niche("This gym workout built muscle and my protein diet changed "
                           "my cardio and sleep and overall health."),
            "fitness_health")

    def test_every_niche_tag_list_is_short_and_hashtag_shaped(self):
        for niche, tags in NICHE_TAGS.items():
            self.assertTrue(1 <= len(tags) <= 4, f"{niche} has {len(tags)} tags")
            for t in tags:
                self.assertTrue(t.startswith("#"), t)
                self.assertTrue(t.lstrip("#").isalnum(), t)


if __name__ == "__main__":
    unittest.main()
