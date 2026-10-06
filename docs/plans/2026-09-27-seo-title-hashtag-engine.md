# SEO Title & Hashtag Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the garbage-generating title/tag pipeline with one that derives a real, searchable topic from the source video and produces front-loaded SEO titles plus relevant Shorts hashtags.

**Architecture:** Topic detection moves from *per-clip, casing-driven* to *per-video, frequency-driven*. A new `transcript_clean` normalizer strips auto-caption artifacts, a new `topic_engine` mines the video's core subject from the full transcript, and new `title_seo` / `hashtag_engine` modules consume that topic. A validation gate rejects any title that still contains junk, so a bad entity can never reach the UI or the YouTube API. `title_tag_engine.generate_smart_title_and_hashtags` keeps its signature and becomes a thin orchestrator so `app.py` and `batch_rerender.py` do not break.

**Tech Stack:** Python 3.14, stdlib only (`re`, `collections`, `unicodedata`). No new dependencies — spacy/nltk/sklearn are deliberately NOT added; see "Why no NLP dependency" in the Global Constraints.

**Spec:** This plan. Evidence for every design decision is in "Measured Baseline" below.

---

## Measured Baseline

Measured against the 45 clips currently in `output/` on 2026-09-27. These numbers are the definition of "bad" and the acceptance bar for this plan.

| Symptom | Measured value |
|---|---|
| Titles using the dead fallback `"Why Nobody Tells You The Truth About X…"` | **28 / 45 (62%)** |
| Titles using a bare spoken quote (`"I ain't with this no more" 🤯`) | 8 / 45 |
| Videos whose clips disagree on niche | **9 / 11 (82%)** — one video (`v9QtM6qnG50`) split across 5 niches |
| Clips falling through to `general_viral` | 12 / 45 |
| Current top entities | `Hold`, `Get`, `Lock`, `Pepperon`, `Put`, `Salt`, `Messi`, `God`, `Nolan`, `Zeke` |

> **Which "disagree on niche" figure to quote.** Three different numbers get quoted in this
> plan, and they measure three different code paths. Do not mix them up:
>
> | Code path | Disagreement | Measured |
> |---|---|---|
> | Per clip, old engine (`title_tag_engine.classify_niche_heuristic`) — what actually ran before this plan | **9 / 11** | 2026-09-27 |
> | Per clip, new engine (`topic_engine.classify_niche`, Task 2) — the classifier improves on its own, but per-clip sampling is still wrong | **4 / 11** | 2026-09-27 |
> | One context per video (Task 3) — the property the plan actually needs | **0 / 11** | 2026-09-27 |
>
> The `9 / 11` in the table above is the **pre-fix baseline for the engine that was
> actually running**, so it is the correct "before" number. The `4 / 11` row is the honest
> answer to "did Task 2 help on its own?" — yes, a lot — and the reason Task 3 is still
> needed is that 4/11 is not 0/11. The worst video under the new per-clip engine is
> `7APGcnUv2zQ`, whose 5 clips split 2 ways (`general_viral` / `science_education`).

Two probes were run to establish root causes:

1. **Casing is unreliable.** A "seen in lowercase ⇒ not a proper noun" rule rejects 38% of current entities, but it also wrongly rejects *legitimate* entities — `Alaska`, `Sodium`, `Netherite`, `Pepperoni` each appear lowercase once in auto-captions. YouTube auto-captions capitalise inconsistently, so capitalisation cannot be a primary signal. Conclusion: score by **casing-independent frequency**, not by capitalisation.
2. **Video-level extraction is dramatically cleaner than clip-level.** For video `7APGcnUv2zQ`:
   - clip-level → `['cuz', 'though', 'caught', 'guard', 'zeke']`
   - video-level → `['zeke', 'sodium', 'air', 'pure']` ← the actual subject matter

   A ~30 s window is too small a sample for topic detection. The full transcript is already in memory in `app.py::run_clipping_job`.

---

## Global Constraints

- **No new pip dependencies.** spacy, nltk, textblob and sklearn are all absent. Do not add them. Casing-independent frequency plus a curated blocklist handles this corpus; POS tagging is not required.
- **Pure stdlib only** in all new modules: `re`, `collections`, `unicodedata`, `json`, `pathlib`.
- **YouTube Shorts title limit: 100 characters, hard.** Titles over 100 chars are truncated by the API at `uploader.py:263`.
- **The searchable core phrase must occupy the first 45 characters** of every title, because the feed truncates there.
- **`generate_smart_title_and_hashtags(transcript_text, category, api_key)` signature is preserved.** New parameters are keyword-only with defaults.
- **Existing tests must keep passing.** `test_title_and_uploader.py` asserts on the current return-dict shape; `test_reported_bug_fixes.py` asserts the hashtag/uppercase fixes from the previous round.
- **Tests live flat at the repo root as `test_*.py` and run with `python -m unittest <module>`.** Match this convention; do not introduce a `tests/` package.
- **Match existing code style:** module-level docstrings, `typing` annotations, `Optional`/`List`/`Dict` from `typing`, f-strings, print-based progress logging.
- **Every module in this plan has been executed against the real corpus.** See "Calibration Findings" below for the nine defects that were found and fixed by doing so, and for the verified before/after numbers. Do not re-derive this code from the prose; use the code blocks as written.
- **Never re-add a `isascii()` check on a whole title.** Emoji are intentional. Check non-ASCII *letters* instead. See Calibration Finding #4.
- **Never let `validate_title` re-check keyword topicality.** It caused a 100% rejection rate once already. See Finding #5.
- **A new task's tests must include at least one assertion on real output quality** (a keyword must not be a function word), not only on gate compliance. Gate-compliance tests all passed while the titles were still nonsense.

## Why no NLP dependency

A POS tagger would let us keep a "proper noun" concept. It is still the wrong trade here:

- spaCy + `en_core_web_sm` is ~15 MB and adds a model download to a project that currently installs cleanly with `pip install -r`-free ad-hoc deps.
- The measured failure is **not** "we mis-tag part of speech". It is "we picked `Hold` as the topic because it was capitalised mid-sentence". Frequency + blocklist fixes that directly.
- The corpus is spoken conversational English with stutters, `>>` speaker markers and `[laughter]` tags. A tagger trained on written prose handles it worse than a blocklist tuned to this corpus.

If a future requirement needs real POS, that is a separate plan with a dependency decision of its own.

---

## Review Focus

Inputs and conditions the spec implies but no task's tests exercise — one line each, most likely to bite first. Each line's test is added to the owning task below.

- **A video whose full transcript is shorter than one clip window** must not crash topic extraction; it has no video-level corpus to mine. → Task 2
- **A transcript containing a URL, a price (`$4.99`) or an emoji** must not produce a hashtag like `#4` or `#99`. → Task 1, Task 5
- **Two clips from the SAME video must not get the same title.** Sharing video context means they share topics, so without a per-clip rotation this plan would emit identical titles for every clip of one video — the single most likely way it makes the output *worse*. → Task 4
- **A single repeated word (`"yeah yeah yeah"`) must not produce a title.** No topic survives, so the engine must fall back to a generic-but-valid frame, not emit `Why Nobody Tells You The Truth About Yeah`. → Task 4
- **Profanity must not reach a published title.** The video is already bleeped, so leaking the word in the title is both a policy and a discoverability problem. → Task 4
- **Non-ASCII topic text must not leak into a title.** The corpus contains French speaker names; they must be dropped, not transliterated. → Task 4

---

## File Structure

| File | Responsibility |
|---|---|
| `transcript_clean.py` (new) | Normalize raw auto-captions: strip `>>` speaker markers, `[laughter]`-style tags, ALL-CAPS emphasis, stutters, contractions, fillers. Produces clean text plus the token stream. No knowledge of topics. |
| `topic_engine.py` (new) | Casing-independent topic mining. Takes the **full video transcript**, returns a ranked list of topic phrases plus a video-level niche. Owns all stopword/blocklist data. |
| `title_seo.py` (new) | Title pattern bank, generation, front-loading, character budget, and the junk-rejection validation gate. |
| `hashtag_engine.py` (new) | Hashtag + YouTube-tag strategy derived from topic and niche, with a relevance gate. Separates hashtags (3–5) from metadata tags (broader). |
| `title_tag_engine.py` (modify) | Becomes a thin orchestrator. Keeps `generate_smart_title_and_hashtags` and its return shape; delegates to the four new modules. |
| `app.py` (modify) | Compute video context **once** from the full transcript and pass it to every clip in the job. |
| `batch_rerender.py` (modify) | Pass video context through the backfill path. |
| `backfill_titles.py` (new) | Regenerate titles/hashtags/niche for existing `output/*.json` without re-rendering video. |
| `templates/index.html` (modify) | Stop truncating the title; surface the SEO keyword and the 5 candidates. |
| `test_transcript_clean.py` (new) | |
| `test_topic_engine.py` (new) | |
| `test_title_seo.py` (new) | |
| `test_hashtag_engine.py` (new) | |
| `test_video_context_parity.py` (new) | |

---

## Task 1: Transcript Normalizer

**How to read the code blocks in this plan.** Each implementation block opens with a
`# <filename>.py` comment. That comment is a *plan label for humans*, NOT part of the file.
Do not include it. The line after it is the module's real docstring.

Auto-captions are the input to everything downstream. Today `>>`, `[laughter]`, ALL-CAPS shouting and stutters all reach the topic miner, which is why `"We We Hold"` became a topic.

**Files:**
- Create: `transcript_clean.py`
- Test: `test_transcript_clean.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `clean_transcript(text: str) -> str` — normalized single-line text.
  - `clean_tokens(text: str) -> List[str]` — lowercase content tokens, fillers and stutters removed. **This is the token stream every later task consumes.**
  - `normalize_for_tag(text: str) -> str` — ASCII-folded, lowercase, alphanumeric-only, safe to embed in a hashtag. **Returns `""` when nothing usable survives**: pure numbers, prices, URLs, emoji, and any non-Latin script. Callers must treat `""` as "skip this candidate", not as an error. Every call site in Tasks 2, 4 and 5 already guards on empty.

- [ ] **Step 1: Write the failing test**

```python
# test_transcript_clean.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_transcript_clean -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'transcript_clean'`

- [ ] **Step 3: Write minimal implementation**

```python
# transcript_clean.py
"""Normalization of raw YouTube auto-captions into clean text and a content token stream.

Everything downstream (topic mining, title generation, hashtags) consumes `clean_tokens`,
so this module is the single place that knows how to strip auto-caption artifacts:
`>>` speaker markers, [laughter]-style stage directions, shouted ALL-CAPS runs, stutter
repeats, contractions and conversational fillers.
"""
import re
import unicodedata
from typing import List

_STAGE_DIRECTIONS = re.compile(r"\[(?:[^\]]*)\]|\([^)]*\)")
# A run of >=2 shouted words is transcript emphasis, not a proper noun.
# NB the {2,} is deliberate: with {3,} a run like "IT CAUGHT ME" is only partly matched,
# leaving shouting in the text. Some single shouted words do survive -- see the plan's
# Calibration Findings. Only `clean_tokens` (which lowercases) is consumed downstream.
_SHOUTING = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})+\b")
# URLs, emails and bare paths can never become a topic word or a hashtag.
_URLISH = re.compile(r"(?:https?://|www\.|\.com|\.org|\.net/|/|@[\w.]+\.)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_REPEAT_WORD = re.compile(r"\b(\w+)(\s+\1\b)+", re.IGNORECASE)

_FILLERS = {
    "um", "uh", "erm", "ah", "oh", "eh", "hmm", "mm",
    "like", "basically", "literally", "actually", "honestly", "right",
    "okay", "ok", "yeah", "yep", "yup", "nah", "wow", "hey", "well", "so",
    "gonna", "wanna", "gotta", "lemme", "gimme", "dunno", "kinda", "sorta",
    "thing", "things", "stuff", "kind", "sort", "bit", "lot", "maybe",
    "know", "mean", "see", "look", "think", "guess", "suppose",
    "sure", "course", "definitely", "probably",
    "anyway", "though", "although", "cuz", "cause", "alright",
    "just", "really", "very", "quite", "true", "false",
}

_CONTRACTIONS = {
    "ain't": "is not", "aren't": "are not", "can't": "can not",
    "cannot": "can not", "couldn't": "could not", "didn't": "did not",
    "doesn't": "does not", "don't": "do not", "hadn't": "had not",
    "hasn't": "has not", "haven't": "have not", "i'm": "i am",
    "isn't": "is not", "it's": "it is", "let's": "let us",
    "shouldn't": "should not", "that's": "that is", "there's": "there is",
    "they're": "they are", "we're": "we are", "weren't": "were not",
    "what's": "what is", "won't": "will not", "wouldn't": "would not",
    "you're": "you are", "you've": "you have", "i've": "i have",
    "i'll": "i will", "we'll": "we will", "they'll": "they will",
    "you'll": "you will", "he'll": "he will", "she'll": "she will",
    "gonna": "going to", "wanna": "want to", "gotta": "got to",
    "lemme": "let me", "gimme": "give me", "dunno": "do not know",
    "y'all": "you all", "cuz": "because", "'cause": "because",
    "kinda": "kind of", "sorta": "sort of",
}


def _expand_contractions(text: str) -> str:
    def repl(m):
        word = m.group(0).lower().replace("\u2019", "'")
        return _CONTRACTIONS.get(word, m.group(0))

    return re.sub(r"[A-Za-z]+['\u2019][A-Za-z]+", repl, text)


def clean_transcript(text: str) -> str:
    if not text:
        return ""
    s = str(text)
    s = s.replace("\u2019", "'")
    s = _STAGE_DIRECTIONS.sub(" ", s)
    s = re.sub(r"(?:^|\s)>>+\s*", " ", s)
    s = re.sub(r"^\s*[\[\(][^\]\)]*[\]\)]\s*", " ", s)
    s = _expand_contractions(s)
    s = _REPEAT_WORD.sub(r"\1", s)
    s = _SHOUTING.sub(lambda m: m.group(0).lower(), s)
    s = re.sub(r"[^A-Za-z0-9'\s%$.,!?]", " ", s)
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def clean_tokens(text: str) -> List[str]:
    cleaned = clean_transcript(text)
    if not cleaned:
        return []
    out: List[str] = []
    for raw in re.findall(r"[a-z0-9][a-z0-9']{1,}", cleaned.lower()):
        token = raw.strip("'")
        if len(token) < 2 or token in _FILLERS:
            continue
        out.append(token)
    return out


def normalize_for_tag(text: str) -> str:
    if not text:
        return ""
    s = str(text)
    # A URL, email or path is not a search term. Reject before stripping punctuation,
    # otherwise "https://x.com/a" would silently become the tag "https_x_com_a".
    if _URLISH.search(s):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    s = re.sub(r"_+", "_", s)
    if not re.search(r"[a-z]", s):
        return ""
    return s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest test_transcript_clean -v`
Expected: PASS — 9 tests OK

- [ ] **Step 5: Run the full suite to prove no regression**

Run: `python -m unittest test_confirmed_bugs_fixes test_subtitles_taxonomy test_niche_integration test_title_and_uploader test_reported_bug_fixes`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add transcript_clean.py test_transcript_clean.py
git commit -m "feat: add auto-caption transcript normalizer"
```

> **Precondition for every commit step in this plan:** the repo is not under version
> control. Run `git init && git add -A && git commit -m "baseline before SEO title engine"`
> once before starting Task 1. If you prefer not to initialise git, skip every
> "Commit" step; nothing else in the plan depends on it.

---

## Task 2: Video-Level Topic Engine

Replaces casing-driven `extract_topical_entities` for titles and hashtags. Frequency-driven, so it is immune to the inconsistent capitalisation measured in the baseline.

**Files:**
- Create: `topic_engine.py`
- Test: `test_topic_engine.py`

**Interfaces:**
- Consumes: `transcript_clean.clean_tokens(text) -> List[str]` (Task 1).
- Produces:
  - `BLOCKED_TOPIC_WORDS: Set[str]` — module-level, so later tasks import rather than duplicate.
  - `mine_topic_phrases(transcript_text: str, limit: int = 8) -> List[Tuple[str, float]]` — ranked `(phrase, score)` pairs. `phrase` is 1–3 tokens, lowercase, already hashtag-safe.
  - `classify_niche(transcript_text: str, phrases: List[Tuple[str, float]] = None) -> str` — returns a `NICHE_KEYWORDS` key, or `"general_viral"` only when there is genuinely no evidence.
  - `NICHE_TAGS: Dict[str, List[str]]` — the short, relevant tag set per niche (replaces the 6-per-niche `NICHE_HASHTAGS` dump).

- [ ] **Step 1: Write the failing test**

```python
# test_topic_engine.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_topic_engine -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'topic_engine'`

- [ ] **Step 3: Write minimal implementation**

```python
# topic_engine.py
"""Casing-independent topic mining for YouTube transcripts.

The previous engine scored capitalised tokens as "proper nouns". Auto-captions
capitalise inconsistently, so on the real corpus that produced topics like "Hold",
"Get" and "Lock" -- all sentence-initial verbs -- which then produced 62% of titles
sharing one dead template. This module scores by FREQUENCY instead, which is immune to
capitalisation, and mines the whole-video transcript rather than a single 30s clip.
"""
import re
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

from transcript_clean import clean_tokens, normalize_for_tag

BLOCKED_TOPIC_WORDS: Set[str] = set("""
the a an and or but nor so yet if then than that this these those there here
of in on for with at by from as into onto about over under above below
off out up down back ahead apart aside besides within across against toward towards
via per than then so such both each either neither
per onto off out up down back again further once
i me my mine myself we us our ours ourselves you your yours yourself
he him his she her hers it its they them their theirs
is are was were be been being am do does did doing done have has had having
will would shall should can could may might must
not no none nothing never nobody
what when where who whom whose which why how
all any both each either neither few more most other others another such only own same too
also even still just ever always once twice else otherwise however therefore thus hence
get gets getting got go goes going went gone come comes coming came
take takes taking took taken make makes making made use uses used using
look looks looking looked see sees saw seen say says saying said
tell tells told ask asks asked feel feels felt seem seems seemed
happen happens happened think thinks thought want wants wanted
hold holds holding kept keep keeps catching caught scared scares guarding
know knows knew known like likes liked let lets do did
need needs wanted work works worked working try tries tried trying
start starts started starting turn turns turned put puts putting
help helps helped become becomes became show shows showed give gives gave
call calls called mean means meant like need want
going gonna wanna gotta lemme gimme dunno cause cuz
now chat bring some wait love lock lot lots bit bitte lotta kinda sorta
thing things stuff okay ok right
need needs wanted work works worked working try tries tried trying
start starts started starting turn turns turned put puts putting
help helps helped become becomes became show shows showed give gives gave
call calls called mean means meant
good bad best worst big small huge tiny great nice
real true full empty new old young fast slow easy hard long short high low
same different first second last next better worse
simple hidden secret truth myth myths facts lesson problem issue
time times day days week weeks year years month months minute minutes second seconds
part parts way ways everyone everybody someone somebody anyone anybody
something anything everything
guy guys girl girls bro bruh dude dudes folks people person
yeah yep yup nah oh wow hey well um uh hmm mhm erm ah eh oops oof
actually really very quite rather almost anyway because until during
lit yes maybe perhaps
man men woman women folk
video videos channel subscribe shorts short
music applause laughter sound noise
one two three four five six seven eight nine ten
""".split())

# Stage-direction and vocal-reaction words. These are usually removed upstream by
# `transcript_clean` (bracketed or parenthesised), but an UNBRACKETED one -- "she laughs
# loudly", "everyone cheers" -- would otherwise survive and reach a published title. A
# review demonstrated exactly that: "5 Facts About laughs That Change Everything" passed
# `validate_title` and was publishable. Layered defence, because the title gate is the
# last line and should not depend on upstream bracket-stripping having run.
STAGE_DIRECTION_WORDS: Set[str] = set("""
laugh laughs laughed laughing laughter laughs
chuckle chuckles chuckling
sigh sighs sighed sighing
grunt grunts grunted grunting
gasp gasps gasped gasping
cough coughs coughed coughing
sneeze sneezed
sniff sniffs sniffed
whisper whispers whispered whispering
shout shouts shouted shouting
scream screams screamed screaming
yell yells yelled
cheer cheers cheered cheering
clap claps clapped clapping
laughable
beep beeps beeped beeping
inaudible unintelligible
subtitle subtitles caption captions
background
""".split())

# Interjections and stretched reactions ("ohhhh", "noooo", "hahahaha", "whoooa") that
# survive the word list. Matched structurally rather than enumerated.
_STRETCHED = re.compile(r"(.)\1{2,}")
_INTERJECTION = re.compile(
    r"^(?:oh|ah|eh|hm|mm|uh|um|no|yes|yeah|yep|nope|nah|huh|hey|hi|whoa|wow|omg|"
    r"hmm|heh|hah|mmh)+h*$", re.IGNORECASE)
# Auto-captions spell most vocal reactions as consonant + a DOUBLED trailing vowel:
# "hAH HAA", "hoO!", "NoOO", "whoooa". _STRETCHED cannot see these (the letters are
# interleaved) and _INTERJECTION cannot match "hahaha" (a repeated syllable with a
# trailing vowel). Requiring the token to END in a doubled vowel keeps every legitimate
# word that merely doubles a consonant: book, coffee, letter, little, summer, moon.
_VOWEL_TAIL = re.compile(r"^[^aeiou]*[aeiou]{2,}$", re.IGNORECASE)
# "hahaha", "hehehe": a laughter syllable with an interleaved trailing vowel.
_LAUGHTER = re.compile(r"^(?:h[aeiou]){2,}h*$", re.IGNORECASE)

NICHE_KEYWORDS: Dict[str, List[str]] = {
    "gaming": [
        "game", "games", "gaming", "gamer", "gamers", "boss", "weapon", "weapons",
        "level", "play", "playthrough", "gameplay", "fps", "elden", "ring",
        "minecraft", "fortnite", "cod", "steam", "switch", "ps5", "xbox", "npc",
        "speedrun", "mod", "mods", "katana", "respawn", "rage", "gg",
        "open world", "speed run",
    ],
    "outdoors_survival": [
        "outdoor", "outdoors", "snow", "cave", "tent", "mountain", "mountains",
        "alaska", "survival", "survive", "bushcraft", "camp", "camping", "fire",
        "hike", "hiking", "woods", "forest", "fish", "fishing", "hunt", "hunting",
        "toboggan", "wilderness", "arctic", "frozen", "blizzard", "trail",
    ],
    "tech_ai": [
        "ai", "code", "coding", "software", "api", "model", "models", "python",
        "computer", "app", "apps", "algorithm", "data", "tech", "developer",
        "prompt", "chatgpt", "llm", "bot", "bots", "cloud", "server", "codebase",
        "laptop", "hardware", "machine learning", "neural network", "open source",
    ],
    "business_money": [
        "business", "money", "income", "cash", "finance", "sales", "client",
        "clients", "profit", "investing", "rich", "company", "startup", "crypto",
        "bitcoin", "market", "markets", "wealth", "revenue", "dollar", "dollars",
        "founder", "employees", "hiring", "million", "billion",
    ],
    "fitness_health": [
        "workout", "workouts", "gym", "muscle", "muscles", "diet", "protein",
        "training", "weight", "exercise", "body", "health", "fitness", "sleep",
        "calories", "calorie", "fat", "cardio", "lifting", "macros", "creatine",
    ],
    "comedy_entertainment": [
        "funny", "laugh", "hilarious", "crazy", "joke", "jokes", "prank",
        "pranking", "meme", "roast", "weird", "insane", "dumb", "epic", "fail",
        "comedy", "humor", "sketch", "skits",
    ],
    "science_education": [
        "science", "fact", "facts", "education", "study", "experiment",
        "space", "earth", "biology", "physics", "brain", "psychology", "history",
        "ancient", "universe", "chemistry", "sodium", "molecule", "protein",
        "research", "theory",
    ],
    "motivation_mindset": [
        "mindset", "discipline", "success", "habits", "habit", "focus", "goal",
        "goals", "productivity", "productive", "advice", "life", "stoic",
        "motivation", "wisdom", "grind", "consistency",
    ],
}

# Keywords confined to their niche. `NICHE_KEYWORDS` alone cannot separate niches,
# because so many of its entries are ordinary English that unrelated videos use
# constantly: "my boss", "level the flour", "play the song", "the game plan".
# On the real corpus, "game" and "play" alone filed a FOOTBALL commentary, a workplace
# comedy skit and a livestream donation callout as `gaming`.
#
# A niche may only be claimed if at least one of ITS strong keywords appears. Weak
# keywords still add score -- so a genuine gaming video that says "this game" still
# ranks well -- but they can never satisfy the gate on their own.
#
# CAVEAT: these lists are tuned against an 11-video corpus and are overfit by
# construction. test_topic_engine.py pins them to a committed corpus fixture so that
# any future retune is visible in a test diff rather than silent.
NICHE_STRONG_KEYWORDS: Dict[str, List[str]] = {
    "gaming": ["minecraft", "fortnite", "elden", "speedrun", "playthrough", "gameplay",
               "respawn", "npc", "ps5", "xbox", "fps", "katana", "rage", "gg", "cod",
               "open world", "speed run"],
    "outdoors_survival": ["outdoors", "survival", "survive", "bushcraft", "toboggan",
                          "wilderness", "alaska", "arctic", "blizzard", "hiking",
                          "camping", "snow"],
    "tech_ai": ["coding", "software", "chatgpt", "llm", "codebase", "algorithm", "python",
                "developer", "hardware", "laptop", "server", "cloud", "api",
                "machine learning", "neural network", "open source"],
    "business_money": ["business", "startup", "crypto", "bitcoin", "investing", "revenue",
                       "profit", "founder", "hiring", "dollars", "million"],
    "fitness_health": ["workout", "workouts", "gym", "muscle", "muscles", "cardio",
                       "calories", "macros", "creatine", "lifting", "protein"],
    "comedy_entertainment": ["hilarious", "prank", "pranking", "comedy", "sketch", "meme",
                             "roast", "jokes", "skits"],
    "science_education": ["science", "chemistry", "biology", "physics", "molecule",
                          "sodium", "psychology", "research", "theory", "experiment"],
    "motivation_mindset": ["mindset", "discipline", "productivity", "stoic", "habits",
                           "motivation", "consistency", "grind"],
}

NICHE_TAGS: Dict[str, List[str]] = {
    "gaming": ["#gaming", "#gamingcommunity", "#gameplay"],
    "outdoors_survival": ["#outdoors", "#survival", "#camping"],
    "tech_ai": ["#tech", "#techtok", "#software"],
    "business_money": ["#business", "#money", "#entrepreneur"],
    "fitness_health": ["#fitness", "#gym", "#fitnesstips"],
    "comedy_entertainment": ["#comedy", "#funny", "#humor"],
    "science_education": ["#science", "#facts", "#didyouknow"],
    "motivation_mindset": ["#motivation", "#mindset", "#selfgrowth"],
    "general_viral": ["#viral", "#trending"],
}

_NICHE_MIN_SCORE = 3.0
# A niche must beat the runner-up by this multiple, else the video is honestly ambiguous.
_NICHE_MARGIN = 1.25


def _is_reaction(token: str) -> bool:
    """True for vocal reactions and stretched spellings, which are never topics."""
    return bool(
        _STRETCHED.search(token)
        or _VOWEL_TAIL.match(token)
        or _LAUGHTER.match(token)
        or _INTERJECTION.match(token)
    )


def _overlaps(a: str, b: str) -> bool:
    """
    True when two phrases describe the same subject: they share a word, or one is a
    morphological variant of the other ("car"/"cars", "piece"/"scratch piece").

    Word-bounded on purpose. The previous check was a raw substring test in ONE direction
    only, so `car` and `cars` both survived, and `art` was silently swallowed by `party`.
    The `len(x) > 3` guard keeps unrelated short words distinct ("scar" vs "car").
    """
    for x in a.split():
        for y in b.split():
            if x == y:
                return True
            # Prefix match with a shared stem of >=3 collapses morphological variants
            # ("car"/"cars", "piece"/"pieces"). The old check used a raw SUBSTRING test,
            # which both missed those variants AND swallowed unrelated words ("art" inside
            # "party"). "art"/"party" are safe here because neither is a prefix of the other.
            if len(x) > 2 and len(y) > 2 and (x.startswith(y) or y.startswith(x)):
                return True
    return False


def _is_blocked(token: str) -> bool:
    if not token or len(token) < 3:
        return True
    if token in BLOCKED_TOPIC_WORDS:
        return True
    if token in STAGE_DIRECTION_WORDS:
        return True
    if any(ch.isdigit() for ch in token):
        return True
    if "'" in token:
        # Contractions are disfluencies, not topics, and they break hashtag safety.
        return True
    if _is_reaction(token):
        return True
    if len(token) > 4 and token.endswith("est"):
        return True      # superlative: cheapest, biggest
    if len(token) > 4 and token.endswith("ly"):
        return True      # adverb: really, literally
    # NOTE: no blanket "-ing" rule. It was tried and rejected: it kills legitimate domain
    # gerunds like "throttling" and "streaming" that are the best available topic for a
    # large class of videos. Verbs that matter are in BLOCKED_TOPIC_WORDS instead.
    return False


def _is_niche_term(token: str) -> bool:
    """
    Relaxed filter for NICHE classification only.

    The two filters differ in two ways, deliberately:
      - digits are allowed, because niche keywords include real product names ("ps5").
        Topic extraction still rejects digits, so a numeric name can never be a title.
      - a token only needs to be non-empty and not obviously a reaction, because niche
        keywords are matched by exact membership rather than used as display text.
    """
    if not token or len(token) < 2:
        return False
    if token in BLOCKED_TOPIC_WORDS or token in STAGE_DIRECTION_WORDS:
        return False
    if "'" in token or _is_reaction(token):
        return False
    return True


def mine_topic_phrases(transcript_text: str, limit: int = 8) -> List[Tuple[str, float]]:
    tokens = clean_tokens(transcript_text)
    if not tokens:
        return []
    # No `limit <= 0` guard: the `len(final) >= limit` cap below runs BEFORE each append,
    # so a non-positive limit already yields [].

    usable = [t if not _is_blocked(t) else None for t in tokens]

    singles: Counter = Counter()
    for t in usable:
        if t:
            singles[t] += 1
    scores: Dict[str, float] = {t: c ** 0.5 for t, c in singles.items()}

    # 2. Adjacent-token phrases, only where both tokens survive the blocklist. This is
    #    what recovers "cold start" and "toboggan run" rather than bare "cold" / "toboggan".
    #    A compound must REPEAT before it earns full weight: gluing two content words
    #    that merely happen to sit next to each other ("dollar car") invents a subject
    #    that does not exist. A one-off pair is demoted so it can rank low but never top.
    bigram_counts: Counter = Counter()
    for i in range(len(usable) - 1):
        a, b = usable[i], usable[i + 1]
        if not a or not b or a == b:
            continue
        bigram_counts[f"{a} {b}"] += 1

    for phrase, count in bigram_counts.items():
        weight = 1.5 if count >= 2 else 0.4
        scores[phrase] = scores.get(phrase, 0.0) + weight
        a, b = phrase.split(" ", 1)
        for j in range(len(usable) - 2):
            if usable[j] != a or usable[j + 1] != b:
                continue
            c = usable[j + 2]
            if not c or c in (a, b):
                break
            tri = f"{a} {b} {c}"
            scores[tri] = scores.get(tri, 0.0) + weight * 0.5
            break

    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))

    final: List[Tuple[str, float]] = []
    covered: List[str] = []
    for phrase, score in ranked:
        if score < 0.9:
            break
        # Bidirectional: a phrase is redundant if it overlaps anything already kept, in
        # EITHER direction, so "piece" and "scratch piece" cannot both consume slots.
        if any(_overlaps(phrase, c) for c in covered):
            continue
        safe = normalize_for_tag(phrase.replace(" ", "_"))
        if not safe or len(safe) < 3:
            continue
        if len(final) >= limit:
            break
        covered.append(phrase)
        final.append((phrase, round(score, 3)))
    return final


def classify_niche(transcript_text: str) -> str:
    """
    Classifies the video's niche by frequency-weighted keyword evidence.

    A domain term said six times is stronger evidence than one said once, so hits are
    weighted by occurrence count. Three gates must all pass:

      1. minimum weighted score,
      2. at least one STRONG keyword -- a term confined to this niche. Without this, "game"
         and "play" filed a football commentary, an office skit and a livestream donation
         callout as `gaming`, because those words are ordinary English.
      3. a margin over the runner-up.

    All evidence is derived from the transcript itself. An earlier signature also accepted
    pre-mined `phrases`; it was dead (nothing read them), and feeding it the plain string
    list that build_video_context produces used to raise
    `ValueError: too many values to unpack`. Removed rather than defended.
    """
    tokens = clean_tokens(transcript_text)
    if not tokens:
        return "general_viral"   # early exit; the min-score gate would also catch this

    # Multi-word keywords are matched against the raw token bigrams, NOT against the mined
    # topic list, which only surfaces the top 8 and would miss an occasional phrase.
    ngram_hay = " " + " ".join(
        f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)) + " "

    counts = Counter(t for t in tokens if _is_niche_term(t))

    ranked: List[Tuple[float, int, str]] = []
    for niche, kws in NICHE_KEYWORDS.items():
        strong = set(NICHE_STRONG_KEYWORDS.get(niche, ()))
        score = 0.0
        strong_hits = 0
        for kw in kws:
            if " " in kw:
                if f" {kw} " in ngram_hay:
                    score += 4.0
                    if kw in strong:
                        strong_hits += 1
                continue
            n = counts.get(kw, 0)
            if not n:
                continue
            score += 1.0 + (n ** 0.5)
            if kw in strong:
                strong_hits += n
        ranked.append((score, strong_hits, niche))

    ranked.sort(key=lambda r: (-r[0], r[2]))

    best_score, best_strong, best_niche = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0

    if best_score < _NICHE_MIN_SCORE:
        return "general_viral"
    if best_strong < 1:
        # No keyword confined to this niche appeared at all. "game" and "play" are not
        # evidence of gaming: a football commentary is full of them, as is any office
        # video that says "my boss" and any cooking video that says "level the flour".
        return "general_viral"
    # No explicit `runner_up > 0` guard: when it is 0.0 the comparison becomes
    # `best_score < 0.0`, which is already False for a non-negative score. The two
    # gates above are what stop a lone keyword; this one only breaks near-ties.
    if best_score < runner_up * _NICHE_MARGIN:
        return "general_viral"
    return best_niche
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest test_topic_engine -v`
Expected: PASS — 11 tests OK

- [ ] **Step 5: Verify against the real corpus**

```powershell
$env:PYTHONIOENCODING='utf-8'
python -c "import json,glob,collections; from topic_engine import mine_topic_phrases, classify_niche; vids=collections.defaultdict(list); [vids[json.load(open(f,encoding='utf-8')).get('video_id')].append(json.load(open(f,encoding='utf-8'))) for f in glob.glob('output/*.json')]; [print(v, classify_niche(' '.join(c.get('transcript_snippet','') for c in cs)), [p for p,_ in mine_topic_phrases(' '.join(c.get('transcript_snippet','') for c in cs), 4)]) for v,cs in vids.items()]"
```

Expected: each video prints exactly ONE niche, and topics are content words (no `hold`, `get`, `bro`).
Record the output — Task 5 uses it as its acceptance fixture.

- [ ] **Step 6: Commit**

```bash
git add topic_engine.py test_topic_engine.py
git commit -m "feat: add casing-independent video-level topic engine"
```

---

## Task 3: Wire Video Context Through the Pipeline

Topic detection needs the **whole video transcript**, not a 30 s clip. `app.py` already holds the full transcript when it renders clips. This task computes video context once per job and threads it to every clip — this is what makes niche consistent (per-clip disagreement on the real corpus goes 4/11 → 0/11; see the table at the top of this plan for why three different figures get quoted) and topics clean (fixing `sodium` vs `cuz`).

**Files:**
- Modify: `app.py` — add the `build_video_context` import at the top (with the other
  `title_tag_engine` imports), compute the context once in `run_clipping_job`, pass it to
  each clip's title call, and record `niche` / `seo_topic` in the clip metadata
- Modify: `clipper.py` — the same three changes. **This is a second entry point, not an
  afterthought.** The web UI and the CLI both write `output/*.json` and both run the
  critique gate; wiring only `app.py` leaves the CLI path on the old per-clip behaviour, so
  the two entry points emit different schemas for the same input.
- Modify: `batch_rerender.py` — **also required, and an earlier draft of this plan wrongly
  claimed it was not.** It rewrites the same `output/*.json` files and previously did
  `data["niche"] = smart_meta.get("niche", ...)`, i.e. it *overwrote* a good video-level
  niche with a fresh per-clip guess on every backfill. It needs a `_video_context_by_id`
  helper that groups clip files by `video_id` and builds one context per video.
- Modify: `title_tag_engine.py` — add the `topic_engine` import, add `build_video_context`,
  and widen `generate_smart_title_and_hashtags` to accept `video_context`
- Test: `test_video_context_parity.py`

**Interfaces:**
- Consumes: `topic_engine.mine_topic_phrases`, `topic_engine.classify_niche`,
  `topic_engine.NICHE_TAGS` (Task 2).
- Produces: `title_tag_engine.build_video_context(full_transcript_text: str) -> Dict[str, Any]`
  with keys `topics: List[str]`, `niche: str`, `niche_tags: List[str]`. **This function is
  added in Step 3 of this task**, not in Task 4.
- Produces: `generate_smart_title_and_hashtags(..., video_context: Optional[Dict] = None)`
  — a new **keyword-only** parameter with a default, so existing callers are unaffected.
  Write it as `*, video_context: Optional[Dict[str, Any]] = None` in the signature. The
  first implementation omitted the `*`, leaving `video_context` positional-or-keyword, so a
  future 4-positional call would silently bind to it. Audited: the only 4-positional call
  site in the repo is `batch_rerender.py`, which passes 2 positional plus
  `video_context=`, so adding the `*` breaks nothing.
- Produces: `batch_rerender._video_context_by_id(json_files) -> Dict[str, Dict]`, mapping
  `video_id` to that video's context, built from the union of its clips' `transcript_snippet`
  (the full transcript is not stored on disk, so the union is the best available signal and
  is still consistent within a video).

- [ ] **Step 1: Write the failing test**

> **Do not test this task by reading the source.** An earlier draft of this step asserted
> `app.py`'s source text contained the string `build_video_context(` exactly once, plus a
> literal `video_context=video_context`. Both assertions are worthless:
>
> - `src.count("build_video_context(") == 1` **cannot distinguish once-per-job from
>   once-per-clip.** Moving the call inside the clip loop still leaves exactly one
>   occurrence, and the whole suite stayed green.
> - Deleting the `niche` and `seo_topic` metadata lines **outright** also left the suite
>   green. Nothing tested the task's actual output.
>
> Review rejected the task on exactly this, and then rejected the fix too, because the
> first repair covered only `app.py` and left `clipper.py` on the same grep. The tests in
> Step 6 drive `run_clipping_job`, `clipper.process_video` and `batch_rerender.rerender_all`
> end to end over mocked I/O and assert on the metadata actually written to disk. All three
> entry points write the same `output/*.json` schema, so all three need behavioural
> coverage; a source-text check on one of them says nothing about the other two.
>
> This block therefore contains only the pure-function tests. Treat it as the *first*
> slice, not the whole task.

```python
# test_video_context_parity.py
import unittest
from pathlib import Path

import app as app_module
from app import app


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


class TestVideoContextParity(unittest.TestCase):

    def test_build_video_context_shape(self):
        from title_tag_engine import build_video_context
        ctx = build_video_context(" ".join(t["text"] for t in TRANSCRIPT))
        self.assertIn("topics", ctx)
        self.assertIn("niche", ctx)
        self.assertIn("niche_tags", ctx)
        self.assertIsInstance(ctx["topics"], list)
        self.assertIsInstance(ctx["niche"], str)

    def test_one_video_yields_one_niche_for_every_clip(self):
        """
        The core regression: clips of one video previously disagreed 9 times in 11.

        The original version of this test added `ctx["niche"]` to a set inside a loop, so
        the set always had exactly one element and the assertion could never fail. This
        version proves the fix matters FIRST (per-clip contexts genuinely disagree on this
        transcript) and only then that the shared context resolves the disagreement.
        """
        from title_tag_engine import build_video_context

        full = " ".join(t["text"] for t in TRANSCRIPT)
        ctx = build_video_context(full)
        self.assertTrue(ctx["topics"], "context must carry topics")

        clip_texts = ("sodium is what makes the flavour pop",
                      "my weapon level was too low",
                      "the boss fight on elden ring")

        # Guard: if per-clip extraction happened to agree, this test would prove nothing.
        per_clip = {build_video_context(c)["niche"] for c in clip_texts}
        self.assertGreater(len(per_clip), 1,
                           f"guard failed: per-clip contexts already agree ({per_clip}), "
                           f"so the shared-context test is vacuous on this transcript")

        # The shared context is one value, reused for every clip -- that is the whole point.
        self.assertIsInstance(ctx["niche"], str)
        self.assertNotEqual(ctx["niche"], "", "niche must always be a string")
        for c in clip_texts:
            self.assertEqual(build_video_context(full)["niche"], ctx["niche"],
                             "build_video_context must be deterministic")

    def test_metadata_records_the_context(self):
        from title_tag_engine import build_video_context
        ctx = build_video_context("sodium sodium sodium broth flavour")
        self.assertTrue(ctx["topics"], "context must carry topics for the title engine")
        self.assertNotEqual(ctx["niche"], "", "niche must always be a string")

    def test_status_endpoint_never_leaks_context_internals(self):
        app_module.JOBS["parity"] = _job_stub(
            status="completed", clips=[{"filename": "a.mp4", "video_id": "v"}])
        self.addCleanup(app_module.JOBS.pop, "parity", None)
        body = app.test_client().get("/api/status/parity").get_json()
        self.assertTrue(body["clips"])
        self.assertNotIn("video_context", body["clips"][0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_video_context_parity -v`
Expected: FAIL — `ImportError: cannot import name 'build_video_context' from 'title_tag_engine'`

- [ ] **Step 3: Add `build_video_context` to `title_tag_engine.py`**

Add the import with the file's OTHER IMPORTS at the top, immediately after the existing
`from config import ...` line (currently line 7). Do **not** leave it in the middle of the
file next to the function:

```python
from topic_engine import classify_niche, mine_topic_phrases, NICHE_TAGS
```

Then insert the function itself immediately above
`def generate_smart_title_and_hashtags(` (currently line 446):

```python
def build_video_context(full_transcript_text: str) -> Dict[str, Any]:
    """
    Derives the video-level context used by every clip of one source video.

    Computed ONCE per job from the whole transcript. Previously each ~30 s clip ran its own
    extraction, so clips of one video disagreed on niche and clip-level topics came out
    polluted with interjections ("cuz", "though", "guard").

    Measured on the real 11-video / 45-clip corpus:

        per clip, old engine (this module's classify_niche_heuristic)  9/11 disagree
        per clip, new engine (topic_engine.classify_niche)             4/11 disagree
        one context per video (this function)                          0/11 disagree

    The three rows measure three different code paths, so quote the right one: the 9/11
    figure is the pre-fix baseline for the engine that was actually running, the 4/11 is
    what the improved classifier still does if it is left per clip, and 0/11 is the
    property this function exists to guarantee.
    """
    text = full_transcript_text or ""
    phrases = mine_topic_phrases(text, limit=8)
    niche = classify_niche(text)
    return {
        "topics": [p for p, _ in phrases],
        "niche": niche,
        "niche_tags": list(NICHE_TAGS.get(niche, NICHE_TAGS["general_viral"])),
    }
```

> **Signature note.** `classify_niche` takes ONLY the transcript text. A code review found
> that an earlier `phrases=` parameter was dead (nothing read the value) and that feeding it
> the plain string list `build_video_context` returns used to raise
> `ValueError: too many values to unpack`. The parameter was removed, so do not pass it.

- [ ] **Step 4: Compute it once in `app.py` and pass it to every clip**

First, add `build_video_context` to the **top-level** import. `app.py:31` is currently a
single line, not a parenthesised block:

```python
from title_tag_engine import generate_smart_title_and_hashtags
```

Make it:

```python
from title_tag_engine import generate_smart_title_and_hashtags, build_video_context
```

Do **not** use a function-local import inside `run_clipping_job`; the plan's Step 3 forbids
that pattern for `title_tag_engine.py` and consistency matters.

Then, in `run_clipping_job`, immediately after the `if not transcript:` guard (which raises),
add:

```python
        # Video-level context: computed ONCE from the whole transcript, then reused by
        # every clip so titles, niche and hashtags stay consistent across the job.
        video_context = build_video_context(" ".join(t.get("text", "") for t in transcript))
```

Then change the per-clip call to pass it:

```python
                smart_meta = generate_smart_title_and_hashtags(
                    transcript_text=clip["text"],
                    category=clip.get("category", "high_value_insight"),
                    video_context=video_context
                )
```

And record the niche from the shared context rather than a per-clip guess. In the
`metadata` dict, replace `"niche": smart_meta.get("niche", "general_viral"),` with:

```python
                    "niche": video_context.get("niche") or smart_meta.get("niche", "general_viral"),
                    "seo_topic": (video_context.get("topics") or [None])[0],
```

- [ ] **Step 5: Accept `video_context` in the orchestrator**

In `title_tag_engine.generate_smart_title_and_hashtags`, change the signature to:

```python
def generate_smart_title_and_hashtags(
    transcript_text: str,
    category: str = "high_value_insight",
    api_key: Optional[str] = None,
    video_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
```

and, before the existing body, resolve the context so older callers that omit it still work:

```python
    if video_context is None:
        video_context = build_video_context(transcript_text)
```

Do **not** yet change what the function does with the context — Tasks 4 and 5 do that. This step only establishes the plumbing so the parity test passes.

- [ ] **Step 5b: Mirror the wiring in `clipper.py` (the CLI entry point)**

`clipper.py` is a second producer of the same `output/*.json` schema. If it is skipped, the
CLI keeps the per-clip bug and the two entry points diverge. Make the same three changes:

1. `clipper.py:26` — extend the import:
   ```python
   from title_tag_engine import generate_smart_title_and_hashtags, build_video_context
   ```
2. After the `if not transcript:` guard, before `create_windows` (currently just after the
   "Loaded N caption blocks" print), add:
   ```python
   # Video-level context: computed ONCE from the whole transcript, then reused by every
   # clip so titles, niche and hashtags stay consistent across the job. Must stay in sync
   # with app.run_clipping_job, which writes the same output/*.json schema.
   video_context = build_video_context(" ".join(t.get("text", "") for t in transcript))
   ```
3. In the per-clip title call, add `video_context=video_context`, and replace
   `"niche": smart_meta.get("niche", "general_viral"),` with the same two lines Step 4 uses.

- [ ] **Step 5c: Stop `batch_rerender.py` from re-introducing the bug**

`batch_rerender.py` rewrites the same files, and line 88 previously did
`data["niche"] = smart_meta.get("niche", "general_viral")` — overwriting a correct
video-level niche with a fresh per-clip guess on every backfill. It also never wrote
`seo_topic`, so the schema diverged a third way.

Add the import (`build_video_context` alongside `generate_smart_title_and_hashtags`), then
add this helper above `rerender_all`:

```python
def _video_context_by_id(json_files):
    """
    Build ONE video context per source video, shared by every clip of that video.

    Rerendering is a per-clip loop, so a naive fix would recompute the niche from each
    30 s snippet and each clip of one video would get a different niche -- exactly the bug
    this whole change set exists to remove. Only the `transcript_snippet` of already
    rendered clips is available here (the full transcript is not stored), so the context is
    built from the union of that video's snippets, which is a strict improvement over
    per-clip extraction and is still consistent within the video.
    """
    snippets = {}
    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        vid = data.get("video_id")
        snippet = data.get("transcript_snippet")
        if vid and snippet:
            snippets.setdefault(vid, []).append(str(snippet))
    return {vid: build_video_context(" ".join(parts)) for vid, parts in snippets.items()}
```

Then in `rerender_all`, add the context build. Put it **before** the
`json_files.sort(...)` line, not after as an earlier draft said — the sort reorders the
list, and the grouping must see every file:

```python
    # Computed once per video (not per clip) so every clip of one video agrees on niche.
    # Guarded because this is the one call in the function that sits outside every try: an
    # unhandled raise here aborts the whole batch before the summary accounting below runs,
    # which is exactly the failure mode the cut_and_format_clip guard further down exists to
    # prevent. Degrade to "no context for anyone" rather than dying.
    try:
        video_contexts = _video_context_by_id(json_files)
    except Exception as e:
        print(f"WARNING: could not build video contexts ({type(e).__name__}: {e}); "
              f"niche/seo_topic will be left untouched for this run.", flush=True)
        video_contexts = {}
```

Then, **before** the `if snippet and (not data.get("candidate_titles") ...)` conditional,
add the niche and `seo_topic` reconciliation. This placement is the whole point: put it
inside that conditional, as an earlier draft did, and it never runs.

```python
        # Video-level context for this clip's video. Any file reaching here has both a
        # truthy video_id and a truthy transcript_snippet (both checked above), so its id is
        # necessarily a key in video_contexts. There is no fallback to write, and an earlier
        # `or build_video_context(snippet)` was dead code that quietly implied a per-clip
        # path still existed.
        ctx = video_contexts.get(video_id)

        # A file that ALREADY carries `seo_topic` was written by a post-Task-3 producer
        # (app.py or clipper.py), which had the FULL transcript, so its niche is strictly
        # better evidence than the snippet union available here and is preserved. A file
        # WITHOUT `seo_topic` predates Task 3, and `git log -Svideo_context` proves no such
        # run existed before 93efe1a, so its niche is a 30 s per-clip guess and is replaced.
        # See the prose notes below for the evidence.
        migrated = False
        if ctx is not None:
            ctx_topic = (ctx.get("topics") or [None])[0]
            if "seo_topic" not in data:
                new_niche = ctx.get("niche") or data.get("niche") or "general_viral"
                if new_niche != data.get("niche"):
                    data["niche"] = new_niche
                    migrated = True
                if ctx_topic != data.get("seo_topic"):
                    data["seo_topic"] = ctx_topic
                    migrated = True
            elif data.get("seo_topic") != ctx_topic:
                # Post-Task-3 file: the existing niche came from the full transcript, so keep
                # it, but do not leave a stale seo_topic sitting beside it.
                data["seo_topic"] = ctx_topic
                migrated = True
```

and replace the metadata block inside the conditional with the following. Two changes
matter: the dead `or build_video_context(snippet)` is gone (`ctx` is now computed above),
and a bare `except: pass` becomes a printed warning, because the bare form made a failed
backfill completely invisible.

```python
                smart_meta = generate_smart_title_and_hashtags(
                    snippet,
                    data.get("category", "high_value_insight"),
                    video_context=ctx,
                )
                data["suggested_title"] = smart_meta.get("suggested_title", data.get("suggested_title"))
                data["suggested_hashtags"] = smart_meta.get("suggested_hashtags", data.get("suggested_hashtags"))
                data["candidate_titles"] = smart_meta.get("candidates", [])
                data["platform_metadata"] = smart_meta.get("platform_metadata", {})
                migrated = True
            except Exception as e:
                print(f"[{i}/{len(json_files)}] WARNING {jf.name}: title backfill failed "
                      f"({type(e).__name__}: {e})", flush=True)

        if migrated:
            try:
                with open(jf, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"[{i}/{len(json_files)}] WARNING {jf.name}: metadata write failed "
                      f"({type(e).__name__}: {e})", flush=True)
```

> **The niche and `seo_topic` writes must run for EVERY file.** Placed inside the
> `if snippet and (not data.get("candidate_titles") or "brutal truth" in ...)` conditional,
> as an earlier draft had them, they are dead code on the real corpus: all 45 files have
> `candidate_titles` and none has a title containing "brutal truth", so the branch is
> entered **0 times in 45**. `_video_context_by_id` parsed 45 files, built 11 contexts,
> and the result was discarded — 0 niches changed, 0 `seo_topic` keys written. Measured
> before and after a real `rerender_all` over a *copy* of the real corpus:
>
> ```
> BEFORE: 9/11 videos disagree on niche; 0/45 files carry seo_topic
> RUN   : Total: 45 | Succeeded: 45 | Failed: 0
> AFTER : 0/11 videos disagree on niche; 45/45 files carry seo_topic
> ```

> **Why the niche is preserved only when `seo_topic` is present.** An earlier draft
> preserved every existing niche on the stated grounds that "an existing niche was written
> by a run that had the full transcript; this script has only the union of that video's
> rendered snippets, which is weaker evidence". **That premise is false, and git proves
> it:**
>
> ```
> $ git log --oneline --all -Svideo_context -- app.py clipper.py batch_rerender.py
> 93efe1a   feat: compute video context once and share it across all clips
> e68c4c7   fix(task3): wire the other two entry points...
> $ git show 93efe1a^:batch_rerender.py | grep 'niche'
>                 data["niche"] = smart_meta.get("niche", "general_viral")
> ```
>
> `video_context` appears in **no commit before `93efe1a`**, and the newest file in
> `output/` is dated 2026-09-27 04:30 while that commit landed at 22:46. So all 45 on-disk
> niches were produced by a 30 s window — precisely the 9-of-11 disagreement this change set
> exists to remove. The old rule was therefore not a safety guard; it was a permanent
> freeze of known-bad data, re-affirmed on every future run, and it left the corpus worse
> than the naive per-clip fix it replaced (9/11 versus 4/11).
>
> The presence of `seo_topic` is the observable discriminator, and it is the only evidence
> available about which producer wrote a file. Pinned in both directions by
> `test_rerender_preserves_a_post_task3_niche_but_replaces_a_pre_task3_one`. Task 8 remains
> the right place for a proper whole-corpus migration, but it must not be the *only* place:
> a task that may not run is not a migration plan.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m unittest test_video_context_parity -v`
Expected: PASS

> **Test the wiring behaviourally, not by reading `app.py`'s source.** An earlier draft of
> this step asserted `app.py.count("build_video_context(") == 1`, which cannot distinguish
> "once per job" from "once per clip" — moving the call inside the clip loop survived it, as
> did deleting the `niche` / `seo_topic` lines entirely. Drive `run_clipping_job` with
> `unittest.mock.patch.object` over `extract_video_id`, `get_transcript`, `create_windows`,
> `critique_gate_search`, `cut_and_format_clip`, `generate_smart_title_and_hashtags`,
> `build_video_context`, `load_upload_config` and `OUTPUT_DIR`, then assert on the metadata
> actually written. Use the real `build_video_context` for classification tests and a stub
> whose `niche` differs from the stubbed `smart_meta` niche, so a fallback is detectable.

- [ ] **Step 7: Run the full suite**

Run: `python -m unittest test_transcript_clean test_topic_engine test_video_context_parity test_confirmed_bugs_fixes test_subtitles_taxonomy test_niche_integration test_title_and_uploader test_reported_bug_fixes`
Expected: `OK`.

> **Test counts change as review rounds add tests.** The per-module counts at the time of
> writing are: `test_transcript_clean` 16, `test_topic_engine` 39, `test_video_context_parity`
> 16, `test_confirmed_bugs_fixes` 9, `test_subtitles_taxonomy` 6, `test_niche_integration` 5,
> `test_title_and_uploader` 7, `test_reported_bug_fixes` 88 — 186 total. Treat the total as a
> floor, not a target: if a later task legitimately adds tests, a higher number is correct,
> not a regression. What matters is `OK`. (`test_jev_features` is excluded throughout — it
> makes a live network call to an API that returns 502, pre-existing and unrelated.)

- [ ] **Step 8: Commit**

```bash
git add app.py clipper.py batch_rerender.py title_tag_engine.py test_video_context_parity.py
git commit -m "feat: compute video context once and share it across all clips"
```

---

## Task 4: SEO Title Engine with a Junk Gate

This is the task that fixes the actual complaint. Two changes matter:

1. **Titles are built from the video topic, not from a per-clip "entity".** No more `Why Nobody Tells You The Truth About Hold`.
2. **A validation gate rejects any title containing a blocked word, a filler, or a duplicate**, so a bad topic can never be published even if the miner regresses.

**Files:**
- Create: `title_seo.py`
- Modify: `title_tag_engine.py` — `generate_candidate_titles` delegates to `title_seo`
- Test: `test_title_seo.py`

**Interfaces:**
- Consumes: `topic_engine.mine_topic_phrases` (Task 2), `transcript_clean.normalize_for_tag` (Task 1).
- Produces:
  - `SEO_PATTERNS: List[Dict[str, str]]` — the pattern bank.
  - `generate_seo_titles(topics: List[str], niche: str, category: str, limit: int = 5) -> List[Dict[str, str]]` — each `{id, framework, title, pattern_id, keyword}`. **Guaranteed** to contain no blocked word and to be ≤100 chars.
  - `validate_title(title: str) -> Tuple[bool, Optional[str]]` — `(ok, reason)`. The gate.

- [ ] **Step 1: Write the failing test**

```python
# test_title_seo.py
import unittest

from title_seo import (
    generate_seo_titles,
    validate_title,
    SEO_PATTERNS,
    ALL_PATTERNS,
    MAX_TITLE_CHARS,
    FRONT_LOAD_CHARS,
)
from topic_engine import BLOCKED_TOPIC_WORDS

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
        "Crème brûlée" out while leaving the emoji alone.
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_title_seo -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'title_seo'`

- [ ] **Step 3: Write minimal implementation**

```python
# title_seo.py
"""SEO + click-optimised title generation for YouTube Shorts.

Replaces the previous 5-template engine whose 62% fallback produced titles like
"Why Nobody Tells You The Truth About Hold...". Two structural differences:

  1. Titles are built from the VIDEO's mined topic, not from a per-clip "entity".
  2. Every title passes `validate_title` before it is returned, so a regression in the
     topic miner can never publish a junk title.
"""
import re
import zlib
from typing import Dict, List, Optional, Tuple

from transcript_clean import normalize_for_tag
from topic_engine import BLOCKED_TOPIC_WORDS, NICHE_TAGS

MAX_TITLE_CHARS = 100
FRONT_LOAD_CHARS = 45

_EMOJI = {
    "shocking_revelation": "\U0001F92F",
    "controversial_opinion": "\U0001F525",
    "compelling_story": "\U0001F3AD",
    "high_value_insight": "\U0001F4A1",
    "filler_banter": "\U0001F602",
}

SEO_PATTERNS: List[Dict[str, str]] = [
    {"id": "how_it_works", "framework": "Explainer", "template": "How {kw} Actually Works"},
    {"id": "the_proof", "framework": "Proof", "template": "The Proof That {kw} Is Real"},
    {"id": "why_it_happens", "framework": "Root Cause", "template": "The Real Reason {kw} Happens"},
    {"id": "count_facts", "framework": "List", "template": "{n} Facts About {kw} That Change Everything"},
    {"id": "nobody_tells_you", "framework": "Curiosity Gap", "template": "What Nobody Tells You About {kw}"},
    {"id": "i_tried", "framework": "First Person", "template": "I Tried {kw} For {n} Days"},
    {"id": "before_after", "framework": "Transform", "template": "{kw} Before And After You Know It"},
    {"id": "the_mistake", "framework": "Loss Aversion", "template": "The Mistake Everyone Makes With {kw}"},
    {"id": "explained_plainly", "framework": "Simple", "template": "{kw} Explained In Under A Minute"},
    {"id": "watch_this", "framework": "Curiosity", "template": "Watch This Before You Judge {kw}"},
]

_NICHE_PATTERNS: Dict[str, List[str]] = {
    "science_education": ["science_explained", "evidence"],
    "tech_ai": ["build_log", "comparison"],
    "business_money": ["income_breakdown", "mistake_money"],
    "fitness_health": ["transformation", "science_explained"],
    "gaming": ["run_showcase", "comparison"],
    "outdoors_survival": ["survival_story", "lesson"],
    "comedy_entertainment": ["reaction", "setup_punch"],
    "motivation_mindset": ["hard_truth", "story"],
}

_EXTRA_PATTERNS: List[Dict[str, str]] = [
    {"id": "science_explained", "framework": "Explainer", "template": "The Science Of {kw}"},
    {"id": "evidence", "framework": "Evidence", "template": "What The Research Actually Says About {kw}"},
    {"id": "build_log", "framework": "Build Log", "template": "I Built {kw} From Scratch"},
    {"id": "comparison", "framework": "Comparison", "template": "{kw}: Old School vs What Works Now"},
    {"id": "income_breakdown", "framework": "Breakdown", "template": "The Real Numbers Behind {kw}"},
    {"id": "mistake_money", "framework": "Loss Aversion", "template": "The Mistake That Costs You The Most On {kw}"},
    {"id": "transformation", "framework": "Transform", "template": "{n} Weeks Of {kw} Changed Everything"},
    {"id": "run_showcase", "framework": "Showcase", "template": "This {kw} Run Should Not Have Been Possible"},
    {"id": "survival_story", "framework": "Story", "template": "Surviving {kw} Changed How I See Everything"},
    {"id": "lesson", "framework": "Lesson", "template": "What {kw} Taught Me The Hard Way"},
    {"id": "reaction", "framework": "Reaction", "template": "I Was Not Ready For {kw}"},
    {"id": "setup_punch", "framework": "Comedy", "template": "When {kw} Goes Exactly As Planned"},
    {"id": "hard_truth", "framework": "Direct", "template": "The Hard Truth About {kw}"},
    {"id": "story", "framework": "Story", "template": "How {kw} Changed My Entire Week"},
    # The no-topic fallbacks are real patterns too, so `pattern_id` ALWAYS resolves against
    # ALL_PATTERNS. Keeping them out of the registry forces every consumer to learn that
    # `fallback_*` ids are special, which is exactly the inconsistency Task 5 would trip on.
    {"id": "fallback_search", "framework": "Searchable",
     "template": "Why This {cap} Moment Changed My Mind"},
    {"id": "fallback_explainer", "framework": "Explainer",
     "template": "This {cap} Detail Nobody Points Out"},
    {"id": "fallback_story", "framework": "Story",
     "template": "The Part Of This {cap} Story Everyone Skips"},
    {"id": "fallback_value", "framework": "Value",
     "template": "{cap} Rules I Wish I Knew Sooner"},
    {"id": "fallback_curiosity", "framework": "Curiosity",
     "template": "Watch To The End Of This {cap} Clip"},
]

ALL_PATTERNS: List[Dict[str, str]] = SEO_PATTERNS + _EXTRA_PATTERNS
_PATTERN_IDS = {p["id"] for p in ALL_PATTERNS}
_PATTERNS_BY_ID: Dict[str, Dict[str, str]] = {p["id"]: p for p in ALL_PATTERNS}

# Words the gate never tolerates: disfluencies plus the specific garbage tokens the old
# casing-driven extractor actually emitted on this corpus (Hold, Get, Lock, Bro, ...).
_TITLE_BLOCKED = set("""
um uh yeah yep bro bruh like basically literally actually
hold holds holding get put lock nope nothing stuff
shit fuck bitch asshole bastard cuz cause gonna wanna gotta
""".split())

_FILLER_RE = re.compile(
    r"(?:um+|uh+|erm+|ah+|hmm+|like|basically|literally|honestly|right|okay|ok|yeah|"
    r"yep|yup|wow|hey|well|so|just|really|very|quite|thing|things|stuff)",
    re.IGNORECASE,
)
_DUP_WORD_RE = re.compile(r"\b(\w+)(\s+\1\b)+", re.IGNORECASE)


def _cap(phrase: str) -> str:
    parts = phrase.split()
    if not parts:
        return phrase
    return " ".join(p if i == 0 else p.lower() for i, p in enumerate(parts)).capitalize()


def _sanitize_phrase(phrase: str) -> str:
    if not phrase:
        return ""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", phrase)]
    kept = [w for w in words
            if w.lower() not in _TITLE_BLOCKED
            and w.lower() not in BLOCKED_TOPIC_WORDS
            and len(w) > 2]
    if not kept:
        return ""
    return " ".join(kept[:3])


def validate_title(title: str) -> Tuple[bool, Optional[str]]:
    """
    The publication gate. Returns (ok, reason).

    Scope is deliberately limited to FLUENCY and STRUCTURE. It does NOT re-check whether
    a keyword is topical, and it must not try: `_sanitize_phrase` already guarantees the
    keyword slot is clean, while the templates contribute ordinary English that any
    topic-quality blocklist would wrongly reject. An earlier version of this gate
    rejected "The Sodium Trick Chefs Use To Cut Salt" for containing "the", which silently
    invalidated 100% of generated titles. Keep the two concerns separate.

    Emoji ARE allowed. An earlier version required the whole title to be ASCII, which
    rejected every generated title because the templates append an emoji. Only non-ASCII
    LETTERS are rejected, which is what actually keeps "Crème brûlée" out of a title.

    Nothing reaches the UI, the metadata JSON or the YouTube API without passing this.
    """
    if not title or not title.strip():
        return False, "empty title"
    if len(title) > MAX_TITLE_CHARS:
        return False, f"title exceeds {MAX_TITLE_CHARS} chars ({len(title)})"
    if _DUP_WORD_RE.search(title):
        return False, "title repeats a word"
    if title.count("...") > 1:
        return False, "multiple ellipses"

    words = re.findall(r"[A-Za-z']+", title.lower())
    if len(words) < 3:
        return False, "title has fewer than 3 words"
    if not any(len(w) >= 3 for w in words):
        return False, "no real word in title"

    # Non-ASCII letters (accented names) are rejected; emoji are not letters, so they pass.
    for ch in title:
        if ch.isalpha() and ord(ch) > 127:
            return False, f"title contains non-ascii letter {ch!r}"

    for word in words:
        if word in _TITLE_BLOCKED:
            return False, f"blocked word {word!r}"
        if _FILLER_RE.fullmatch(word):
            return False, f"filler word {word!r}"

    return True, None


def _ordered_patterns(niche: str) -> List[Dict[str, str]]:
    ordered: List[Dict[str, str]] = []
    for pid in _NICHE_PATTERNS.get(niche, []):
        if pid in _PATTERNS_BY_ID:
            ordered.append(_PATTERNS_BY_ID[pid])
    for p in SEO_PATTERNS:
        if p not in ordered:
            ordered.append(p)
    return ordered


def _fallback_titles(niche: str, category: str, rotation: int = 0) -> List[Dict[str, str]]:
    """
    Titles for when no mined topic survived sanitisation.

    Drawn from the same `fallback_*` entries in `_EXTRA_PATTERNS` as the rest of the engine,
    so `pattern_id` always resolves against `ALL_PATTERNS` and there is exactly one
    definition of each title shape. `keyword` is the niche tag, which is what the title is
    actually about when nothing better was available.
    """
    emoji = _EMOJI.get(category, "")
    tag = (NICHE_TAGS.get(niche) or ["#viral"])[0].lstrip("#")
    cap = _cap(tag)
    ids = ["fallback_search", "fallback_explainer", "fallback_story",
           "fallback_value", "fallback_curiosity"]
    rotated = ids[rotation % len(ids):] + ids[:rotation % len(ids)]
    out = []
    for pid in rotated:
        p = _PATTERNS_BY_ID[pid]
        title = p["template"].format(kw=tag, kw2=tag, cap=cap, n=5)
        if emoji and len(f"{title} {emoji}") <= MAX_TITLE_CHARS:
            title = f"{title} {emoji}"
        ok, _reason = validate_title(title)
        if not ok:
            continue
        out.append({
            "id": pid,
            "framework": p["framework"],
            "title": title,
            "pattern_id": pid,
            "keyword": tag,
        })
    return out


def _rotation_for(clip_text: str) -> int:
    return zlib.crc32((clip_text or "").encode("utf-8", "ignore")) % 997


def generate_seo_titles(
    topics: List[str],
    niche: str = "general_viral",
    category: str = "high_value_insight",
    limit: int = 5,
    rotation: int = 0
) -> List[Dict[str, str]]:
    keywords = [p for p in (_sanitize_phrase(t) for t in (topics or [])) if p]
    if not keywords:
        return _fallback_titles(niche, category, rotation=rotation)

    emoji = _EMOJI.get(category, "")
    patterns = _ordered_patterns(niche)
    counts = [5, 7, 3, 10, 6]

    results: List[Dict[str, str]] = []
    seen = set()

    for step in range(len(patterns)):
        if len(results) >= limit:
            break
        # Rotation shifts BOTH the pattern and the keyword. Rotating only the keyword
        # left every clip of one video using the same pattern with a different noun,
        # which is what makes a batch of uploads look auto-generated.
        pattern = patterns[(step + rotation) % len(patterns)]
        i = step + rotation
        kw = keywords[i % len(keywords)]
        kw2 = keywords[(i + 1) % len(keywords)] if len(keywords) > 1 else ""
        n = counts[i % len(counts)]
        title = pattern["template"].format(kw=kw, kw2=kw2 or kw, n=n, cap=_cap(kw))

        if emoji and len(f"{title} {emoji}") <= MAX_TITLE_CHARS:
            title = f"{title} {emoji}"

        ok, _reason = validate_title(title)
        if not ok:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "id": pattern["id"],
            "framework": pattern["framework"],
            "title": title,
            "pattern_id": pattern["id"],
            "keyword": kw,
        })

    if len(results) < limit:
        for extra in _fallback_titles(niche, category, rotation=rotation):
            if len(results) >= limit:
                break
            if extra["title"].lower() not in seen:
                seen.add(extra["title"].lower())
                results.append(extra)

    return results[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest test_title_seo -v`
Expected: PASS — **16 tests** OK (11 from the Step 1 block, 3 rotation tests from Step 6,
and 2 structural guards added during implementation — see the Step 6 note)

> **Pre-flight note (verified by extracting these blocks and running them).** Six defects
> were found in this section before implementation and all six are fixed above. Recorded
> here so nobody re-introduces them:
>
> 1. `_fallback_titles` emitted only `{id, framework, title}`, violating the declared
>    `{id, framework, title, pattern_id, keyword}` contract. Task 5 reads `keyword`, so a
>    batch whose topics all sanitise away would `KeyError` at upload time.
> 2. `test_no_title_contains_a_blocked_word` scanned the *whole title* against
>    `BLOCKED_TOPIC_WORDS` (430 entries, including "the", "of", "is", "that"). The
>    templates legitimately contain those, so the test failed on nearly every title — the
>    exact over-broad-gate failure `validate_title`'s docstring warns about. Now checks
>    the keyword slot and delegates the rest of the title to the gate.
> 3. `test_candidate_payload_carries_the_keyword_and_pattern` validated `pattern_id`
>    against `SEO_PATTERNS` only (10 ids), but niche patterns come from `_EXTRA_PATTERNS`
>    (14 more) and are emitted *first*, so it failed for the best-tuned niches. Now checks
>    `ALL_PATTERNS`.
> 4. `test_non_ascii_topics_do_not_leak_into_titles` used `str.isascii()`, but emoji are
>    appended by design, so it failed every time. Now checks for non-ASCII **letters**,
>    mirroring the gate.
> 5. The section had **two different "Step 6"** and the later one was about running the
>    tests. Renumbered: Step 6 adds the rotation tests, Step 7 runs them, Step 8 commits.
>    The old Step 6 also handed a *replacement* import block that dropped `ALL_PATTERNS`,
>    silently undoing fix 3; it now says to add `_rotation_for` to the existing block.
> 6. The old Step 6 told the implementer to **relax**
>    `test_candidate_payload_carries_the_keyword_and_pattern` to allow
>    `pattern_id.startswith("fallback_")`. That was treating the symptom of fix 1, and it
>    left `pattern_id` unresolvable against the pattern registry. Instead the five fallback
>    templates are now real entries in `_EXTRA_PATTERNS`, so `ALL_PATTERNS` (29 ids) covers
>    every candidate a caller can receive and the test needs no special case at all.
>
> Verified after fixing: Step 1's block alone gives **11 tests OK**, and with Step 6's three
> rotation tests appended, **14 tests OK**, exit code 0. (An earlier version of this note
> said 13; that was an arithmetic slip of mine, corrected during implementation when the
> implementer counted 14 from the plan and the discrepancy turned out to be in the note
> rather than in the blocks.)
>
> If a future change makes this section fail, check the design note in `validate_title`
> before loosening an assertion: two of these six bugs were a too-broad gate, and the
> tempting "fix" is to make the gate stricter, which invalidates 100% of output silently.
> The general lesson from four consecutive pre-flights: **the plan's test blocks are the
> least reliable part of it.** They have been verified by extraction and execution; the
> prose and the step numbering have not always matched them.

- [ ] **Step 5: Wire it into `title_tag_engine.generate_candidate_titles`**

Replace the whole body of `generate_candidate_titles` (currently `title_tag_engine.py`, the function that hardcodes 5 niche-gated branches) with:

```python
def generate_candidate_titles(
    transcript_text: str,
    category: str = "high_value_insight",
    video_context: Optional[Dict[str, Any]] = None
) -> List[Dict[str, str]]:
    """
    Generates 5 validated, SEO-optimised candidate titles for one clip.

    Previously this produced 5 niche-gated templates filled with a per-clip "entity",
    62% of which collapsed to "Why Nobody Tells You The Truth About {garbage}" because
    the entity came from casing-driven proper-noun matching on auto-captions.
    """
    if video_context is None:
        video_context = build_video_context(transcript_text)

    topics = video_context.get("topics") or []
    niche = video_context.get("niche") or "general_viral"
    return generate_seo_titles(
        topics,
        niche=niche,
        category=category,
        limit=5,
        rotation=_rotation_for(transcript_text),
    )
```

Add the imports at the top of `title_tag_engine.py`:

```python
from title_seo import generate_seo_titles, validate_title, _rotation_for
```

**Update the call site.** `generate_smart_title_and_hashtags` currently calls
`generate_candidate_titles(cleaned, category)` around the `# 1. Generate 5 diverse candidates`
comment. Change it to pass the shared context, otherwise the clip-level rebuild defeats
Task 3 and every clip regresses to its own topic:

```python
    # 1. Generate 5 diverse candidates (shares the video-level context)
    candidates = generate_candidate_titles(cleaned, category, video_context=video_context)
```

> **Verified against the real file:** the current signature is
> `def generate_candidate_titles(transcript_text: str, category: str = "high_value_insight")`
> at `title_tag_engine.py:275`, and the only call site is `title_tag_engine.py:506`, whose
> exact text is `    candidates = generate_candidate_titles(cleaned, category)`. Both anchors
> exist verbatim. `title_seo` is not yet imported anywhere, which is the correct pre-state.
>
> **Do not let this step's change break `score_and_rank_titles_with_jev`.** The line
> immediately after the call site is
> `best_id, niche, conf = score_and_rank_titles_with_jev(candidates, cleaned, api_key)`.
> Checked by AST: that function subscripts only `c["id"]` and `c["title"]` from each
> candidate, so the new payload satisfies it. The uniform 5-key shape is still required for
> a different reason — Task 5 reads `candidate["keyword"]` — not for this call site.

> **Undocumented consequence worth knowing: the Jev-derived niche is now dead weight in
> the metadata.** Two niches exist per clip after Task 3: `smart_meta["niche"]` from the Jev
> scorer (per clip, costs a live API call) and `video_context["niche"]` from
> `topic_engine.classify_niche` (per video, free). Every metadata writer — `app.py`,
> `clipper.py`, `batch_rerender.py` — uses
> `video_context.get("niche") or smart_meta.get("niche", "general_viral")`, and
> `classify_niche` never returns an empty string, so the context value always wins and the
> Jev niche is never persisted. That is the intended outcome (per-video consistency is the
> whole point of Task 3), but nobody should later be surprised that Jev's niche is
> discarded. The Jev call itself is still doing useful work: `best_id` selects the winning
> title and `conf` is the confidence. Removing the redundant niche derivation is out of
> scope here.

- [ ] **Step 6: Add the rotation regression tests**

Append to `test_title_seo.py`, and **add `_rotation_for` to the existing import block**
from Step 1 — do not replace that block, it also needs `ALL_PATTERNS`:

```python
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

    def test_every_template_uses_only_format_keys_that_are_supplied(self):
        """
        Structural guard on the pattern bank itself.

        Implementation found this one the hard way: a transcription slip turned one
        template's `{kw}` into `{cpn}` and the whole suite stayed green, because every test
        in this file uses the `science_education` or `general_viral` niche and
        `survival_story` is only reachable from `outdoors_survival`. The engine raised
        `KeyError: 'cpn'` on 16 of 40 rotations for that niche and on 3 real corpus clips,
        while `Ran 14 tests ... OK` printed. Check the bank, not the output.
        """
        import re as _re

        supplied = {"kw", "kw2", "n", "cap"}
        offenders = {}
        for pattern in ALL_PATTERNS:
            unknown = set(_re.findall(r"\{(\w+)\}", pattern["template"])) - supplied
            if unknown:
                offenders[pattern["id"]] = {"template": pattern["template"],
                                            "unknown": sorted(unknown)}
        self.assertEqual(offenders, {},
                         f"templates name format keys generate_seo_titles never supplies, "
                         f"so they raise KeyError at runtime: {offenders}")

    def test_no_niche_and_rotation_combination_raises(self):
        """
        Every niche x a wide sweep of rotations must produce titles, never raise.

        The `{cpn}` corruption was invisible to output-shape assertions precisely because no
        test varied the niche. Sweeping both dimensions surfaces a template that only some
        code paths reach.
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
```

> **Known limitation, accepted for this task, and it is the MODULO that causes it.**
> `_rotation_for` returns `crc32(text) % 997`, but `generate_seo_titles` consumes it as
> `patterns[(step + rotation) % len(patterns)]`. With 12 patterns for `science_education`,
> two clips collide whenever their rotations are congruent mod 12 — e.g. 672 and 480, both
> `0 mod 12`. Measured on the real corpus with the real per-video context:
>
> ```
> identical 5-title SETS across real clip pairs : 3 / 83
> identical FIRST titles (what the user sees)  : 4 / 83
> ```
>
> The 4/83 reproduces the figure recorded in Step 6's note below, so the plan was right and
> the implementer's guess that it was a probe artefact was wrong. It is a real ~5% collision
> rate on the pattern path. A fix is not in scope here: it needs the rotation to *select*
> rather than permute, which changes the candidate ordering Tasks 4-5 both depend on. If
> duplicate titles show up in production, that is where to start.
>
> **Known limitation, accepted for this task: the no-topic fallback path can only produce
> 5 distinct titles.** `_fallback_titles` holds 5 templates and `rotation` merely permutes
> them, so the entire reachable pool is 5 no matter how many clips a video has. Measured on
> the real corpus, the 7 videos with no surviving topic produce 17 colliding top-title pairs
> across their clips (worst: `JheRzFxeSBg`, 4 collisions among 5 clips).
>
> This is a real quality gap, not a test artifact, and it is reachable: Task 8 backfills
> already-rendered videos, and `7APGcnUv2zQ` mines topics like `zeke` and `sodium` that are
> weak but survive, while other videos mine nothing usable. The fix — more fallback
> templates, or making rotation select rather than permute — is deliberately **not** in this
> task, because the pattern path (the common case, and the one every new render uses) is
> already 95% collision-free at 4 of 83 real clip pairs. Revisit if backfill output looks
> repetitive.

- [ ] **Step 7: Run the tests**

Run: `python -m unittest test_title_seo test_title_and_uploader -v`
Expected: PASS

> If `test_title_and_uploader` fails on an assertion about the old title strings, that is
> an intentional behaviour change. Update those assertions to the new contract (validated
> titles, keyword present) — do not weaken them back to accepting junk.

- [ ] **Step 8: Commit**

```bash
git add title_seo.py title_tag_engine.py test_title_seo.py
git commit -m "feat: SEO title engine with junk-rejection validation gate"
```

---

## Task 5: Hashtag Engine

The current tag builder pads to 7 with 3 from a fixed per-niche list plus leftovers, none of which relate to the clip. This builds a short, relevant set from the actual topic.

**Files:**
- Create: `hashtag_engine.py`
- Modify: `title_tag_engine.py` — hashtag block in `generate_smart_title_and_hashtags`
- Test: `test_hashtag_engine.py`

**Interfaces:**
- Consumes: `topic_engine.NICHE_TAGS` (Task 2), `title_seo.validate_title` gate style (Task 4), `transcript_clean.normalize_for_tag` (Task 1).
- Produces:
  - `build_hashtags(topics: List[str], niche: str, max_tags: int = 5) -> List[str]`
  - `build_metadata_tags(hashtags: List[str], topics: List[str], niche: str) -> List[str]` — the broader YouTube `tags` field, distinct from hashtags.

- [ ] **Step 1: Write the failing test**

```python
# test_hashtag_engine.py
import unittest

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_hashtag_engine -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hashtag_engine'`

- [ ] **Step 3: Write minimal implementation**

```python
# hashtag_engine.py
"""Hashtag and YouTube-tag strategy for Shorts.

The previous builder emitted 7 tags: 3 from a fixed per-niche list plus leftover entity
tags, padded to a quota with #shorts/#viral/#fyp. None of them related to the clip, and
#shorts/#viral were themselves frequently dropped by an order-dependent length guard.
This builds a short, relevant set anchored on the video's actual topic.
"""
import re
from typing import Dict, List

from transcript_clean import normalize_for_tag
from topic_engine import BLOCKED_TOPIC_WORDS, NICHE_TAGS

MAX_HASHTAGS = 5
# #Shorts is the only non-negotiable reach tag. #fyp is included only when budget allows,
# because a 3-tag set that includes the topic beats a 5-tag set of generic reach tags.
_ALWAYS = ["#Shorts"]
# At most 2 niche tags, so at least 2 slots remain for the clip's actual topic.
_MAX_NICHE_TAGS = 2
# Bare URL tokens: alphanumeric, unblocked, and completely useless as a hashtag.
_URL_FRAGMENTS = {
    "http", "https", "www", "com", "org", "net", "edu", "gov", "io", "co",
    "htm", "html", "php", "aspx", "url", "link", "links", "linkinbio", "subscribe",
}


def _clean_tag(raw: str) -> str:
    """
    Turns a topic phrase into a single valid hashtag.

    `normalize_for_tag` joins words with "_" so `build_metadata_tags` can recover a
    readable phrase. That is wrong for a hashtag: "_" is not alphanumeric, so "elden ring"
    would become the invalid tag "#elden_ring". A multi-word topic is therefore
    CONCATENATED ("#eldenring"), which is the standard convention for a compound subject.

    This is safe here in a way it was not in the old engine: the old one concatenated
    bigrams it had invented from adjacent words; this phrase is adjacent words that both
    survived the blocklist and actually occur in the transcript.
    """
    body = normalize_for_tag(raw.replace(" ", "_")).replace("_", "")
    if not body or len(body) < 3:
        return ""
    if body in BLOCKED_TOPIC_WORDS:
        return ""
    if any(ch.isdigit() for ch in body):
        return ""
    # A whole URL like "https://x" is already rejected above (its digits and punctuation
    # fail the checks), but auto-captions also split URLs into bare tokens -- "https",
    # "www", "com" -- and each of those is alphanumeric and unblocked, so it would become
    # a valid-looking tag that helps nobody. 20 tokens in the real corpus do this.
    if body.lower() in _URL_FRAGMENTS:
        return ""
    return f"#{body}"


def build_hashtags(topics: List[str], niche: str = "general_viral",
                  max_tags: int = MAX_HASHTAGS) -> List[str]:
    """
    Builds up to `max_tags` relevant hashtags: 1 reach tag, <=2 niche tags, then topics.

    The topic slots are guaranteed reserved. An earlier design appended 3 niche tags
    before any topic, which consumed the whole budget and made every clip's tags
    identical to every other clip in the same niche.
    """
    topics = [t for t in (topics or []) if t]
    reserved_for_topics = min(3, max(0, len(topics)))
    niche_budget = min(_MAX_NICHE_TAGS, max(0, max_tags - len(_ALWAYS) - reserved_for_topics))

    tags: List[str] = []

    def add(tag: str) -> None:
        if tag and tag not in tags and len(tags) < max_tags:
            tags.append(tag)

    for tag in _ALWAYS:
        add(tag)
    for tag in NICHE_TAGS.get(niche, NICHE_TAGS["general_viral"])[:niche_budget]:
        add(tag)
    for topic in topics[:3]:
        add(_clean_tag(topic))
    # Reach bonus last, so it can never displace a topic tag.
    if len(tags) < max_tags:
        add("#fyp")

    return tags[:max_tags]


def build_metadata_tags(hashtags: List[str], topics: List[str],
                        niche: str = "general_viral") -> List[str]:
    out: List[str] = []
    seen = set()

    def add(value: str) -> None:
        v = value.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)

    add(str(niche).replace("_", " "))
    for topic in (topics or [])[:6]:
        clean = normalize_for_tag(topic)
        if not clean:
            continue
        add(clean.replace("_", " "))
        if clean.endswith("s") and len(clean) > 4:
            add(clean[:-1])
    for tag in (hashtags or [])[:4]:
        add(tag.lstrip("#"))

    return out[:15]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest test_hashtag_engine -v`
Expected: PASS — 9 tests OK

> **Pre-flight note (verified by extracting these blocks and running them).** Four defects
> were found in this section before implementation, all now fixed above:
>
> 1. `test_no_blocked_or_junk_tags` asserted the blocked-word rule over the **whole** output
>    and therefore failed on `#Shorts` itself — `shorts` is in `BLOCKED_TOPIC_WORDS` (430
>    entries) because the platform name appears in nearly every transcript, so it must never
>    be *mined as a topic*. The reach tag is chosen deliberately and never passes through
>    `_clean_tag`, so the implementation was right and the test was wrong. This is the same
>    conflation that broke Task 4's blocked-word test: **a blocklist built for one purpose
>    being asserted against a different one.**
> 2. `test_metadata_tags_are_broader_than_hashtags` ended with
>    `assertGreater(len(set(tags)), 0)`, which is implied by the `assertTrue(tags)` above it
>    and can never fail. Replaced with assertions that carry weight.
> 3. **A real quality gap, not a test bug:** `_clean_tag` rejects a whole URL like
>    `https://x`, but auto-captions also split URLs into bare tokens — `https`, `www`, `com` —
>    each of which is alphanumeric and unblocked, so each would become a valid-looking tag
>    that helps nobody. **20 tokens in the real corpus do this.** Added a `_URL_FRAGMENTS`
>    guard and a test.
> 4. Step 5's instruction to update the downstream `"niche": niche,` was ambiguous: that
>    exact line occurs **twice**, and the other one is inside `build_video_context` where
>    `resolved_niche` does not exist. A find-and-replace would `NameError`. The step now
>    names line 575 explicitly. Step 5 also said nothing about the four dead constants left
>    behind, including a `MAX_HASHTAGS` of 7 that shadows the new value of 5.
>
> Verified after fixing: **9 tests OK, exit code 0.**

- [ ] **Step 5: Wire it into `title_tag_engine`**

Replace the hashtag combination block in `generate_smart_title_and_hashtags` — currently
**lines 515-557**, from the `# 3. Generate tailored hashtags` comment through
`final_hashtags = combined_tags[:MAX_HASHTAGS]` (43 lines) — with:

```python
    # 3. Tags: a short, topic-anchored set. Replaces the 7-tag quota pad.
    topics_for_tags = video_context.get("topics") or []
    resolved_niche = video_context.get("niche") or "general_viral"
    final_hashtags = build_hashtags(topics_for_tags, resolved_niche)
    raw_tags = build_metadata_tags(final_hashtags, topics_for_tags, resolved_niche)
```

> **Change only the `"niche": niche,` INSIDE the returned dict at `title_tag_engine.py:575`,
> which becomes `"niche": resolved_niche,`. Do not do a find-and-replace.**
>
> That exact line `        "niche": niche,` appears **twice** in the file: once at
> **line 471** inside `build_video_context` and once at **line 575** in the dict
> `generate_smart_title_and_hashtags` returns. Line 471 is correct as-is and must not
> change — `resolved_niche` does not exist in that scope, so a blind replace is an
> immediate `NameError` the moment anything calls `build_video_context`. Line 575 is the
> only one to edit. Verified: after line 557, the string `niche` appears nowhere else in
> the file except line 575, so there is no third site to worry about.

**Delete the old `raw_tags = [t.lstrip("#") for t in final_hashtags]` line.** This
instruction originally read "leave both lines alone", and it was **wrong** — a defect found
during implementation, after I had already briefed an implementer with it.

The new block above assigns `raw_tags` from `build_metadata_tags(...)`. The old line sat at
567, *after* the replaced block ended at 557, so leaving it in place would have **overwritten
the metadata engine's output with a naive lstrip of the hashtags** — silently, with no test
failing, because the value would still be a plausible list of strings. The whole
`build_metadata_tags` function, and Task 5's second deliverable, would have been discarded
in production while every test stayed green.

Keep only the *use* of `raw_tags`, which is the `"tags": raw_tags` entry in the returned
`platform_metadata` at (originally) line 583. Verified in the committed file: `raw_tags` is
assigned exactly once, at the `build_metadata_tags` call, and read once, in the
`platform_metadata` dict.

Add the import:

```python
from hashtag_engine import build_hashtags, build_metadata_tags
```

**Then delete the four now-dead constants** in `title_tag_engine.py`: `NICHE_HASHTAGS`
(line 45), `MAX_HASHTAGS = 7` (line 98), `RESERVED_HASHTAGS` (line 99) and
`OPTIONAL_HASHTAGS` (line 100).

**Keep `_entity_to_tag`.** The plan said to delete it "if nothing else references it", and
during implementation `test_reported_bug_fixes.py:486` turned out to reference it. Deleting it
would have broken that test for no benefit — it is a small helper, and the old entity engine's
`extract_topical_entities` is still live elsewhere in the orchestrator.
Leaving them is not harmless: `title_tag_engine.MAX_HASHTAGS` is **7** while
`hashtag_engine.MAX_HASHTAGS` is **5**, and `test_reported_bug_fixes` already imports the
wrong one. Two same-named constants with different values is a live trap.

- [ ] **Step 6: Run the tests**

Run: `python -m unittest test_hashtag_engine test_reported_bug_fixes -v`
Expected: PASS

> **`TestBug08_ViralDropped` needs FOUR changes, not one.** Its assertions encoded the old
> padding contract. Measured against the new engine
> (`build_hashtags(["sodium","broth"], "fitness_health")` →
> `['#Shorts', '#fitness', '#gym', '#sodium', '#broth']`):
>
> | assertion | old | new engine | change |
> |---|---|---|---|
> | `assertIn("#shorts", tags)` | passes | **fails** | the reach tag is now `#Shorts` (capital S). `assertIn` is case-sensitive. |
> | `assertIn("#viral", tags)` | passes | **fails** | `#viral` is no longer guaranteed. The topic budget is reserved, so a clip with usable topics gets topic tags instead. Reachable tags are `#Shorts` always and `#fyp` only when there is spare budget. |
> | `assertLessEqual(len(tags), title_tag_engine.MAX_HASHTAGS)` | passes | passes | **Silently wrong.** It compares against 7 while the new cap is 5. Point it at `hashtag_engine.MAX_HASHTAGS`. |
> | `test_niche_tags_still_present` | passes | passes | Works by luck: it hardcodes `("#fitness","#gym","#workout","#health")` and the engine happens to emit `#fitness` and `#gym`, which are the first two of `NICHE_TAGS["fitness_health"]` (`['#fitness','#gym','#fitnesstips']`). Better to assert against `NICHE_TAGS["fitness_health"]` directly so a change to that list cannot silently unpin it. |
>
> Update these to the new contract — `#Shorts` always present, at most
> `hashtag_engine.MAX_HASHTAGS` tags, topic-derived tags present — rather than deleting the
> tests. `test_no_duplicate_tags` needs no change and should stay as-is: it is still a real
> property, and `test_hashtag_engine.test_no_duplicate_tags` covers the same ground from
> the engine side.

- [ ] **Step 7: Commit**

```bash
git add hashtag_engine.py title_tag_engine.py test_hashtag_engine.py
git commit -m "feat: topic-anchored hashtag and metadata tag engine"
```

---

## Task 6: End-to-End Quality Gate on the Real Corpus

Unit tests prove the parts; this task proves the whole pipeline on the 45 real clips, with an explicit budget that must not regress.

**Files:**
- Create: `test_title_quality_budget.py`

**Interfaces:**
- Consumes: `title_tag_engine.generate_smart_title_and_hashtags`, `build_video_context`.
- Produces: nothing; this is a guard test.

- [ ] **Step 1: Write the failing test**

```python
# test_title_quality_budget.py
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
        """
        offenders = []
        for vid, clips in sorted(_by_video().items()):
            caller_topics = {t.lower() for t in (VIDEO_CONTEXTS[vid].get("topics") or [])}
            for clip in clips:
                for cand in self._meta(clip).get("candidates") or []:
                    kw = (cand.get("keyword") or "").lower()
                    if kw not in caller_topics:
                        offenders.append((vid, kw, sorted(caller_topics)[:4]))
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} candidate keywords are not topics the caller passed, so the "
            f"engine is not reading the shared video context: {offenders[:6]}")

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
        Zero disagreements, computed the way the pipeline actually computes them.

        An earlier version built the context PER CLIP, which is the pre-Task-3 path, and then
        allowed `len(multi) <= len(by_video) // 2` -- up to 5 of 11 videos disagreeing. That
        contradicted the plan's own acceptance bar of 0, and measured a path the pipeline
        stopped using in Task 3. Now it groups by `video_id`, builds one context from the union
        of that video's snippets -- independently of the shared cache, which is what makes it
        usable as a check on that cache -- and demands agreement.

        This is the corpus-level parity check the whole change set needed and never had.
        Without it, B-2 in the Task 3 review -- a backfill that rewrote 0 of 45 files while
        reporting success -- would have gone unnoticed again.
        """
        pools = _by_video()
        self.assertGreaterEqual(len(pools), 5,
                                f"only {len(pools)} videos in the corpus; too few to judge")
        multi = {}
        for vid, clips in pools.items():
            union = " ".join(c["transcript_snippet"] for c in clips)
            by_video_niche = {build_video_context(union)["niche"]}
            stored = {c.get("niche") for c in clips}
            if len(stored) > 1:
                multi[vid] = ("stored clips disagree", sorted(stored))
            elif by_video_niche != stored:
                multi[vid] = ("stored != recomputed", sorted(by_video_niche | stored))
        self.assertEqual(multi, {},
                         f"{len(multi)} videos fail niche parity: {multi}")


if __name__ == "__main__":
    unittest.main()
```

> **Pre-flight note, then re-measured twice.** Every figure below was measured against
> the implemented code, not the draft. The block above is the file as committed, and the two
> were verified identical by extracting the fence and executing it.
>
> **First pass (before Tasks 4 and 5 existed).** Four defects, all fixed in the block above:
> the `@SKIP`-on-everything trap that let the gate report `OK (skipped=7)` while measuring
> nothing; `test_one_niche_per_video` measuring the pre-Task-3 per-clip path *and* permitting
> 5 of 11 videos to disagree against an acceptance bar of 0; generated and stored assertions
> interleaved so the report could not distinguish them; and Task 5's hashtag output never
> budgeted at all.
>
> **Second pass, after Tasks 1-5 shipped.** Seven of the then-eleven tests went green and the
> four that stayed red were all correct: three `TestStoredMetadata` tests plus
> `test_one_niche_per_video_is_exact`, every one of them reporting the stale on-disk corpus
> that Task 8 rewrites. A fifth defect surfaced here and it was mine — the block made **~135
> live HTTPS calls** to the Jev API and took **231 seconds**, because
> `generate_smart_title_and_hashtags` ends in `score_and_rank_titles_with_jev`, which POSTs
> whenever `get_jev_api_key()` returns a key, and it does on a configured machine. A quality
> budget that takes four minutes and depends on a third party's uptime is not a guard on this
> codebase. `setUpModule` now stubs the key.
>
> **Third pass, after review.** A fresh reviewer attacked the hardening and found that several
> of its own numbers were wrong, including two that pointed the wrong way. All corrected, and
> the corrections are the point of this note:
>
> 1. **The cost of the unstubbed file was understated, not overstated.** Instrumenting
>    `score_and_rank_titles_with_jev` over a real run gives **356** entries per file, not the
>    ~135 claimed. Whole-file measurement, same machine, same corpus, stub neutralised and
>    restored: **1.51 s stubbed, 585.7 s unstubbed, identical verdict** — 309x. A second
>    unstubbed run of the previous 16-test version gave 241 s, so the honest claim is a range
>    of 240-590 s, the spread being the third party's latency, not ours. Per-call timings were
>    dropped from the file's docstring for the same reason: two attempts to measure them
>    disagreed by 100x, and one of them was silently leaking the live call it claimed to have
>    stubbed.
> 2. **A per-clip context breaks parity for 0 of 9 multi-clip videos, not 2 of 9.** The
>    "2 of 9" figure understated the margin the parity assertion has. Likewise a per-clip
>    context gives distinct titles per video of `[3,5,5,5,5,5,5,5,5]`, not "5 every time";
>    the `[3,...]` entry makes the title-side argument *weaker*, not stronger, which is why
>    the keyword seam check exists at all.
> 3. **The keyword assertion was tautological as documented.** 225 of 225 candidate keywords
>    are exactly an element of the topic list handed to the engine, because
>    `generate_seo_titles` builds its keyword list from the topics it is given
>    (title_seo.py:269) and indexes it (title_seo.py:291). Read as a claim about topic content
>    it asserts nothing. It is a **seam** check — the engine's topics versus the caller's — and
>    the test is now named and documented as that, with an explicit warning not to "fix" it by
>    recomputing topics independently, which would destroy the only detector for the defect the
>    whole commit exists for.
> 4. **The topic predicate modelled `_clean_tag` backwards.** It asserted
>    `tag in topic`, but `_clean_tag` *concatenates*: `'toboggan run'` publishes as
>    `#tobogganrun`. Measured, the old predicate was false for **all 30** multi-word topics in
>    the corpus, so the assertion was being satisfied by a different, single-word topic — the
>    "asserts a value is present rather than right" defect, in the very test written to fix
>    that. The replacement states the published form as a specification rather than importing
>    the transformation, and an anti-vacuity assertion stops it passing on a corpus where every
>    context lost its topics.
> 5. **Both limits were imported from the module they judge.** `MAX_TITLE_CHARS` and
>    `MAX_HASHTAGS` are the engine's own constants, so raising either moved the budget with
>    it and nothing fired — the recorded "constants that can never fire" failure mode. Limits
>    are now pinned as literals (YouTube's 100 characters is an external fact; 5 tags is a
>    project decision) with a separate test requiring the engine to agree with them, so a
>    change becomes a decision rather than a silent widening.
> 6. **The claim that the budget's own fixture was unauditable was wrong, and stated
>    backwards.** The comment said a per-clip cache "makes all clips of a video agree", which
>    is the inverse of what happens. It is also fixable: `test_one_niche_per_video_is_exact`
>    already re-derives the union context independently, so comparing that against the cache
>    is two independent derivations, not self-comparison. Two fixture tests were added and
>    they kill all three fixture mutants.
> 7. **Six real coverage gaps, five of them now closed:** the main path's `youtube.tags`
>    reverting to the hand-rolled `#` strip (the very defect the silent-clip test exists for,
>    one path over); the silent path's title going unchecked for length; only one candidate
>    returned per clip; rotation shifting the keyword but not the pattern (the pre-Task-4
>    defect, invisible to the collapse test); and topic mining collapsing from 8 topics to 1.
>    The YouTube description being emptied is now pinned too. Two gaps are left open and
>    documented rather than papered over: the orchestrator ignoring the ranking is
>    indistinguishable from "took the last candidate" while the API is stubbed, and the
>    empty-transcript branch cannot be detected as *deleted*, because deleting it lets the main
>    path handle the input and honour the context just as well.
> 8. **The niche-parity test was filed under the wrong class.** It reads from disk and is one
>    of the four correctly-red tests, but it sat in `TestGeneratedOutput`, whose docstring
>    promises green. It now has its own class, `TestCorpusVsEngine`.
>
> **Mutation result: 17 of 19 killed**, up from 0 of 4 on production seams before the first
> hardening pass. The two survivors are a deliberate no-op control (which must survive) and the
> "empty branch deleted" gap documented above. Every other mutant — all four production
> seams, both shipped-title limits, both limit-drift mutants, all three fixture mutants, and
> all five closed coverage gaps — is detected by name.
>
> **Final shape: 24 tests, 1.51 s, 4 failures**, all four the stale on-disk corpus that Task 8
> rewrites. Runtime went 231 s -> 1.51 s across these fixes.

```powershell
$env:PYTHONIOENCODING='utf-8'
python -c "import json,glob,collections; from title_tag_engine import build_video_context, generate_smart_title_and_hashtags; from title_seo import validate_title; cs=[json.load(open(f,encoding='utf-8')) for f in glob.glob('output/*.json')]; rs=[(c.get('filename'), generate_smart_title_and_hashtags(c['transcript_snippet'], video_context=build_video_context(c['transcript_snippet']))) for c in cs if c.get('transcript_snippet')]; print('generated:',len(rs)); print('invalid  :',sum(1 for _,m in rs if not validate_title(m['suggested_title'])[0])); [print(' ',f,'|',m['suggested_title'],'|',m['suggested_hashtags']) for f,m in rs[:12]]"
```

Expected: `invalid: 0` and the sample titles reference real topics, not `Hold` / `Get`.

- [ ] **Step 4: Commit the guard**

```bash
git add test_title_quality_budget.py
git commit -m "test: add whole-pipeline title quality budget"
```

---

## Task 7: UI — Stop Truncating, Surface the Keyword

The card used `line-clamp-2`. **The original diagnosis in this section was wrong, and the correction matters for Task 8.** It claimed the clamp is why the user saw `Why Nobody Tells You The Truth About Hold...`. Measured, the ellipsis in that string is part of the STORED TITLE ITSELF: **34 of the 45 stored titles contain a literal `...` and none ends in one** — the old engine appended an emoji after the ellipsis, e.g. `'Why Nobody Tells You The Truth About Hold... 😳'`, and the user's reported string is that value minus the emoji. (An earlier draft of this correction claimed all 45 *ended* in `...`. Measured, that is 0 of 45. Recorded because the count and the predicate were both wrong, which is the same failure this plan has now made twice.) `line-clamp-2` does not fire on this corpus at any width: **0 of 45 cards are cut at 390/430/640/768/820/1024/1280/1440/1600 px, with a maximum of 2 line boxes.**
>
> **The method matters, and my first one was unfalsifiable.** It compared `lines_visible` against `lines_needed` where `lines_visible` was derived from the element's own `clientHeight` — on an element that no longer carried the clamp, so the two are equal *by construction*. That test cannot fail for any corpus at any width, including one that is badly truncated, and it was recorded here as if it were evidence. The measurement above restores the clamp and asks whether `scrollHeight > clientHeight`, then counts line boxes with `Range.getClientRects()`.
>
> Worth recording the near-miss in the other direction too: `scrollHeight / 19.25` reads `2.03` at 6 of 8 widths, which looks exactly like the clamp firing. It is not — integer `scrollHeight` (39 px) against two lines of 19.25 px (38.5 px). A reader who stops at that number will reach the opposite conclusion from the correct one. So Step 1 is live code that prevents *future* truncation (the new engine allows titles up to `MAX_TITLE_CHARS` = 100) but changes nothing a user can see today. The complaint the task was written for is a DATA problem and is fixed by Task 8, not by this step.


**Files:**
- Modify: `templates/index.html:1894` (title `<h4>`), `templates/index.html:1991` (candidate chip) — **line numbers are pre-edit; after this task they are 1902, 2000 and 1191 respectively. Anchor on the class strings instead: `leading-snug line-clamp-2`, `cand.framework || 'hook'`, `let activeJobId = null;`**
- Test: covered by `test_title_quality_budget` for data; UI verified manually (Step 5).

**Interfaces:**
- Consumes: `suggested_title`, `suggested_hashtags`, `seo_topic` (Task 3), `candidate_titles[].keyword` (Task 4).
- Produces: visible full title; visible SEO keyword.

- [ ] **Step 1: Remove the two-line clamp on the card title**

Replace, at `templates/index.html` around line 1894:

> The line below is the **pre-edit** form and is deliberately absent from the file now; a plan-vs-file check that compares it will report drift that is not drift.

```html
              <h4 class="text-sm font-bold text-white leading-snug line-clamp-2">${title}</h4>
```

with:

```html
              <h4 class="text-sm font-bold text-white leading-snug break-words">${title}</h4>
```

- [ ] **Step 2: Show the SEO keyword above the title**

Insert immediately before that `<h4>` (i.e. after the `uploadReceiptsHtml` line at 1892):

```html
              ${c.seo_topic ? `<p class="text-[10px] font-mono text-slate-400 mb-1 truncate">SEO: ${escapeHtml(c.seo_topic)}</p>` : ''}
```

> **Verified for Step 2.** The card template is `grid.innerHTML = clips.map((c, i) => {…})`
> at `templates/index.html:1806`, so `c` is the per-clip metadata object and `c.seo_topic` is
> the right accessor. `seo_topic` appears nowhere in `index.html` today, which is expected —
> it is a new metadata field, and `/api/clips` (which is what `loadClips()` calls) returns the
> whole per-file metadata dict, so it arrives automatically once Task 8 has written it.
> `escapeHtml` is defined at 1271, well before this point, so it is in scope.

- [ ] **Step 3: Show the keyword on each candidate chip**

Inside the candidate-chip template, immediately after the `cand.framework` badge at
`templates/index.html:1992` (the chip's `innerHTML` spans 1990-1993), add the keyword:

```js
            ${cand.keyword ? `<span class="text-[9px] uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono shrink-0 ml-2">${escapeHtml(cand.keyword)}</span>` : ''}
```

> **Correction made during implementation (2026-09-28).** The block above originally read
> `${escapeHtml(cand.keyword || cand.framework || 'hook')}`, and it shipped that way once before
> being caught. It is a **visible regression on today's data**: `candidate_titles[]` in all 45
> stored clips carries only `{id, framework, title}` — 225 of 225 candidates, 0 with a
> `keyword` — because Task 8 has not regenerated the corpus. So the `|| cand.framework` arm
> fires on every single card and prints the framework badge twice ("CURIOSITY GAP" next to
> "CURIOSITY GAP"). The `|| 'hook'` tail is also unreachable: `title_seo` gives every candidate
> both a non-empty `framework` and, since Task 4, a non-empty `keyword`, so the only way to
> reach `'hook'` is a candidate missing both.
>
> The conditional form is strictly better — the chip is absent on today's data rather than
> wrong, and becomes informative the moment Task 8 writes `keyword`. Verified against all three
> shapes (no keyword, keyword present, neither present).

> `c.warning` **does not exist and never will.** `app.py:268` sets `job["warning"]` — on
> the JOB, not on any clip. The card template maps over `data.clips` from `/api/clips`, which
> returns only the per-file metadata dicts, so `c.warning` would be `undefined` on every card
> and the strip would never render. The plan's own wording ("when `c.warning` is set on the
> job") contradicted itself.
>
> Worse, the data is already gone by the time the cards render: `checkJobStatus()` sets
> `activeJobId = null` at line 1754 and only then calls `loadClips()` at 1760, and it never
> reads `data.warning` at all. So the warning is currently unreachable from the UI. Capture
> it in module scope, then render it once above the grid.

- [ ] **Step 4: Surface the shortfall warning above the clip grid**

Three edits, all in `templates/index.html`:

1. Next to `let activeJobId = null;` (line 1190), add:
   ```js
   let currentJobWarning = null;
   ```

2. In `checkJobStatus()`, in the `data.status === 'completed'` branch, capture the warning
   **before** `activeJobId` is nulled (currently line 1754):
   ```js
   if (data.status === 'completed') {
     clearInterval(pollInterval);
     currentJobWarning = data.warning || null;
     activeJobId = null;
   ```
   Reset it to `null` in the `failed` branch too, or a previous run's warning will persist
   over an unrelated failure.

3. In `loadClips()`, after `const clips = data.clips || [];` (line 1791) and before
   `grid.innerHTML = clips.map(…)` (line 1806), prepend a single strip:
   ```js
   const warnHtml = currentJobWarning
     ? `<div role="status" aria-live="polite" class="col-span-full mb-2 text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">⚠️ ${escapeHtml(currentJobWarning)}</div>`
     : '';
   ```
   and prefix the grid assignment with it: `grid.innerHTML = warnHtml + clips.map(…)`.

   Render it **once above the grid**, not inside the per-card template: the warning is about
   the job as a whole, and repeating it on all five cards would be noise. `col-span-full`
   is required because the grid is a multi-column layout.

> **Known limitations left open after review (2026-09-28), recorded rather than fixed.**
>
> 1. **A page reload discards the warning permanently**, while the server still holds it:
>    `app.py:45` `JOB_RETENTION_SECONDS = 3600`, so `/api/status/<id>` would still return
>    `warning` for up to an hour. The client nulls `activeJobId`, clears the interval and
>    persists nothing, so after a reload there is no job id anywhere and the strip is gone for
>    good. Recovering it needs `sessionStorage` plus a fetch in `DOMContentLoaded` — that is a
>    new persistence mechanism, and this app has none, so it is a design change rather than a
>    fix to what was built. Not done here on purpose.
> 2. **`warnHtml` cannot fire on the empty-grid path.** It is built just after
>    `const clips = data.clips || [];`, but `loadClips()` returns early at the
>    `clips.length === 0` branch before reaching `grid.innerHTML`, so the strip is never
>    inserted. Production reachability is very low — `app.py:259-260` raises when
>    `completed_clips` is empty — and "no warning when there are no clips" is arguably right.
>    Left as an expression that cannot fire on one of its two branches rather than contorting
>    the code to make it reachable.
> 3. **A 404 from `/api/status` polls forever.** Pre-existing, in the function this task edited:
>    measured 4 status requests in 5 s and still polling, with the progress bar stuck and no
>    error shown. Not introduced here and not fixed here; it needs a terminal-state guard.
> 4. **The `SEO:` line sits above a variable-height title in a stretched grid row**, so titles
>    in one visual row are offset by up to ~36 px depending on whether that card has a `SEO:`
>    line. Pre-existing 1-vs-2-line raggedness (~19 px) is the same class. Layout is out of
>    scope; noted so the next person does not read the raggedness as a new bug.
>
> Two further items were found and **fixed**: the `SEO:` line shipped at `text-slate-500`, which
> is 3.07:1 on `slate-800`, 3.75:1 on `slate-900` and 4.24:1 on the page background — below
> WCAG AA's 4.5:1 for normal text, and the dimmest text on a card whose title sits at 14.63:1.
> It is now `text-slate-400` (5.71 / 6.96 / 7.87). And the warning strip arrived
> asynchronously with no live region, so no screen reader announced it; it now carries
> `role="status"`.

- [ ] **Step 5: Verify manually**

Run: `python app.py`, open http://127.0.0.1:5000

Confirm:
- Every card shows its title in full across 2+ lines, not cut at "About Hold…".
- A small `SEO: sodium` line appears above the title on newly generated clips.
- The upload modal's candidate chips show a keyword chip.
- A run that cannot fill the requested clip count shows ONE amber strip above the grid. Check
  this explicitly by requesting more clips than the transcript supports (e.g. `top_k: 5` on a
  short video). A strip that silently never appears is the exact failure Step 4 had.

- [ ] **Step 6: Commit**

```bash
git add templates/index.html
git commit -m "feat: show full titles and SEO keyword on clip cards"
```

---

## Task 8: Backfill the 45 Existing Clips

`batch_rerender.py` already backfills titles, but it also re-downloads and re-encodes every
clip through ffmpeg. This adds a metadata-only path.

> **Pre-flight note (verified by extracting the script below and running it against a copy of
> the real corpus).** Six defects were found in this section and are fixed above. Five were
> reported by review, one was found here.
>
> 1. **The per-video context was built from the FIRST clip's snippet only** (old line 67:
>    `ctx = build_video_context(snippet)`), cached per `video_id`. This directly contradicts
>    Task 3's own pinned behaviour — `test_batch_rerender_helper_uses_every_snippet_of_the_
>    video_not_just_the_first` calls reading `parts[0]` "a regression to per-clip
>    extraction" — and contradicts `_video_context_by_id`'s own docstring. Two shipped code
>    paths would have produced two different "video contexts" for the same video. It now
>    unions every clip's snippet, grouped by `video_id`, and shares one result across them.
> 2. **Niche reconciliation was gated behind the title gate.** On `GATE REJECTED` the script
>    did `continue`, so the file kept its stale per-clip niche *and* got no `seo_topic`.
>    Calibration Finding #4 in this document records a past incident where the gate rejected
>    **100% of titles**; against that history, gating the migration behind the gate means the
>    migration can silently no-op and report only `rejected_by_gate`. Niche and `seo_topic`
>    are now written unconditionally, before the gate is consulted, exactly as the Task 3
>    `batch_rerender` fix does.
> 3. **`stats["failed"] -= 1` on a write error** (old lines 106-107). `failed` was never
>    incremented for that file, so a single write failure drove the counter **negative** and
>    the summary reported `failed: -1`. Now `+= 1`.
> 4. **`ensure_ascii=False` on write**, while `app.py`, `clipper.py` and `batch_rerender.py`
>    all use the `json.dump` default of `True`. The corpus contains emoji, so a backfill
>    rewrites the byte representation of all 45 files, and the next `batch_rerender` pass
>    re-escapes them again — a permanent diff churn between two writers of the same file.
>    Matched to the producers.
> 5. **Step 4's acceptance was `updated ≈ 45`**, which is unfalsifiable: no bound, and
>    `updated` counts every file processed whether or not any value actually changed. Now
>    `updated == 45`, `failed == 0`, `rejected_by_gate == 0`, and a separate `changed` count
>    that only increments when a written value differs.
> 6. **The backup directory was not gitignored.** Step 3 creates `output_backup_<timestamp>/`
>    as a *sibling* of `output/`, and `.gitignore` only excludes `output/*.mp4` — so the
>    backup's ~1.1 GB of mp4s is not ignored. Step 6's explicit `git add output/` avoids it
>    by luck, not by design. `.gitignore` now covers `output_backup_*/`.
>
> The Acceptance Criteria at the end of this document were also stale in two ways and are
> fixed separately: they still named the old `test_one_niche_per_video` (removed — it allowed
> half the videos to disagree, and measured the pre-Task-3 per-clip path), and they omitted
> `test_title_and_uploader` entirely.

**Files:**
- Create: `backfill_titles.py`
- Modify: `.gitignore` — ignore the backup directory
- Test: run against a copy of the corpus (Step 3), then the real quality budget (Step 6)

**Interfaces:**
- Consumes: `title_tag_engine.build_video_context`, `title_tag_engine.generate_smart_title_and_hashtags`,
  `title_seo.validate_title`; the `output/*.json` shape.
- Produces: updated `output/*.json` with new `suggested_title`, `suggested_hashtags`,
  `candidate_titles`, `platform_metadata`, plus `niche` and `seo_topic`.

- [ ] **Step 1: Ignore the backup directory in `.gitignore`**

Append to `.gitignore`:

```gitignore
# ---- PRE-BACKUP COPIES (Step 3 of the SEO plan) ----
output_backup_*/
```

- [ ] **Step 2: Write the backfill script**

```python
# backfill_titles.py
"""
Regenerate titles, hashtags, niche and seo_topic for existing clips in output/ WITHOUT
re-encoding any video. Useful after changing the title engine, and far faster than
batch_rerender.py (which re-downloads and re-renders every clip through ffmpeg).

Two rules govern what is written, and both matter more than they look:

  * ONE context per source VIDEO, built from the union of that video's clips' snippets.
    Building it from a single clip's snippet is the pre-Task-3 per-clip bug wearing a
    different hat -- two clips of one video would then disagree again.

  * `niche` and `seo_topic` are reconciled for EVERY file, unconditionally, BEFORE the
    title gate is consulted. Gating the migration behind the gate means a gate regression
    silently freezes the corpus: this plan's own Calibration Finding #4 records the gate
    once rejecting 100% of titles, and the symptom would be a summary reading
    `rejected_by_gate: 45` with every file still holding its old per-clip niche.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from title_tag_engine import build_video_context, generate_smart_title_and_hashtags
from title_seo import validate_title

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# json.dump is called WITHOUT ensure_ascii, matching app.py, clipper.py and
# batch_rerender.py. Passing ensure_ascii=False here would rewrite the byte representation
# of every file in a corpus containing emoji, and the next batch_rerender pass would
# re-escape them -- permanent churn between two writers of the same file.
_JSON_WRITE = {"indent": 2}


def _load(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("metadata root is not a JSON object")
    return data


def build_contexts(files: List[Path]) -> Dict[str, Dict[str, Any]]:
    """
    ONE video context per source video, from the UNION of that video's snippets.

    Only the `transcript_snippet` of already-rendered clips is available here -- the full
    transcript is not stored -- so the union is the best available signal. It is still
    consistent within the video, which is the property that matters.
    """
    snippets: Dict[str, List[str]] = {}
    for path in files:
        try:
            data = _load(path)
        except Exception:
            continue
        vid = data.get("video_id")
        snippet = data.get("transcript_snippet")
        if vid and snippet:
            snippets.setdefault(vid, []).append(str(snippet))
    return {vid: build_video_context(" ".join(parts)) for vid, parts in snippets.items()}


def backfill(
    output_dir: Path = OUTPUT_DIR,
    filter_video_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Rewrites title/hashtag metadata for every clip JSON. Returns a summary counter."""
    files = sorted(output_dir.glob("*.json"))
    if filter_video_id:
        files = [f for f in files if filter_video_id in f.name]

    stats = {"total": 0, "updated": 0, "changed": 0, "skipped_no_transcript": 0,
             "rejected_by_gate": 0, "failed": 0}

    # Built once for the whole run, from the SAME (possibly filtered) file list, so a
    # filtered run unions only the clips it is about to rewrite.
    try:
        contexts = build_contexts(files)
    except Exception as exc:
        print(f"  [Backfill] could not build video contexts ({type(exc).__name__}: {exc}); "
              f"niche and seo_topic will be left untouched.")
        contexts = {}

    for path in files:
        stats["total"] += 1
        try:
            data = _load(path)
        except Exception as exc:
            print(f"  [Backfill] unreadable {path.name}: {exc}")
            stats["failed"] += 1
            continue

        # Snapshot so `changed` can mean "files whose content differs", not "keys that
        # differ". Counting per key reported 196 for 45 files, which reads as a bug.
        before_dump = json.dumps(data, sort_keys=True)

        snippet = data.get("transcript_snippet") or ""
        if not snippet.strip():
            stats["skipped_no_transcript"] += 1
            continue

        video_id = data.get("video_id") or ""
        ctx = contexts.get(video_id)
        if ctx is None:
            # Only reachable when the context build failed, or a file's video_id was not
            # present at grouping time. Never fall back to a per-clip context: that is the
            # bug this task exists to remove.
            ctx = None

        # ONE write path, reached whether or not the title gate passes.
        #
        # The first version of this loop mutated `data["niche"]` / `data["seo_topic"]`
        # BEFORE the gate and then `continue`d on rejection -- so the mutation existed only
        # in memory and the file was never written. Running it against a copy of the real
        # corpus showed the half-fix immediately: 5 gate-rejected files kept their old
        # per-clip niche, so niche disagreement only fell 9 -> 1 instead of 9 -> 0, and
        # `seo_topic` landed on 40 of 45 files rather than 45. The unconditional write is
        # only unconditional if the WRITE is unconditional too.
        dirty = False

        def put(key: str, value) -> None:
            """Assign, noting whether this actually changes what is on disk."""
            nonlocal dirty
            if data.get(key) != value:
                dirty = True
            data[key] = value

        # ---- unconditional: niche and seo_topic, regardless of the title gate ----
        if ctx is not None:
            put("niche", ctx.get("niche") or data.get("niche") or "general_viral")
            put("seo_topic", (ctx.get("topics") or [None])[0])

        # ---- gated: the title itself ----
        title = None
        try:
            meta = generate_smart_title_and_hashtags(
                snippet,
                category=data.get("category", "high_value_insight"),
                video_context=ctx,
            )
            title = meta.get("suggested_title", "")
            ok, reason = validate_title(title)
        except Exception as exc:
            print(f"  [Backfill] generation failed for {path.name}: {exc}")
            stats["failed"] += 1
            ok, reason = False, str(exc)

        if ok:
            put("suggested_title", title)
            put("suggested_hashtags", meta.get("suggested_hashtags", []))
            put("candidate_titles", meta.get("candidates", []))
            put("platform_metadata", meta.get("platform_metadata", {}))
            stats["updated"] += 1
        else:
            # Keep the old title -- but the niche and seo_topic set above are still written.
            print(f"  [Backfill] GATE REJECTED {path.name}: {title!r} ({reason}); "
                  f"keeping the existing title, migrating niche/seo_topic anyway")
            stats["rejected_by_gate"] += 1

        if dry_run:
            print(f"  [DRY] {path.name} -> {title} | "
                  f"niche={data.get('niche')} | seo_topic={data.get('seo_topic')!r} | "
                  f"{'dirty' if dirty else 'unchanged'}")
            continue

        if not dirty:
            continue

        if json.dumps(data, sort_keys=True) != before_dump:
            stats["changed"] += 1

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, **_JSON_WRITE)
        except Exception as exc:
            print(f"  [Backfill] write failed for {path.name}: {exc}")
            stats["failed"] += 1
            stats["updated"] -= 1

    return stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    target = args[0] if args else None
    result = backfill(filter_video_id=target, dry_run=dry)
    print()
    print("BACKFILL SUMMARY")
    print(f"  total            : {result['total']}")
    print(f"  updated          : {result['updated']}")
    print(f"  values changed   : {result['changed']}")
    print(f"  no transcript    : {result['skipped_no_transcript']}")
    print(f"  gate rejected    : {result['rejected_by_gate']}")
    print(f"  failed           : {result['failed']}")
    if dry:
        print("\n(Dry run - no files were written. Re-run without --dry-run to apply.)")
```

- [ ] **Step 3: Back up `output/` before writing anything**

Only the 45 `.json` files are at risk; the ~1.1 GB of mp4s is not touched by this script and
copying it is a waste of disk. Back up the metadata:

```powershell
$stamp = Get-Date -Format yyyyMMdd_HHmmss
$dest = "output_backup_$stamp"
New-Item -ItemType Directory -Path $dest | Out-Null
Copy-Item -Path "output\*.json" -Destination $dest
(Get-ChildItem $dest -Filter *.json | Measure-Object).Count   # must print 45
```

> **The command this replaces silently did nothing, and it is the step whose entire job is
> to be the safety net.** `Copy-Item -Recurse output "output_backup_$stamp" -Include *.json`
> exits **0, prints nothing, and creates no directory at all** on this PowerShell —
> `-Include` does not filter a recursive copy whose source is a directory the way it filters
> a glob. Reproduced in isolation: destination does not exist afterwards, stderr empty. So the
> step would have reported success and left **no backup whatsoever**, and nothing in the
> plan could have detected it, because the step's own verification line came after a command
> that never errored. Hence the explicit count above, which is the only part that can fail.
> `New-Item` plus `-Path "output\*.json"` was measured to produce exactly the 45 files.

Expected: `output_backup_<stamp>/` exists and contains 45 `.json` files, and
`git status --short` shows nothing (Step 1's `.gitignore` entry).

- [ ] **Step 4: Dry run against a COPY first, never the live corpus**

```powershell
$env:PYTHONIOENCODING='utf-8'
python -c "import shutil,tempfile,pathlib,backfill_titles as b; d=pathlib.Path(tempfile.mkdtemp())/'output'; d.mkdir(); [shutil.copy2(p,d/p.name) for p in pathlib.Path('output').glob('*.json')]; print(b.backfill(output_dir=d))"
```

> **This step was mislabelled: the command it runs is not a dry run.** It calls
> `b.backfill(output_dir=d)` without `dry_run=True`, so it really writes — to the
> *copy*, which is what makes it safe, but not to a scratch directory. Two things follow,
> and both bit the implementer:
>
> - The expected block below shows `values changed : 45`, which is what a **write**
>   produces. A genuine dry run gives `values changed : 0` and a byte-identical digest.
>   Both were run and measured: dry 0, real 45, digest unchanged by the dry run.
> - The block shows the script's `__main__` labels, while this command prints a **dict
>   repr**. They are the same six numbers under different names, so read past the format
>   difference rather than concluding the numbers disagree.

Expected — note the script prints aligned labels, not a dict, and three of the six keys are
renamed (`changed` -> `values changed`, `skipped_no_transcript` -> `no transcript`,
`rejected_by_gate` -> `gate rejected`):
```
  total            : 45
  updated          : 45
  values changed   : 45
  no transcript    : 0
  gate rejected    : 0
  failed           : 0
```
Then re-run `test_title_quality_budget` against the copy to confirm the copy really was
migrated before touching the real thing.

**Any `failed` other than 0, or any `rejected_by_gate` other than 0, is a STOP.** Do not
proceed to Step 5. A non-zero `rejected_by_gate` means the gate has regressed, and applying
anyway leaves a half-migrated corpus.

- [ ] **Step 5: Apply to the real corpus**

Run: `python backfill_titles.py`

Expected **exactly** (these are the script's printed labels, not dict keys — an earlier
draft of this line quoted `skipped_no_transcript`, which the script never prints):
`total 45`, `failed 0`, `no transcript 0`, `gate rejected 0`, and
**`with seo_topic 45 / 45` and `disagreeing 0`** in the Step 6 check.

**Tasks 4 and 5 are implemented and wired, so all six numbers below are hard requirements.**
Measured by extracting this script and running it against a copy of the real corpus at the
current tree: `total 45, updated 45, values changed 45, no transcript 0, gate rejected 0,
failed 0`, and `dead_titles` goes **28 -> 0**.

> **The licence this section used to carry has been removed deliberately.** It said
> `rejected_by_gate > 0` was *expected* before Task 5, because pre-flighting gave
> `updated: 40, rejected_by_gate: 5` while the old title engine was still generating
> `Why Nobody Tells You The Truth About Hold...` and the gate was correctly refusing those
> five. That was true when written and is **false now**. With the new engine the gate rejects
> nothing, so a non-zero count is the Calibration Finding #4 failure mode returning and it is a
> **STOP**, full stop. Leaving the old wording in place would have made a genuine gate
> regression look like expected behaviour -- the failure this plan has already produced three
> times, in a different form each time.

- `gate rejected` > 0 is a **STOP**. It means the gate is refusing titles the new engine built.
- `failed` > 0 is always a STOP.
- `no transcript` > 0 is a STOP: all 45 clips have a snippet.

> **Measured, running this script against a copy of the real corpus at the current tree**
> (Tasks 1-7 all implemented and wired), by extraction and execution:
>
> ```
> BEFORE: videos=11 disagreeing=9  seo_topic=0/45  dead_titles=28
> dry run: total=45 updated=45 changed=0  no_transcript=0 gate_rejected=0 failed=0
>         digest unchanged (correct -- dry run wrote nothing)
> real   : total=45 updated=45 changed=45 no_transcript=0 gate_rejected=0 failed=0
> AFTER : videos=11 disagreeing=0  seo_topic=45/45 dead_titles=0
> budget: Ran 24 tests ... OK   (was FAILED (failures=4))
> keys  : added {seo_topic: 45}  changed {platform_metadata, candidate_titles,
>                             suggested_hashtags, suggested_title: 45, niche: 25}
>         removed {}   MUST-NOT-CHANGE keys that moved: none
> ```
>
> Three things to read here. **The migration succeeds completely**: disagreement 9 -> 0,
> `seo_topic` on 45/45, dead titles 28 -> 0, and the quality budget goes from 4 failures to
> `OK`. **The dry run is genuinely inert** — `values changed: 0` and a byte-identical digest,
> so the `--dry-run` flag is load-bearing rather than decorative. And **`niche` changes on
> only 25 of 45**, not 45: the other 20 already held the value the video-level classifier
> computes, which is the `changed`-vs-`updated` distinction the pre-flight added, doing its
> job. No key that must not move (`start_time`, `end_time`, `transcript_snippet`,
> `filename`, `video_id`, `duration`, `rank`, `category`) moved on any file.

- [ ] **Step 6: Re-run the quality budget**

Run: `python -m unittest test_title_quality_budget -v`
Expected: PASS, all **24** tests, **0 skipped** (it is 24, not the 11 this section was
written against — Task 6 was hardened twice after this text was drafted). In particular
`TestStoredMetadata.test_no_stored_title_uses_the_dead_fallback`,
`test_no_stored_bare_spoken_quote_titles` and `test_every_stored_clip_has_a_seo_topic` were
all failing before this task, and `TestCorpusVsEngine.test_one_niche_per_video_is_exact`
must now report 0 mismatching videos. (It is in `TestCorpusVsEngine`, not
`TestGeneratedOutput`: it reads from disk and is one of the four correctly-red tests, so
filing it under the class whose docstring promises green made the report unreadable.)

Then confirm the migration really landed, rather than trusting the summary:

```powershell
python -c "import json,glob,collections; cs=[json.load(open(f,encoding='utf-8')) for f in glob.glob('output/*.json')]; p=collections.defaultdict(set); [p[c['video_id']].add(c.get('niche')) for c in cs if c.get('video_id')]; print('videos',len(p),'disagreeing',sum(1 for v in p.values() if len(v)>1)); print('with seo_topic',sum(1 for c in cs if 'seo_topic' in c),'/',len(cs))"
```

Expected: `videos 11 disagreeing 0` and `with seo_topic 45 / 45`.

- [ ] **Step 7: Commit**

```bash
git add .gitignore backfill_titles.py output/
git commit -m "feat: backfill regenerated titles, hashtags and niche for existing clips"
```

Expect 45 modified files under `output/`. Review the diff for one file before committing the
rest: `git diff --stat output/` should show only metadata keys changing, never
`start_time`, `end_time`, `transcript_snippet` or `filename`.

## Calibration Findings

The code in Tasks 1, 2, 4 and 5 was **extracted and executed against the real 45-clip corpus before this plan was finalised.** The first draft passed structural review and still failed 11 of 41 assertions. Nine real defects were found and fixed; they are recorded here so nobody reintroduces them.

| # | Defect in the first draft | Symptom | Fix |
|---|---|---|---|
| 1 | `clean_transcript` never lowercased, despite its docstring claiming case is stripped | `IT CAUGHT ME OFF GUARD` survived as a topic candidate | Added `_SHOUTING` to lowercase runs of ≥2 shouted words, preserving genuine proper nouns like `Zeke` |
| 2 | `BLOCKED_TOPIC_WORDS` omitted `hold` | `Hold` was mined as the top entity — the exact bug being fixed | Added `hold/holds/holding`, plus `caught`, `scared`, `guarding` |
| 3 | Blanket `-ing` rejection | Killed `throttling` and `streaming`, the best available topic for a large class of videos | Removed the rule; blocked verbs live in the word list instead |
| 4 | `validate_title` required the whole title to be `isascii()` while the templates append an emoji | **100% of generated titles were rejected.** `generate_seo_titles` returned `[]` for all 45 clips | Reject only non-ASCII **letters**; emoji pass |
| 5 | `validate_title` checked every word against `BLOCKED_TOPIC_WORDS` | Rejected `"The Sodium Trick Chefs Use To Cut Salt"` for containing `"the"` — same total-failure mode as #4 | Gate now covers fluency and structure only. Keyword quality is `_sanitize_phrase`'s job. Documented so the two concerns stay separate |
| 6 | `validate_title` stripped fillers then required ≥3 remaining words | A 2-word keyword produced a 6-word title, but a 2-word *fallback* title was rejected | Count words in the original title, then reject filler words individually |
| 7 | `normalize_for_tag` did not detect URLs | `"https://x.com/a"` became the tag `#https_x_com_a` | Added `_URLISH` pre-check |
| 8 | `hashtag_engine` appended 3 niche tags before any topic | Consumed the whole 5-tag budget, so every clip in a niche got identical tags | Reserve slots for topics: ≤2 niche tags, `#fyp` last, topics guaranteed |
| 9 | `normalize_for_tag` joins words with `_`, and `_` is not alphanumeric | `"elden ring"` produced the invalid hashtag `#elden_ring` | `hashtag_engine._clean_tag` concatenates instead. Documented why this is safe now when it was not in the old engine (see the docstring there) |

Two more were found by inspecting real output rather than test output:

- **Question words and negations were mined as keywords**, producing `"The Proof That not Is Real"`. Added `what/when/where/who/why/how/which/not/no/then/than` and the full preposition set to the blocklist. The first draft's blocklist rewrite had silently dropped the prepositions.
- **Rotation shifted only the keyword, not the pattern**, so every clip of a video got the same pattern with a different noun — which reads as auto-generated. Rotation now shifts both.

### Verified result on the real corpus

```
clips=45   invalid titles=0   dead-template titles=0   distinct top titles=43/45

> **Re-measured after Tasks 1-4 were actually implemented** (2026-09-28), because every
> figure in this section was originally measured against the plan's *draft* code rather than
> against shipped code. Two of them did not reproduce:
>
> | figure | as written | as implemented | verdict |
> |---|---|---|---|
> | `clips` | 45 | 45 | holds |
> | `invalid titles` | 0 | 0 | holds |
> | `dead-template titles` | 0 | 0 | holds |
> | `distinct top titles` | 44/45 | **43/45** | was off by one |
> | `niche disagreements` | 0/11 | 0/11 | holds |
> | `videos with a specific niche` | 5/11 | 5/11 | holds (after the earlier 8/11 correction) |
> | `videos still general_viral` | 3/11 | **6/11** | **was wrong, and contradicted the 5/11 above** — 5 specific + 3 general is 8, not 11 |
>
> The `3/11` figure was load-bearing: the "Known remaining imperfection" section below argued
> that 3 of 11 falling back to `general_viral` was an acceptable outcome for chat-shaped
> clips. At **6 of 11** it is 55% of the corpus, which is a materially different judgement and
> a materially weaker result. See the corrected section below for which six.
videos=11  niche disagreements=0 (was 9/11 with the old per-clip engine, 4/11 with the
                             Task 2 classifier left per clip)   videos with a specific niche=5/11

> **Correction: the `5/11` above was previously written as `8/11`, and `8/11` does not
> reproduce.** Measured over the real corpus with one context per video, exactly 5 of 11
> videos classify to something other than `general_viral`: `7APGcnUv2zQ` →
> `science_education`, `A6v5Vj6h_fQ` → `outdoors_survival`, `KrLj6nc516A` →
> `business_money`, `kJu5VMN3yow` → `gaming`, `v9QtM6qnG50` → `business_money`; the other
> six are `general_viral`. The committed `test_topic_engine.py` `TestCorpusFixture` pins
> the same 5/11, so two independent sources agree and this plan line was simply wrong.
>
> Treat that as a warning about the rest of "Calibration Findings" in this document:
> **re-measure before relying on any figure in it.** The three disagreement counts
> (9/11, 4/11, 0/11) all reproduce; this one did not, and it was the sole calibration
> evidence for the "niche" half of Tasks 4-8.
```

Sample before → after for video `A6v5Vj6h_fQ`:

```
OLD: Why Nobody Talks About Face Yesterday... 😳
     Why Nobody Talks About Best Ramen... 😳
     Why Nobody Tells You The Truth About Scary One... 😳
NEW: 10 Facts About snow That Change Everything 💡
     I Tried scary For 10 Days 💡
     The Real Reason food Happens 💡
     Surviving boys Changed How I See Everything 🎭
     tags: #Shorts #outdoors #boys #snow #tobogganrun
```

### Known remaining imperfection

Topic mining is frequency-and-blocklist based, not part-of-speech aware. Two residual weaknesses, accepted deliberately:

- A person's name used as a topic produces a weak noun slot (`"The Science Of zeke"`). A POS tagger or a name list would fix this; that is the dependency decision deferred to a future plan.
- **6 of 11 videos classify as `general_viral`** — not the 3 an earlier draft claimed, which
  contradicted its own `5/11 specific` figure in the same breath (5 + 3 ≠ 11). The six are
  `4mTLpuQpB80`, `JheRzFxeSBg`, `dV0OgeSbYPM`, `jm-sJUUani8`, `pySIRc4QjsY`, `qteIOgjfDIw`.

  **Measured mechanism** (`_NICHE_MIN_SCORE = 3.0`, gate is `if best_score < 3.0`):

  | video | tokens | best score | via | which gate |
  |---|---|---|---|---|
  | `pySIRc4QjsY` | 81 | 1.0 | motivation_mindset | min-score |
  | `JheRzFxeSBg` | 284 | 1.0 | fitness_health | min-score |
  | `dV0OgeSbYPM` | 311 | 2.0 | gaming | min-score |
  | `jm-sJUUani8` | 399 | 3.0 | gaming | **margin** (3.0 is not < 3.0) |
  | `qteIOgjfDIw` | 608 | 2.0 | gaming | min-score |
  | `4mTLpuQpB80` | 173 | 3.0 | business_money | **margin** |

  For contrast, the five that do classify: `kJu5VMN3yow` 1606 tokens → 4.0 gaming,
  `7APGcnUv2zQ` 472 → 7.0 science_education, `A6v5Vj6h_fQ` 442 → 17.0 outdoors_survival,
  `KrLj6nc516A` 721 → 8.0, `v9QtM6qnG50` 1690 → 8.0.

  **So the classifier is behaving as designed, and this is an accepted limitation, not a
  bug.** Four of the six are short videos (81-311 usable tokens) that simply do not contain
  enough domain vocabulary to clear a floor of 3.0; the other two clear the floor and lose
  the margin comparison. Declining to guess is the correct behaviour, and the fallback titles
  are valid rather than junk — which is the original claim, with the right number.

  **The real, actionable observation is length bias.** Score is a weighted *occurrence
  count*, so it scales with transcript length: `kJu5VMN3yow` reaches 4.0 on gaming from 1606
  tokens while `qteIOgjfDIw` reaches only 2.0 on the same niche from 608. A 2-minute gaming
  video therefore loses to a 10-minute one on identical vocabulary purely by duration. If
  short gaming videos need classifying, the fix is to **normalise the score by token count**
  (or use a distinct-term rate rather than an occurrence count) — **not** to lower
  `_NICHE_MIN_SCORE`, which is what two wrong diagnoses in this document's history would have
  led to. Lowering the floor is exactly the change that let "game" and "play" file a football
  commentary as `gaming`, which is why the strong-keyword gate exists at all.

  **Two wrong diagnoses are recorded here deliberately**, because both sounded confident and
  both would have made things worse if acted on:
  1. "`gg` is in `NICHE_STRONG_KEYWORDS` and fires as a substring inside *bigger*/*eggs*, so
     it is dragging Twitch videos into gaming." **False.** Matching is exact-token
     (`counts.get(kw, 0)`), and a standalone `gg` token occurs in **0 of 11** videos. The
     substring hit came from a probe that searched raw text.
  2. "The strong-keyword gate is too strict, so fix the gate's threshold rather than the
     keyword list." **False**, and dangerous: two of the six clear the gate fine and fail on
     margin; the other four never reach it. Loosening the floor or the gate re-opens the
     false-positive problem Task 2's gate was built to close.

Both are recorded in the quality test at the threshold they are accepted at, so a regression that makes them *worse* fails the build.

## Acceptance Criteria

The plan is done when all of the following are true and verified. Every one of these is a
command with an exact expected result, not a judgement call — a criterion you cannot fail is
not a criterion.

1. Full suite → **OK**
   `python -m unittest test_transcript_clean test_topic_engine test_video_context_parity test_title_seo test_hashtag_engine test_confirmed_bugs_fixes test_subtitles_taxonomy test_niche_integration test_title_and_uploader test_reported_bug_fixes test_title_quality_budget`
   (all ten regression modules **plus** the quality budget. This list was short four
   modules, so criterion 1 did not actually run the suite it claims to.)
   Expect `OK` and **0 skipped**. A skip in `test_title_quality_budget` means the corpus
   could not be found, which is a failure in disguise — see `TestTheBudgetCanActuallyFail`.
   (`test_jev_features` stays excluded throughout: live API, 502, pre-existing.)
2. No regression on the previous round → **OK**
   `python -m unittest test_confirmed_bugs_fixes test_subtitles_taxonomy test_niche_integration test_reported_bug_fixes`
3. `TestGeneratedOutput.test_the_title_that_actually_ships_is_within_youtubes_limit` → PASS, and `TestGeneratedOutput.test_the_chosen_title_is_one_of_the_validated_candidates` → PASS. (This criterion used to name `test_no_clip_produces_an_invalid_title`, which Task 6 removed because it could not fail: `generate_seo_titles` validates every candidate and the chosen title is one of the survivors. The shipped field, `youtube.title`, is what needed checking, and it is the chosen title plus a suffix nothing else looked at.)
4. `TestGeneratedOutput.test_no_generated_title_uses_the_dead_fallback` → PASS. Zero generated titles on `Why Nobody Tells You The Truth About …`.
5. `TestGeneratedOutput.test_generated_titles_are_not_all_identical` → PASS. Distinctness ratio ≥ 0.6.
6. `TestGeneratedOutput.test_generated_hashtags_are_short_and_carry_a_topic` → PASS. ≤ `MAX_HASHTAGS` tags, `#Shorts` present, no duplicates.
7. **`TestCorpusVsEngine.test_one_niche_per_video_is_exact` → PASS. Exactly 0 videos with
   mismatching niches.** This replaces the old `test_one_niche_per_video`, which permitted
   half the videos to disagree and measured the pre-Task-3 per-clip path.
8. `TestStoredMetadata` → PASS, all five. Zero dead-template titles (baseline 28/45), zero
   bare-quote titles (baseline **1**/45, not the 8 an earlier draft recorded -- re-measured,
   exactly one stored title begins with a quote mark), every title within 100 chars,
   stored distinctness
   ratio ≥ 0.6, and `seo_topic` present on **45/45** files.
9. `python backfill_titles.py` → `total 45`, `updated 45`, `failed 0`, `gate rejected 0`,
   `no context 0`, `no transcript 0`, `values changed 45`, `would change 45` on the first
   run. (Label names are the script's, not dict keys -- an earlier draft quoted a dict it
   never prints.) `failed` > 0 or `no context` > 0 is a **STOP**, and the script now exits
   non-zero for either, so a partial run cannot be mistaken for a clean one.
   **`gate rejected` is deliberately NOT a stop criterion.** It cannot fire: the script
   re-validates a title that `generate_seo_titles` already validated before returning it
   (title_seo.py:245 and :299), and the shipped title is always one of the candidates, so
   `validate_title` is a no-op on this path -- measured 0/45. It is kept as a cheap
   assertion, but a criterion that cannot fail is not a criterion, and this plan has
   already deleted one test for exactly that reason.
10. Post-backfill corpus check → `videos 11 disagreeing 0` and `with seo_topic 45 / 45`.
    Criterion 9 alone is not enough: it is a self-report from the script that did the work.
11. The UI shows full titles with the `SEO: <keyword>` line, no visual truncation, and
    **exactly one** amber warning strip above the grid when a run under-delivers.
12. Mutation spot-check: reverting the Task 3 wiring in `app.py`, `clipper.py` **or**
    `batch_rerender.py` must turn the suite red. All three entry points write the same
    schema; a check that only covers one of them has already fooled this plan twice.

> **A second, separate defect that the "no POS tagger" limitation does NOT cover -- and it
> has since been fixed.** This note originally covered only `before_after`. Auditing the whole
> 29-pattern bank found the class was five patterns deep, not one.
>
> The rule: the mined keyword is a bare word of unknown part of speech, so a template is safe
> only if a bare SINGULAR NOUN is grammatical in its slot. Five patterns broke that:
>
> | pattern | was | why it cannot take a bare word | shipped on |
> |---|---|---|---|
> | `before_after` | `{kw} Before And After You Know It` | needs a verb or a noun phrase | 9 |
> | `i_tried` | `I Tried {kw} For {n} Days` | needs a verb | 4 |
> | `why_it_happens` | `The Real Reason {kw} Happens` | missing a `that` clause | 3 |
> | `survival_story` | `Surviving {kw} Changed How I See Everything` | needs a gerund | 1 |
> | `setup_punch` | `When {kw} Goes Exactly As Planned` | needs a verb | 0 |
>
> and two more were grammatical only for a noun carrying an article:
>
> | pattern | was | problem | shipped on |
> |---|---|---|---|
> | `how_it_works` | `How {kw} Works In Practice` | `how` needs a clause | 3 |
> | `watch_this` | `Watch This Before You Judge {kw}` | `before` needs a clause | 3 |
>
> **23 of the 45 titles shipped on one of those seven.** All seven were rewritten to noun slots,
> keeping their pattern **ids** -- `_NICHE_PATTERNS` maps niches to ids and every stored clip
> carries a `pattern_id`, so renaming `before_after` would have silently repointed the niche
> mapping and orphaned 9 clips:
>
> | pattern | is now |
> |---|---|
> | `before_after` | `What Changed My Mind About {kw}` |
> | `i_tried` | `Everything I Learned About {kw} In {n} Days` |
> | `why_it_happens` | `Why {kw} Is Not What You Think` |
> | `survival_story` | `The Story Behind {kw}` |
> | `setup_punch` | `The Wildest Part About {kw}` |
> | `how_it_works` | `The Mechanics Of {kw} Explained Simply` |
> | `watch_this` | `Watch This: The Truth About {kw}` |
>
> The first candidate for `setup_punch` was `The Worst Thing About {kw}`, and `validate_title`
> rejected it on all 16 real keywords for the filler word `thing` -- which is the point of
> having a gate, and is why the replacements were chosen by rendering them against the real
> corpus rather than by judgement.
>
> **Result: all 45 titles are now grammatical English.** `I Tried motion For 5 Days` became
> `Everything I Learned About motion In 5 Days`; `bobby Before And After You Know It` became
> `What Changed My Mind About bobby`; `How boss Works In Practice` became `The Mechanics Of
> boss Explained Simply`. The re-backfill rewrote 23 chosen titles and touched all 45 files,
> because `candidate_titles` and `platform_metadata` embed every candidate -- a clip whose
> chosen title did not move still lists different candidates.
>
> **What did not change, and is the remaining limitation:** the keywords themselves. `bobby`,
> `goin`, `much`, `built`, `donated` and `god` are still bad topics. That is the mining, and it
> is the part that genuinely needs a POS tagger or a name list, out of scope here. The fix
> above was about the sentence, not the word in it, and the two are separable: every title is
> now a grammatical sentence regardless of how good its topic turns out to be.
>
> **The guard against this returning** is `TestNounSlotDiscipline` in `test_title_seo.py`: a
> declared `NOUN_SAFE_PATTERNS` map, so adding a pattern forces a decision; the seven
> known-broken frames as an explicit denylist; a check that each declared preposition is still
> the one sitting immediately before `{kw}`; and a render check over adversarial keywords.
> Nine valid mutants, nine killed.
>
> `validate_title` could not have caught any of this: **all 23 bad titles passed it.** It checks
> fillers, blocked words, length and ASCII -- not whether the sentence is English. Do not add a
> grammar expectation to it on the assumption the guard above is then redundant.

## Out of Scope

Explicitly **not** in this plan, so nobody assumes they were forgotten:

- **Real trend/trend-volume data.** Hashtags are derived from the video's own mined topic and the niche list. No live trending-hashtag API is called. Adding one is a separate plan; the `NICHE_TAGS` table in `topic_engine.py` is the single place to update.
- **A/B testing titles against real impressions.** The engine generates 5 candidates and Jev ranks them on hook strength. Wiring actual CTR feedback is a separate plan.
- **Keyword volume research.** No volume data is fetched; titles are optimised for *searchability* (concrete noun phrase, front-loaded) rather than for measured volume.
- **Rewriting the niche taxonomy.** The 8 existing niches are kept for continuity. If you want different buckets, change `NICHE_KEYWORDS` and `NICHE_TAGS` in `topic_engine.py` — both are single-source-of-truth.
- **Multi-language titles.** Titles are ASCII-only by the gate. Localisation is a separate plan.
- **The Jev API integration for titles.** `score_and_rank_titles_with_jev` is left as-is; it ranks whichever 5 candidates the new engine produces. Its 8-second timeout and heuristic fallback already work.
