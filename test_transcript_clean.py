import re
import unittest
from transcript_clean import clean_transcript, clean_tokens, normalize_for_tag


class TestTranscriptClean(unittest.TestCase):

    def test_strips_speaker_markers(self):
        raw = ">> Zeke, I ain't mad at it. >> Yeah, >> Zeke, that's gota"
        self.assertNotIn(">>", clean_transcript(raw))
        self.assertIn("Zeke", clean_transcript(raw))

    def test_strips_bracketed_stage_directions(self):
        raw = "That was crazy [laughter] I can't believe it [Music] real talk"
        out = clean_transcript(raw)
        self.assertNotIn("laughter", out)
        self.assertNotIn("Music", out)
        self.assertIn("real talk", out)

    def test_strips_all_caps_emphasis(self):
        self.assertNotIn("CAUGHT", clean_transcript(">> IT CAUGHT ME OFF GUARD."))

    def test_collapses_stutter_repeats(self):
        # "We We Hold" became a bogus topic phrase before this task.
        self.assertNotIn("we we", clean_transcript("We We Hold on"))
        self.assertNotIn("the the", clean_transcript("the the answer is"))

    def test_expands_contractions_for_tokenisation(self):
        tokens = clean_tokens("I ain't with this and I'm not going back")
        self.assertNotIn("ain't", tokens)
        self.assertIn("not", tokens)

    def test_drops_fillers(self):
        tokens = clean_tokens("so um basically like you know it was literally crazy")
        for filler in ("um", "basically", "literally"):
            self.assertNotIn(filler, tokens)

    def test_strips_parenthesised_stage_directions(self):
        # Regression: a blanket [..] rule plus a 6-word allowlist for (..) meant
        # "(laughs)" survived while "[laughs]" did not, and "laughs" then reached a
        # PUBLISHED title that passed validate_title.
        self.assertEqual(clean_transcript("hello (laughs) world"), "hello world")
        self.assertNotIn("laughs", clean_tokens("she (chuckles) then (sighs) loudly"))
        self.assertNotIn("chuckles", clean_tokens("she (chuckles) then (sighs) loudly"))
        self.assertNotIn("sighs", clean_tokens("she (chuckles) then (sighs) loudly"))
        # Leading position must behave the same as mid-sentence.
        self.assertEqual(clean_transcript("(laughs) hello there"), "hello there")

    def test_stage_directions_never_reach_the_token_stream(self):
        """The clean_transcript -> clean_tokens contract."""
        raw = "so (laughs) this is [chuckles] the sodium (sighs) content (grunts) now"
        for word in ("laughs", "chuckles", "sighs", "grunts"):
            self.assertNotIn(word, clean_tokens(raw), word)

    def test_output_is_single_line_and_trimmed(self):
        out = clean_transcript("  the sodium \t content\r\n  of the broth  ")
        self.assertEqual(out, "the sodium content of the broth")
        self.assertNotIn("\n", out)
        self.assertNotIn("\t", out)
        self.assertEqual(out, out.strip())

    def test_collapses_unicode_whitespace_and_zero_width(self):
        out = clean_transcript("the\u00a0sodium\u200bcontent of the broth")
        self.assertIn("sodium", out)
        self.assertIn("content", out)
        self.assertNotIn("\u00a0", out)
        self.assertNotIn("\u200b", out)

    def test_degenerate_inputs_return_empty(self):
        for junk in ("", "   ", "\n\n", "!!! ... ???", ">>>", "[]", "()"):
            # The punctuation strip deliberately preserves ,.!? so sentence boundaries
            # survive, so punctuation-only input is not necessarily the empty string.
            # What must hold is that no WORD survives and no token is produced.
            self.assertEqual(clean_tokens(junk), [], repr(junk))
            self.assertFalse(
                re.search(r"[A-Za-z0-9]", clean_transcript(junk)),
                f"{junk!r} -> {clean_transcript(junk)!r}")
        for junk in ("", "   ", "\n\n", ">>>", "[]", "()"):
            self.assertEqual(clean_transcript(junk).strip(), "", repr(junk))

    def test_keeps_alphanumeric_product_names_for_niche_matching(self):
        # "ps5" is a real gaming keyword; dropping the digit made it permanently dead.
        self.assertIn("ps5", clean_tokens("the ps5 runs the game at 60 fps"))

    def test_sanitize_for_tag_survives_bracketed_numbers(self):
        # Bracket removal is blanket, so "[450]" is gone entirely. Documented consequence.
        self.assertNotIn("450", clean_transcript("bake at [450] for 20 minutes"))

    def test_keeps_real_content_words(self):
        tokens = clean_tokens("the sodium content changes the flavour of the broth")
        for keep in ("sodium", "content", "changes", "flavour", "broth"):
            self.assertIn(keep, tokens)

    def test_normalize_for_tag_is_hashtag_safe(self):
        # Review Focus: prices, URLs, emoji and accents must never reach a hashtag.
        self.assertEqual(normalize_for_tag("$4.99"), "")
        self.assertEqual(normalize_for_tag("https://x.com/a"), "")
        self.assertEqual(normalize_for_tag("🔥🔥"), "")
        self.assertEqual(normalize_for_tag("Café Wörld"), "cafe_world")
        self.assertTrue(normalize_for_tag("Low-Sodium Broth").isascii())

    def test_normalize_for_tag_never_empty_for_real_word(self):
        self.assertTrue(normalize_for_tag("sodium"))
        self.assertTrue(normalize_for_tag("Deep-Sea"))


if __name__ == "__main__":
    unittest.main()
