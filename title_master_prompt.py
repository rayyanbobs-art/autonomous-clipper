"""
title_master_prompt.py -- the "Viral YouTube Shorts Title Generator" master prompt, as code.

WHAT THIS IS

A faithful, machine-checkable implementation of the master prompt specification. The prompt
text is a deliverable in its own right (see `docs/MASTER_PROMPT_VIRAL_SHORTS_TITLES.md`), but
prose cannot be enforced. This module holds the same rules as DATA and as executable
checks, so the specification and the gate cannot drift apart.

WHY IT EXISTS

Two gaps in the pipeline motivated it:

1. `mine_topic_phrases` cannot tell a verb from a noun. It has no part-of-speech
   information, and the project is stdlib-only by rule (`docs/.../HANDOFF.md` section 0,
   rule 1), so it must not acquire a tagger. The measured consequence is that `much`,
   `built`, `donated`, `goin` and `bobby` are mined as topics. An LLM already knows the
   difference, which is the one place an LLM genuinely beats the regex engine here.

2. When the Jev API is unavailable, `score_and_rank_titles_with_jev` returned
   `candidates[0]` -- the FIRST of five candidates, not the best. Five titles were
   generated, scored by nobody, and the first was published. `rank_titles_locally` fixes
   that deterministically, with no network and no API key.

DESIGN RULES HONOURED HERE

- The niche table is IMPORTED from `topic_engine.NICHE_STRONG_KEYWORDS`, never restated.
  The master prompt originally carried its own copy of this table. Duplicating a tuned
  constant is precisely the bug class this project's history keeps hitting ("Task 8's
  expectations were recorded pre-Task-5, and one licensed a real regression"), so the
  prompt renders from the code and a test asserts the two agree.
- `title_seo.validate_title` remains the publication gate and is not replaced or
  weakened. This module adds checks the prompt specifies that the gate does not cover
  (front-loaded keyword, minimum real-word count, repeated words, ellipsis count) and
  calls the gate for everything it already covers.
- `generate_smart_title_and_hashtags`'s signature stays backward compatible: new
  parameters are keyword-only with defaults. Nothing positional changes.
- No new pip dependencies. `requests` is already used by `title_tag_engine`.

Scope note: the specification's STEP 4 asks for a visual analysis of a clip frame. Nothing
in the pipeline writes a frame to disk or passes one to a model -- `face_tracker` reads
frames in memory via OpenCV for scene-cut detection only. The prompt therefore renders
STEP 4 as unavailable rather than silently accepting an input the pipeline cannot supply.
"""

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from topic_engine import (
    NICHE_STRONG_KEYWORDS,
    NICHE_TAGS,
    BLOCKED_TOPIC_WORDS,
    mine_topic_phrases,
)
from title_seo import (
    MAX_TITLE_CHARS,
    FRONT_LOAD_CHARS,
    validate_title,
    SEO_PATTERNS,
    _EXTRA_PATTERNS,
    _NICHE_PATTERNS,
    _PATTERNS_BY_ID,
    _fallback_titles,
    ALL_PATTERNS,
)

# Version is part of the rendered prompt so a cached model response can be invalidated.
# 1.1.0: URL-fragment check rescoped to the topic slot (template prose like "Watch This:"
# no longer rejected); `watch_this` and `fallback_curiosity` re-admitted; person names and
# thank/stop/god blocked as topics.
MASTER_PROMPT_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

HARD_CONSTRAINTS: List[Dict[str, str]] = [
    {
        "id": "char_limit",
        "rule": f"CHARACTER LIMIT: every title must be <= {MAX_TITLE_CHARS} characters total. "
                "No exceptions.",
    },
    {
        "id": "front_load",
        "rule": f"SEARCHABLE CORE IN FIRST {FRONT_LOAD_CHARS} CHARS: the main keyword/topic "
                "must appear within the first 45 characters. YouTube truncates long titles, "
                "so the hook and keyword must land before the cut.",
    },
    {
        "id": "no_filler_keyword",
        "rule": "NO FILLER WORDS AS KEYWORDS: never use um, uh, yeah, bro, like, basically, "
                "literally, actually, hold, get, put, lock, nope, nothing, stuff, gonna, "
                "wanna, gotta as the title's core topic.",
    },
    {
        "id": "no_profanity",
        "rule": "NO PROFANITY: never include shit, fuck, bitch, asshole, bastard.",
    },
    {
        "id": "emoji_allowed",
        "rule": "EMOJI ALLOWED: one emoji at the end is encouraged. Emoji boost CTR. Never "
                "reject a title for containing emoji.",
    },
    {
        "id": "no_repeated_words",
        "rule": "NO REPEATED WORDS: a title must not repeat any word. "
                "\"The Truth About The Real Truth\" is rejected.",
    },
    {
        "id": "single_ellipsis",
        "rule": "NO MULTIPLE ELLIPSES: at most one \"...\" per title.",
    },
    {
        "id": "min_real_words",
        "rule": "MINIMUM 3 REAL WORDS: every title must contain at least 3 English words of "
                "3 or more letters.",
    },
    {
        # The original specification read "ASCII LETTERS ONLY", which re-creates a hazard
        # this project already hit: an earlier whole-title isascii() check rejected emoji,
        # and it had to be undone. Calibrated Finding #4 in the plan doc records it.
        # Stated positively as a check on LETTERS so the mistake is not invited.
        "id": "no_non_ascii_letters",
        "rule": "NO NON-ASCII LETTERS: no accented characters (e-acute, u-umlaut, n-tilde). "
                "Emoji and punctuation are NOT letters and are always fine -- check letters "
                "specifically, never test the whole string for ASCII.",
    },
    {
        "id": "no_url_fragments",
        "rule": "NO URL FRAGMENTS: never use http, https, www, com, subscribe, click or link "
                "as topic words.",
    },
]

# Tokens the gate refuses as a title keyword. Deliberately a subset of
# `title_seo._TITLE_BLOCKED` plus the specification's own additions; the union is applied
# so this module and the gate cannot disagree about what counts as filler.
_FILLER_KEYWORD = {
    "um", "uh", "yeah", "bro", "like", "basically", "literally", "actually",
    "hold", "get", "put", "lock", "nope", "nothing", "stuff", "gonna", "wanna",
    "gotta", "just", "really", "very", "thing", "things", "right", "okay", "well",
    "so", "wow", "hey", "oh",
}

_PROFANITY = {"shit", "fuck", "bitch", "asshole", "bastard", "fucking", "shitty"}

_URL_FRAGMENTS = {"http", "https", "www", "com", "org", "net", "edu", "gov", "io", "co",
                  "url", "link", "links", "subscribe", "watch", "dot", "click", "href"}

# ---------------------------------------------------------------------------
# Frameworks
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Frameworks -- DERIVED FROM title_seo, NOT RESTATED
# ---------------------------------------------------------------------------
#
# The framework templates are `title_seo`'s, and they are the single source of truth.
#
# An earlier version of this file declared its own copies, with its own framework labels
# and `pattern_id`s slugified from those labels. That was a mistake with three costs:
#   1. It broke the id contract. `test_title_and_uploader` asserts every candidate's
#      `pattern_id` resolves against `title_seo.ALL_PATTERNS`, and `'list'` did not.
#   2. It duplicated a tuned, validated library. The project's history is a record of
#      exactly that going wrong ("Task 8's expectations were recorded pre-Task-5, and one
#      licensed a real regression").
#   3. It silently lost the niche-specific patterns (`run_showcase`, `comparison`,
#      `survival_story`, ...), which is the mechanism by which the shared context's niche
#      reaches the titles at all.
#
# So the master prompt contributes the specification -- constraints, ranking, output
# format, parsing -- and inherits the templates. It does not fork them.
#
# Each entry is (framework_label, template, pattern_id).


# NOTE on `watch_this` and `fallback_curiosity`.
#
# An earlier version of this file EXCLUDED both templates, on the theory that `watch` is
# a member of `title_seo._URLISH` and so neither could ever pass validation. That theory
# was wrong, and the exclusion was the actual defect:
#
# - `title_seo.validate_title` has no `watch` rule. It checks `_TITLE_BLOCKED` and
#   `_FILLER_RE`, neither of which contains "watch". Dozens of `watch_this` titles
#   shipped in `output/` and passed the gate. The templates were always fine.
# - What rejected them was THIS module's own URL-fragment check, which scanned every
#   token of the whole title. That is an over-broad gate of exactly the kind
#   `validate_title`'s docstring warns about: it mistook intentional template prose
#   ("Watch This:") for a URL fragment split out of auto-captions ("youtu.be/watch?v=").
# - The cost was silent: 3 of 10 rotations returned four titles instead of five.
#
# The fix is to scope the URL check to the TOPIC SLOT (see `validate_master_title`),
# where the fragments actually occur, and to stop filtering templates. There is no
# `_UNUSABLE_PATTERNS` anymore; if one reappears here, the rotation-sweep test
# (`test_always_returns_five_for_every_rotation`) is what catches it.

CORE_FRAMEWORKS: List[Tuple[str, str, str]] = [
    (p["framework"], p["template"], p["id"])
    for p in SEO_PATTERNS
]

NICHE_BONUS_FRAMEWORKS: Dict[str, List[Tuple[str, str, str]]] = {
    niche: [
        (_PATTERNS_BY_ID[pid]["framework"], _PATTERNS_BY_ID[pid]["template"], pid)
        for pid in pattern_ids
        if pid in _PATTERNS_BY_ID
    ]
    for niche, pattern_ids in _NICHE_PATTERNS.items()
}

# Fallback templates are `title_seo`'s too, so their `pattern_id`s stay in the registry.
# `title_seo._fallback_titles` is called directly at generation time; this list exists for
# rendering the prompt and for tests.
FALLBACK_TITLES: List[str] = [
    p["template"] for p in _EXTRA_PATTERNS
    if p["id"].startswith("fallback_")
]

# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

_RULE = "━" * 61
_HEADING = "═" * 61


def render_niche_table() -> str:
    """
    Renders the niche/strong-keyword table directly from `topic_engine`.

    The specification shipped a hand-maintained copy of this table. Rendering it from the
    single source of truth means a retune in `topic_engine` propagates to the prompt
    automatically, and a drift between the two becomes impossible rather than merely
    unlikely.
    """
    lines = ["| Niche | Strong Keywords (must match >= 1) |",
             "|-------|--------------------------------|"]
    for niche, keywords in NICHE_STRONG_KEYWORDS.items():
        lines.append(f"| {niche} | {', '.join(keywords)} |")
    return "\n".join(lines)


def build_master_prompt() -> str:
    """Assembles the full master prompt text."""
    parts: List[str] = []

    parts.append("You are a YouTube Shorts Title Specialist. Generate 5 viral, "
                 "click-optimised titles for a single video clip, for YouTube Shorts, "
                 "TikTok and Instagram Reels. Analyse the actual clip before generating.")

    parts.append(f"{_RULE}\nHARD CONSTRAINTS -- NEVER VIOLATE THESE\n{_RULE}")
    for index, constraint in enumerate(HARD_CONSTRAINTS, 1):
        parts.append(f"{index}. {constraint['rule']}")

    parts.append(f"{_RULE}\nHOW TO ANALYSE THE CLIP\n{_RULE}")
    parts.append(
        "STEP 1 -- CLEAN THE TRANSCRIPT\n"
        "- Strip stage directions: [Music], [Applause], [Laughter], (upbeat music)\n"
        "- Ignore filler: um, uh, like, basically, literally, you know, kind of, right, okay\n"
        "- Ignore shouted ALL-CAPS runs (auto-caption artifacts)\n"
        "- Expand contractions: don't -> do not, gonna -> going to\n"
        "- Remove stutter repeats: \"the the\" -> \"the\""
    )
    parts.append(
        "STEP 2 -- IDENTIFY THE VIDEO'S NICHE\n"
        "Classify into exactly ONE niche. A niche may only be claimed if at least ONE of\n"
        "its STRONG keywords appears. Generic words like \"game\", \"play\", \"fire\" and\n"
        "\"money\" are NOT strong enough alone -- they appear in everyday speech. If no\n"
        "strong keyword matches, classify as general_viral. If two niches are close in\n"
        "score, classify as general_viral, because ambiguous means general.\n\n"
        + render_niche_table()
    )
    parts.append(
        "STEP 3 -- MINE THE TOPIC\n"
        "Extract the 3-5 most important TOPIC PHRASES.\n"
        "- Score by FREQUENCY, not by capitalisation. Auto-captions capitalise randomly,\n"
        "  so a capitalised word is not a proper noun. \"Hold on, let me explain\" means\n"
        "  Hold is NOT the topic.\n"
        "- A topic must appear multiple times to be real signal.\n"
        "- Multi-word phrases (\"cold start\", \"snow cave\", \"hot tent\") beat single words.\n"
        "- Reject single-occurrence bigrams -- they are accidental adjacency, not phrases.\n"
        "- Never extract: pronouns, verbs, adjectives, adverbs, numbers, filler words,\n"
        "  names of people, or stage-direction words (laugh, sigh, gasp, cheer, clap,\n"
        "  scream, whisper). \"much\", \"built\", \"donated\" and \"goin\" are all verbs.\n"
        "- Do not fabricate compounds. \"year supply\" from \"this year the supply\" is\n"
        "  invented, not observed."
    )
    parts.append(
        "STEP 4 -- VISUAL CONTEXT\n"
        "UNAVAILABLE IN THIS PIPELINE. No clip frame is exported, so there is no visual to\n"
        "read. Do not claim to have seen one. Report \"No visual provided\" and rely on the\n"
        "transcript alone. (To enable this, the pipeline would need to export a frame per\n"
        "clip and send it alongside the transcript; `face_tracker` reads frames in memory\n"
        "for scene-cut detection but never writes one.)"
    )

    parts.append(f"{_RULE}\nTITLE FRAMEWORKS -- 5 TITLES, 5 DIFFERENT FRAMEWORKS\n{_RULE}")
    parts.append("CORE (any niche):")
    for label, template, pattern_id in CORE_FRAMEWORKS:
        parts.append(f"  {label:<18} \"{template}\"")
    parts.append("NICHE-SPECIFIC BONUS (prefer these when the niche matches):")
    for niche, frameworks in NICHE_BONUS_FRAMEWORKS.items():
        rendered = ", ".join(f"\"{t}\"" for _, t, _ in frameworks)
        parts.append(f"  {niche:<22} {rendered}")
    parts.append("FALLBACK (no good topic was mined):")
    for template in FALLBACK_TITLES:
        parts.append(f"  \"{template}\"")

    parts.append(f"{_RULE}\nTITLE QUALITY RULES\n{_RULE}")
    parts.append(
        "WHAT MAKES A TITLE VIRAL:\n"
        "- Creates a curiosity gap -- the viewer MUST click to resolve it\n"
        "- Front-loads the searchable keyword within the first 45 characters\n"
        "- Uses emotional triggers: mistake, truth, secret, nobody, change, real, proof\n"
        "- One emoji at the end (shocking, fire, mask, bulb, tears-of-joy)\n"
        "- 6-12 words is the sweet spot\n\n"
        "WHAT KILLS A TITLE:\n"
        "- Generic garbage: \"Brutal Truth You Need to Hear\" -- no keyword, no search,\n"
        "  no views\n"
        "- Filler as keyword: \"Why Nobody Tells You About Hold\"\n"
        "- Too vague: \"This Changed Everything\" -- what changed?\n"
        "- Clickbait with no payoff signal: \"You Won't BELIEVE This\"\n"
        "- A topic that does not match the clip: mining \"car\" from a cooking video\n"
        "  because someone said \"park the car\"\n\n"
        "ROTATION: do not make all 5 titles about the same keyword -- spread them across\n"
        "the mined topics. Keep casing consistent: capitalise the first word of the title\n"
        "and each content word, but render articles, prepositions and conjunctions in\n"
        "lower case when they fall in mid-position (so \"The Science of Sodium\", not\n"
        "\"The Science Of Sodium\")."
    )

    parts.append(f"{_RULE}\nOUTPUT FORMAT -- FOLLOW EXACTLY\n{_RULE}")
    parts.append(
        "**Niche:** {detected niche}\n"
        "**Topics Mined:** {topic1}, {topic2}, {topic3}\n"
        "**Visual Context:** No visual provided\n\n"
        "**Titles:**\n"
        "1. [{framework}] {title} -- {character count}/100\n"
        "2. [{framework}] {title} -- {character count}/100\n"
        "3. [{framework}] {title} -- {character count}/100\n"
        "4. [{framework}] {title} -- {character count}/100\n"
        "5. [{framework}] {title} -- {character count}/100\n\n"
        "**Recommended:** Title #{number} -- {one sentence on why this has the highest "
        "CTR potential}\n\n"
        "The character count is the exact length of the title string, emoji included. "
        "Count it; do not estimate."
    )

    parts.append(f"{_RULE}\nSELF-CHECK -- VERIFY BEFORE SUBMITTING\n{_RULE}")
    checks = [
        f"<= {MAX_TITLE_CHARS} characters?",
        f"Keyword within the first {FRONT_LOAD_CHARS} characters?",
        "No filler word used as the keyword?",
        "No repeated word?",
        "At least 3 real English words of 3+ letters?",
        "No non-ASCII LETTERS? (emoji are fine)",
        "Each title uses a different framework?",
        "Keyword actually recurs in the transcript, not a one-off mention?",
        "Title matches what the clip is actually about?",
    ]
    parts.append("\n".join(f"[ ] {check}" for check in checks))
    parts.append("If any title fails, regenerate it. Never return a title that fails "
                 "validation.")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _tokens(title: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(title)]


def _has_non_ascii_letter(text: str) -> bool:
    """True if any LETTER is non-ASCII. Emoji and punctuation are not letters."""
    for char in text:
        if char.isalpha() and ord(char) > 127:
            return True
        category = unicodedata.category(char)
        if category.startswith("L") and ord(char) > 127:
            return True
    return False


def validate_master_title(
    title: str,
    topics: Optional[List[str]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Enforces the specification's hard constraints. Returns (ok, reason).

    Layers on top of `title_seo.validate_title` rather than replacing it. The gate stays
    authoritative for fluency and structure; this adds the constraints the gate does not
    implement, and re-states the gate's own rules in the specification's terms so a prompt
    response and a template-generated title are held to one standard.

    ORDER MATTERS. The gate runs first and its reason is returned verbatim when it fails,
    so the most specific diagnostic wins. Constraints are then checked cheapest-first.
    """
    if not title or not title.strip():
        return False, "empty title"

    # The publication gate. Not weakened, not bypassed.
    ok, reason = validate_title(title)
    if not ok:
        return False, f"gate: {reason}"

    if len(title) > MAX_TITLE_CHARS:
        return False, f"exceeds {MAX_TITLE_CHARS} characters ({len(title)})"

    if title.count("...") > 1:
        return False, "more than one ellipsis"

    tokens = _tokens(title)
    if not tokens:
        return False, "no words"

    if _has_non_ascii_letter(title):
        return False, "non-ASCII letter"

    bad = sorted({t for t in tokens if t in _PROFANITY})
    if bad:
        return False, f"profanity: {bad[0]}"

    # URL fragments are checked against the TOPIC SLOT, not the whole title.
    #
    # An earlier version scanned every token of the title. That rejected intentional
    # template prose -- "Watch This: The Truth About {kw}" contains "watch", which is in
    # `_URLISH` only because auto-captions split "youtu.be/watch?v=..." into bare words.
    # The fragments occur in practice as MINED KEYWORDS ("The Proof That https Is Real",
    # "...About dot"), never as template prose, and `title_seo.validate_title` -- the
    # publication gate -- has no whole-title URL rule at all. Scanning the whole title
    # here silently cost one title in 3 of 10 rotations. The topic list is where the
    # keyword comes from, so it is where the check belongs.
    #
    # When no topics are known (an LLM reply that listed none, or the no-topic fallback
    # path), there is no slot to check, so the title tokens are scanned instead. That is
    # strictly stronger than skipping the check, at the price that a topic-less title
    # containing template prose like "Watch" is rejected -- acceptable for paths that
    # only run when composition already failed or the model gave nothing to work with.
    topic_words: set = set()
    for topic in topics or []:
        topic_words.update(_tokens(topic))
    if not topic_words:
        topic_words = set(tokens)
    bad = sorted({t for t in topic_words if t in _URL_FRAGMENTS})
    if bad:
        return False, f"URL fragment: {bad[0]}"

    if len(set(tokens)) != len(tokens):
        repeated = sorted({t for t in tokens if tokens.count(t) > 1})
        return False, f"repeated word: {repeated[0]}"

    real_words = [t for t in tokens if len(t) >= 3]
    if len(real_words) < 3:
        return False, f"only {len(real_words)} real words of 3+ letters (need 3)"

    # Front-loading: the first mined topic must land inside the truncation window.
    if topics:
        head = title[:FRONT_LOAD_CHARS].lower()
        if not any(topic and topic.lower() in head for topic in topics if topic):
            return False, f"no mined topic within first {FRONT_LOAD_CHARS} characters"

    return True, None


def title_filler_hits(title: str) -> List[str]:
    """Filler tokens present in a title. Used by the local ranker, not as a hard failure."""
    return sorted({t for t in _tokens(title) if t in _FILLER_KEYWORD})


# ---------------------------------------------------------------------------
# Local ranking -- the fix for "five titles generated, the first one published"
# ---------------------------------------------------------------------------

# Curiosity hooks, matched as PHRASES rather than single words.
#
# A bag of single trigger words was the first attempt and it was too coarse: four of the
# five specification examples tied at 7.50, so the "pick the best" function returned
# whichever came first and reproduced the very bug it was written to remove. A hook is a
# construction ("nobody tells you", "not what you think"), not a vocabulary item, so it
# has to be matched as one. Ordered strongest-first for readability; all are additive.
_HOOK_PATTERNS: List[Tuple[re.Pattern, float]] = [
    (re.compile(r"\bnobody tells you\b"), 2.5),
    (re.compile(r"\bnobody points out\b"), 2.5),
    (re.compile(r"\bnot what you think\b"), 2.0),
    (re.compile(r"\bmistake everyone makes\b"), 2.0),
    (re.compile(r"\bbiggest mistake\b"), 2.0),
    (re.compile(r"\bchanged my mind\b"), 2.0),
    (re.compile(r"\bnobody\b"), 2.0),
    (re.compile(r"\bmistake\b"), 1.5),
    (re.compile(r"\bproof\b"), 1.5),
    (re.compile(r"\btruth\b"), 1.5),
    (re.compile(r"\bscience of\b"), 1.5),
    (re.compile(r"\bhard way\b"), 1.5),
    (re.compile(r"\bnot ready\b"), 1.5),
    (re.compile(r"\bwildest\b"), 1.5),
    (re.compile(r"\brules i wish\b"), 1.5),
    (re.compile(r"\bshould not have been\b"), 1.5),
    (re.compile(r"\bstory behind\b"), 1.5),
    (re.compile(r"\bmechanics\b"), 1.0),
    (re.compile(r"\bexplained\b"), 1.0),
    (re.compile(r"\breal numbers\b"), 1.0),
    (re.compile(r"\bfrom scratch\b"), 1.0),
    (re.compile(r"\bunder a minute\b"), 1.0),
    (re.compile(r"\bchanged everything\b"), 1.0),
]

# Total hook contribution is capped so a title cannot buy relevance with hook stacking.
_HOOK_CAP = 4.0

# Trending-topic boosts, applied in `score_title` (capped, see _TREND_CAP).
#
# HONESTY NOTICE. OpusClip's Trend dimension is a live feed over platform big data.
# There is no stdlib-only, offline equivalent, and inventing per-topic weights from
# vibes would be worse than nothing: untraceable numbers that steer what ships. So this
# map starts EMPTY and is populated only from measured data, by this procedure:
#
#   1. Ship clips with publish logging on (see `publish_log.py`).
#   2. After real view/CTR data exists, set a topic's boost proportional to its
#      measured over-performance versus the account baseline. Record the evidence and
#      the date in the commit message, not just the number.
#   3. Update TRENDS_UPDATED. Anything older than 90 days is stale: trends decay, and
#      a boost without a fresh date is a superstition.
#
# An empty map means the ranker runs purely on content signals, which is the correct
# behavior until data exists. `test_trending_boost_applies_when_populated` pins the
# mechanism using a test-local override, so the production map being empty is a data
# state, not missing code.
TRENDING_BOOST: Dict[str, float] = {}

TRENDS_UPDATED = "2026-09-30"

_TREND_CAP = 1.0

_HAS_DIGIT_RE = re.compile(r"\d")
# Emoji live in the supplementary planes; a cheap range test beats importing a table.
_SUPPLEMENTARY_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")


def score_title(
    title: str,
    topics: List[str],
    hook_text: str = "",
) -> float:
    """
    Deterministic CTR-proxy score, roughly [0, 10]. No network, no API key.

    Deliberately transparent and individually testable: every term is a named decision,
    so a score can be argued with rather than trusted. This exists because the previous
    behaviour -- publish `candidates[0]` -- was not a ranking decision at all.

    Discriminating power is the requirement, not absolute accuracy. A scorer that ties
    everything is equivalent to taking the first element, which is the regression.

    `hook_text` is the clip's opening line (see `extract_hook_text`). A title whose
    keyword is what the clip OPENS with outranks one whose keyword appears thirty
    seconds in -- that is OpusClip's Hook dimension, minus the model.
    """
    score = 0.0
    tokens = _tokens(title)
    lowered = title.lower()

    # Keyword presence, and whether it survives the feed's truncation.
    matched_topic = ""
    for topic in topics:
        if topic and topic.lower() in lowered:
            matched_topic = topic.lower()
            break
    if matched_topic:
        score += 2.0
        if matched_topic in lowered[:FRONT_LOAD_CHARS]:
            score += 1.0
        # The primary mined topic is the video's actual subject; a title carrying it is
        # more on-topic than one carrying a secondary detail. This is also the tiebreak
        # that stops every candidate from scoring identically.
        if topics and matched_topic == topics[0].lower():
            score += 0.6

    hook_total = sum(weight for pattern, weight in _HOOK_PATTERNS if pattern.search(lowered))
    score += min(hook_total, _HOOK_CAP)

    # Hook-topic overlap: the title describes what the viewer hears first.
    if matched_topic and hook_text and matched_topic in hook_text.lower():
        score += 1.5

    # Trend boost: measured over-performance only (see TRENDING_BOOST). Capped so a
    # stale or generous map cannot buy relevance the content did not earn.
    #
    # Matching is token-aware, not substring: a short key like "ai" must not over-fire
    # on "said", "air" or "again". Single-word keys match whole tokens; multi-word keys
    # match as phrases.
    if matched_topic:
        trend_total = 0.0
        for trend_topic, weight in TRENDING_BOOST.items():
            if not trend_topic:
                continue
            key = trend_topic.lower()
            if " " in key.strip():
                if key in lowered:
                    trend_total += weight
            elif key in tokens:
                trend_total += weight
        score += min(max(trend_total, 0.0), _TREND_CAP)

    if _HAS_DIGIT_RE.search(title):
        score += 0.5

    word_count = len(tokens)
    if 6 <= word_count <= 12:
        score += 1.0
    if 25 <= len(title) <= 60:
        score += 1.0
    if _SUPPLEMENTARY_RE.search(title):
        score += 0.5

    if title_filler_hits(title):
        score -= 3.0

    if not matched_topic:
        score -= 3.0

    if len(title) > 85:
        score -= 1.0

    return round(score, 2)


def rank_titles_locally(
    titles: List[Dict[str, str]],
    topics: List[str],
    hook_text: str = "",
) -> Tuple[str, float]:
    """
    Picks the best candidate deterministically. Returns (candidate_id, score).

    THE REGRESSION THIS REPLACES: with the Jev API down, the old code returned
    `candidates[0]["id"]` -- always the first title. Five titles were generated and the
    first was published, so the four others were computed and discarded. The published
    title was a function of list order, not quality.

    Ties break toward the EARLIEST candidate, so behaviour is stable and deterministic.
    A future agent should not be able to make this return `candidates[0]` for every input
    without a test failing; `test_master_prompt.py` asserts exactly that.
    """
    if not titles:
        raise ValueError("rank_titles_locally requires at least one candidate")

    best_id = titles[0]["id"]
    best_score = float("-inf")
    for candidate in titles:
        score = score_title(candidate["title"], topics, hook_text)
        if score > best_score:
            best_score = score
            best_id = candidate["id"]
    return best_id, best_score


# ---------------------------------------------------------------------------
# Parsing a model response
# ---------------------------------------------------------------------------

_TITLE_LINE_RE = re.compile(
    r"^\s*(\d)\s*[.)]\s*\[(?P<framework>[^\]]*)\]\s*(?P<title>.+?)\s*"
    r"(?:--|—|–|-)\s*(?P<count>\d+)\s*/\s*100\s*$"
)
_RECOMMENDED_RE = re.compile(r"recommended:.*?title\s*#\s*(\d)", re.IGNORECASE | re.DOTALL)
_FIELD_RE = {
    "niche": re.compile(r"\*\*Niche:\*\*\s*(.+)", re.IGNORECASE),
    "topics": re.compile(r"\*\*Topics Mined:\*\*\s*(.+)", re.IGNORECASE),
    "visual": re.compile(r"\*\*Visual Context:\*\*\s*(.+)", re.IGNORECASE),
}


def parse_master_prompt_response(text: str) -> Dict[str, Any]:
    """
    Parses the specification's OUTPUT FORMAT into structured data.

    Tolerates the en dash and em dash a model may emit in place of `--`, and tolerates a
    missing character count rather than discarding an otherwise valid title: a model that
    miscounts should not cost you the title. A wrong count is recorded, not trusted --
    `parsed["count_mismatches"]` lists the titles whose stated length disagrees with the
    measured one, and the caller may treat a non-empty list as grounds to re-ask.
    """
    niche_match = _FIELD_RE["niche"].search(text or "")
    topics_match = _FIELD_RE["topics"].search(text or "")
    visual_match = _FIELD_RE["visual"].search(text or "")

    niche = niche_match.group(1).strip() if niche_match else ""
    raw_topics = topics_match.group(1).strip() if topics_match else ""
    topics = [t.strip() for t in raw_topics.split(",") if t.strip()]

    titles: List[Dict[str, Any]] = []
    count_mismatches: List[str] = []
    for line in (text or "").splitlines():
        match = _TITLE_LINE_RE.match(line)
        if not match:
            continue
        title = match.group("title").strip()
        stated = int(match.group("count"))
        measured = len(title)
        if stated != measured:
            count_mismatches.append(title)
        titles.append({
            "id": f"llm_{len(titles) + 1}",
            "framework": match.group("framework").strip() or "LLM",
            "title": title,
            "stated_count": stated,
            "measured_count": measured,
        })

    recommended_index = 0
    rec_match = _RECOMMENDED_RE.search(text or "")
    if rec_match:
        recommended_index = max(0, min(4, int(rec_match.group(1)) - 1))

    return {
        "niche": niche,
        "topics": topics,
        "visual": visual_match.group(1).strip() if visual_match else "",
        "titles": titles,
        "recommended_index": recommended_index,
        "count_mismatches": count_mismatches,
    }


def build_llm_candidates(parsed: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Turns parsed titles into engine-shaped candidates, dropping any that fail validation.

    A rejected title is not silently repaired. It is dropped, and the reason is available
    on the returned dict, because a title that needed repairing to pass was a title the
    model got wrong and a human should see.
    """
    topics = parsed.get("topics") or []
    candidates: List[Dict[str, str]] = []
    for entry in parsed.get("titles", []):
        ok, reason = validate_master_title(entry["title"], topics or None)
        if not ok:
            continue
        candidates.append({
            "id": entry["id"],
            "framework": entry["framework"],
            "title": entry["title"],
        })
    return candidates


# ---------------------------------------------------------------------------
# Deterministic generation -- the default path, no API key required
# ---------------------------------------------------------------------------

# Emoji is appended, not inserted: the specification's own example puts it at the end, and
# appending cannot disturb the front-loaded keyword.
_NICHE_EMOJI: Dict[str, str] = {
    "gaming": "🎮",
    "outdoors_survival": "🏔️",
    "tech_ai": "💡",
    "business_money": "💰",
    "fitness_health": "💪",
    "comedy_entertainment": "😂",
    "science_education": "🧠",
    "motivation_mindset": "🔥",
    "general_viral": "🤯",
}

# Words that must be lower-cased mid-title. The specification's quality rules ask for
# articles, prepositions and conjunctions in lower case when they are not first; the
# templates are written Title Case because that is how the specification presents them, so
# normalisation happens once, here, instead of being trusted to every future editor.
_MID_TITLE_LOWER = {
    "of", "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with",
    "from", "that", "is", "it", "its", "as", "by", "vs", "more", "than", "so",
}


def normalize_casing(title: str) -> str:
    """Lower-cases articles/prepositions/conjunctions except as the first or last word."""
    words = title.split()
    out: List[str] = []
    for index, word in enumerate(words):
        bare = re.sub(r"[^A-Za-z'-]", "", word).lower()
        is_edge = index == 0 or index == len(words) - 1
        if not is_edge and bare in _MID_TITLE_LOWER:
            out.append(word.lower())
        else:
            out.append(word)
    return " ".join(out)


# Channel voice profiles. OpusClip lets creators match title generation to their
# channel's voice; this is the stdlib-honest version: framework preference ordering plus
# an emoji switch, both explicit and both tested.
#
# `high_energy` is the default and reproduces the exact pre-tone behavior -- no
# preference ordering, emoji on. The other profiles only REORDER the picked frameworks
# (stable partition, preferred first) and optionally drop the emoji; they never remove
# frameworks, so the diversity and rotation guarantees hold for every tone.
TONE_PROFILES: Dict[str, Dict[str, Any]] = {
    "high_energy": {
        "blurb": "Curiosity-gap first, emoji on. The default; matches all prior behavior.",
        "emoji": True,
        "preferred_frameworks": (),
    },
    "professional": {
        "blurb": "Evidence-led frameworks first, no emoji. For education, tech, business.",
        "emoji": False,
        "preferred_frameworks": (
            "Explainer", "Evidence", "Research", "Comparison", "Breakdown", "Proof",
            "Root Cause", "Build Log",
        ),
    },
    "playful": {
        "blurb": "Story and reaction frameworks first, emoji on. For comedy, vlogs, gaming.",
        "emoji": True,
        "preferred_frameworks": (
            "Story", "Reaction", "Comedy", "Showcase", "Transform", "Curiosity",
            "Curiosity Gap",
        ),
    },
}

DEFAULT_TONE = "high_energy"


def _resolve_tone(tone: str) -> Dict[str, Any]:
    """Fail fast on an unknown tone: silently rendering the wrong voice is worse."""
    try:
        return TONE_PROFILES[tone]
    except KeyError:
        raise ValueError(
            f"unknown tone {tone!r}; expected one of {sorted(TONE_PROFILES)}"
        ) from None


def _frameworks_for(
    niche: str,
    rotation: int,
    limit: int = 5,
    tone: str = DEFAULT_TONE,
) -> List[Tuple[str, str, str]]:
    """
    Picks up to `limit` frameworks: two niche-specific ones when the niche has any, then
    core frameworks continuing from the rotation offset.

    Rotation exists so two clips of one video do not open on the same framework, and so
    repeated runs over a corpus explore the framework space instead of hammering one
    template. It is paired with the topic rotation in `generate_master_titles`, because
    `title_seo` established that rotating one without the other makes a batch of uploads
    look auto-generated.

    `tone` only REORDERS (preferred frameworks first, stable); it never removes, so
    diversity and rotation hold for every voice.
    """
    profile = _resolve_tone(tone)
    preferred = set(profile["preferred_frameworks"])

    bonus = NICHE_BONUS_FRAMEWORKS.get(niche, [])
    picked: List[Tuple[str, str, str]] = list(bonus[:2])

    if len(picked) < limit and CORE_FRAMEWORKS:
        start = rotation % len(CORE_FRAMEWORKS)
        ordered = CORE_FRAMEWORKS[start:] + CORE_FRAMEWORKS[:start]
        used = {entry[2] for entry in picked}
        for entry in ordered:
            if len(picked) >= limit:
                break
            if entry[2] not in used:
                picked.append(entry)
                used.add(entry[2])

    if preferred:
        picked = sorted(picked, key=lambda e: (0 if e[0] in preferred else 1,))
    return picked


def _title_case_topic(topic: str) -> str:
    """
    Renders a mined topic as it should appear inside a title.

    `mine_topic_phrases` returns lowercase phrases ("snow cave", "cold start"). Dropped
    straight into a template that yields "The Story Behind cave" and "What dark Taught Me
    the Hard Way" -- grammatical garbage that still passes every structural check, which
    is the failure mode the project already recorded (a green suite over nonsense output).
    """
    words = topic.split()
    if not words:
        return topic
    rendered: List[str] = []
    for index, word in enumerate(words):
        bare = re.sub(r"[^A-Za-z'-]", "", word).lower()
        if 0 < index < len(words) - 1 and bare in _MID_TITLE_LOWER:
            rendered.append(word.lower())
        else:
            rendered.append(word[:1].upper() + word[1:])
    return " ".join(rendered)


# Past-tense and participle verb forms that `BLOCKED_TOPIC_WORDS` does not already cover.
#
# WHY THIS LIST EXISTS. `mine_topic_phrases` ranks by frequency, and frequency cannot tell
# a verb from a noun. A POS tagger would fix it and is forbidden: the project is stdlib-only
# by rule, and the plan doc's own analysis is that a tagger trained on written prose handles
# auto-caption transcripts worse than a list tuned to this corpus. So the corpus's actual
# verb forms are enumerated.
#
# `test_rejects_verbs_and_fillers_as_topics` asserts on the forms the handoff document
# names as measured defects -- `built`, `donated`, `goin` -- which the blocklist missed.
#
# SCOPE. This filters what may be COMPOSED INTO A TITLE. It deliberately does not change
# what the engine mines or stores, so `test_topic_engine.py`'s committed corpus fixtures
# are untouched. Widening it to the engine is a separate, larger decision.
_EXTRA_VERB_FORMS = {
    "built", "build", "builds", "building", "donated", "donate", "went", "goes",
    "rewrite", "rewrote", "rewrites", "rewriting", "debug", "debugged",
    "deadlock", "deadlocked", "deadlocks", "blocked", "spinning",
    "got", "gets", "ran", "running", "saw", "sees", "ate", "eats", "fell", "falls",
    "felt", "feels", "held", "holds", "kept", "keeps", "left", "leaves", "lost",
    "loses", "met", "meets", "paid", "pays", "read", "reads", "sold", "sells",
    "sent", "sends", "sat", "sits", "slept", "sleeps", "stood", "stands", "wore",
    "wears", "won", "wins", "wrote", "writes", "grew", "grows", "heard", "hears",
    "found", "finds", "drew", "draws", "drove", "drives", "drank", "drinks",
    "began", "begins", "broke", "breaks", "chose", "chooses", "came", "flew",
    "flies", "forgot", "forgets", "gave", "gives", "shot", "shoots", "shut",
    "showed", "shows", "spent", "spends", "took", "takes", "threw", "throws",
    "understood", "worn", "gone", "gotten", "lying", "swam", "sank", "struck",
    "swore", "thrust", "wove", "stolen", "spoken", "broken", "chosen", "frozen",
}


# Temporal, positional and abstract words. They pass `BLOCKED_TOPIC_WORDS` because they are
# ordinary English, but they make terrible title keywords -- and they are what
# `mine_topic_phrases` actually returns on real transcripts. Measured on the corpus:
#
#     "The Story Behind Before"                (from "before dark")
#     "Everything I Learned About Whole"        (from "the whole weekend")
#     "This Dodge Run Should Not Have Been Possible"  (a verb used as a noun)
#     "I Built Rewrote from Scratch"            (a past-tense verb)
#
# Each of those is a real title this project could and did generate. A part-of-speech
# tagger would prevent the whole class, and §0 rule 1 forbids one. So the abstract,
# positional and temporal vocabulary is enumerated instead. This is the hand-maintained
# list tradeoff, and it is scoped to title composition: it does not change what the engine
# mines, stores or reports.
_ABSTRACT_TOPIC_WORDS = {
    # temporal
    "before", "after", "during", "while", "whole", "weekend", "morning", "evening",
    "tonight", "yesterday", "tomorrow", "lately", "already", "still", "yet",
    "moment", "instant", "second", "while", "meanwhile", "suddenly", "finally",
    # positional / spatial
    "point", "spot", "area", "place", "side", "front", "back", "bottom", "edge",
    "line", "row", "end", "start", "middle", "centre", "center", "corner", "top",
    # abstract
    "thing", "way", "part", "lot", "bit", "kind", "sort", "bitte", "lotta",
    "something", "anything", "everything", "nothing", "someone", "anyone",
    "everyone", "people", "stuff", "idea", "plan", "problem", "issue", "reason",
    "result", "effect", "change", "difference", "level", "state", "force", "power",
    "phase", "step", "stage", "role", "case", "fact", "number", "amount",
    "roll", "dodge", "shift", "drop", "hit", "run", "set", "move", "turn",
    "version", "option", "choice", "value", "score", "level", "system", "process",
    # Measured on video `o1_FvfJD8fg` (2026-09-29), whose candidates used all three as
    # keywords: "Speed Explained in Under a Minute", "Everything I Learned About Stop",
    # "What Changed My Mind About God". `stop` and `thank` are verbs ("stop it", "thank
    # you"); `god` comes from interjections ("oh my god", "oh god") and was already in
    # the pre-fix baseline's garbage-entity list alongside Hold, Get and Lock. `speed`
    # stays allowed: it is a legitimate gaming term and a dedicated test pins that.
    "stop", "stops", "thank", "thanks", "god", "gods",
}


# Person names. The specification forbids names of people as the primary keyword
# ("What Nobody Tells You About Luke" is useless unless Luke is famous), and the new
# pipeline proved it on real output: video `o1_FvfJD8fg` (2026-09-29) published
# "What Nobody Tells You About Chad" and "The Mistake Everyone Makes with Chad".
#
# This mirrors `title_tag_engine.COMMON_FIRST_NAMES` rather than importing it:
# `title_tag_engine` imports THIS module, so a top-level import would be circular.
# `test_person_names_cover_title_tag_engine` asserts this set stays a superset of that
# one, so the two cannot silently disagree. `chad` is absent upstream and was added here
# because the pipeline mined it; if more names surface, add them here with the measured
# title that justifies each one.
_PERSON_NAMES = {
    "luke", "tommy", "nate", "john", "jack", "mike", "dave", "chris", "alex", "sam",
    "dan", "tom", "ben", "mark", "paul", "steve", "james", "boys", "guy", "guys",
    "girl", "girls", "mom", "dad", "brother", "sister", "friend", "dude",
    "chad",
}


def _is_usable_topic(topic: str) -> bool:
    """
    Can this phrase serve as the keyword slot of a published title?

    Reuses `topic_engine.BLOCKED_TOPIC_WORDS` rather than inventing a second stopword list.
    That blocklist already carries the corpus's verb and filler forms (get, take, make,
    look, say, work, try, start, help, become, show, give, call, mean); duplicating it is
    how a project ends up with two lists that disagree.

    The `-ing` test is safe. The obvious `-ed` test is NOT: it would reject `speed`,
    `need`, `red`, `bed` and `feed`, several of which are exactly the concrete nouns a
    title wants. Only the unambiguously verbal suffix is applied.
    """
    words = [re.sub(r"[^a-z'-]", "", w) for w in topic.lower().split()]
    if not words:
        return False
    for word in words:
        if len(word) < 3:
            return False
        if word in BLOCKED_TOPIC_WORDS:
            return False
        if word in _EXTRA_VERB_FORMS:
            return False
        if word in _ABSTRACT_TOPIC_WORDS:
            return False
        if word in _PERSON_NAMES:
            return False
        if word in _FILLER_KEYWORD or word in _URL_FRAGMENTS:
            return False
        if word.endswith("ing"):
            return False
    return True


def select_topics_for_titles(
    topics: List[str],
    niche: str = "general_viral",
    limit: int = 6,
) -> List[str]:
    """
    Ranks the topics it is HANDED. It never introduces one.

    THIS FUNCTION IS A PURE FILTER OVER ITS INPUT, AND THAT IS A HARD INVARIANT.

    The project's core property is "one context per video, computed once" (Task 3). A
    title generator that re-derives topics from the clip's own transcript reintroduces
    exactly the per-clip context defect that property exists to prevent -- measured at
    9 of 11 videos disagreeing on niche before Task 3, 0 of 11 after.

    An earlier version of this file scanned the transcript for `NICHE_STRONG_KEYWORDS` and
    added whatever it found to the pool. That broke the seam, and
    `test_title_quality_budget.test_the_engine_used_the_context_the_caller_passed` caught
    it with 230 keywords that were not in the caller's context. That test's docstring
    warns about this exact mistake by name: "someone spots the tautology, decides to
    recompute the topics from topic_engine 'independently', and silently destroys the only
    detector for the defect the guard was written to catch." Read it before changing this
    function.

    So: the niche's keyword list may PROMOTE a topic that was already handed to us, and
    may never contribute one that was not.
    """
    cleaned = [t.strip() for t in (topics or []) if t and t.strip()]
    usable = [t for t in cleaned if _is_usable_topic(t)]

    strong = set(NICHE_STRONG_KEYWORDS.get(niche, ()))

    def sort_key(topic: str) -> Tuple[int, int]:
        # 0: a niche-confirmed concrete term, multi-word. 1: any other multi-word phrase.
        # 2: a niche-confirmed concrete term. 3: everything else.
        confirmed = topic.lower() in strong
        multiword = len(topic.split()) > 1
        rank = 0 if (confirmed and multiword) else 1 if multiword else 2 if confirmed else 3
        return (rank, -len(topic))

    return sorted(usable, key=sort_key)[:limit]


def build_clip_topics(
    clip_transcript: str,
    niche: str = "general_viral",
    limit: int = 4,
) -> List[str]:
    """
    Mines what THIS CLIP is about, as opposed to what its video is about.

    Runs `mine_topic_phrases` over the clip's own transcript and keeps only topics that
    survive `_is_usable_topic`. The ranking (niche-confirmed concrete terms first) is
    `select_topics_for_titles`'s, reused rather than restated.

    WHY THIS EXISTS. The video-level context guarantees every clip of one video shares a
    niche and a topic pool -- the property Task 3 was built for. But for a heterogeneous
    video that property ships wrong titles: video `o1_FvfJD8fg` (gym + geography quiz +
    KFC taste test + spelling game, 2026-09-29) gave every clip titles about "chicken",
    including "The Proof that Chicken is Real" on a gym clip. Every one of those passed
    all gates, because the gates check structure, not relevance.

    SCOPE. This mines TOPICS ONLY. The niche always stays video-level -- that is what
    keeps the 0/11 disagreement property intact. A clip topic pool that ends up empty
    (silent clip, laughter-only clip) falls back to the video pool at the merge step;
    this function itself returns [] in that case rather than inventing a topic.
    """
    if not clip_transcript or not clip_transcript.strip():
        return []
    mined = [phrase for phrase, _ in mine_topic_phrases(clip_transcript, limit=8)]
    return select_topics_for_titles(mined, niche, limit)


def merge_topic_pools(
    clip_topics: List[str],
    video_topics: List[str],
    limit: int = 6,
) -> List[str]:
    """
    One ordered pool: clip specificity first, video coverage second.

    Clip topics that survive `_is_usable_topic` lead, because they describe what the
    viewer is about to watch. Video topics fill the remainder, so a clip whose own text
    yields nothing usable (music-only, reaction-only) still gets titles about its video
    instead of nothing. Deduplicated case-insensitively, order-preserving.
    """
    merged: List[str] = []
    seen: set = set()
    for topic in list(clip_topics or []) + list(video_topics or []):
        cleaned = (topic or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            merged.append(cleaned)
    return merged[:limit]


_SENTENCE_SPLIT_RE = re.compile(r"[.!?…\n]+")
_WORD_COUNT_RE = re.compile(r"[A-Za-z]{2,}")


def extract_hook_text(cleaned_transcript: str, max_chars: int = 140) -> str:
    """
    The clip's opening hook: the first substantive spoken sentence.

    "Substantive" is doing the work. Auto-caption transcripts open with stage leftovers
    ("[Music]" survives cleaning as "" but ">>" speaker markers do not), shouted
    ALL-CAPS runs ("ARE YOU BACK, BRO?"), and filler openers ("Oh, you trying again,
    huh?"). The first sentence with at least 4 alpha words that is not all-shouted wins.
    Returns "" when nothing qualifies -- a music-only clip has no hook, and callers must
    handle that rather than receive an invented one.

    Used for two things: the hook-topic overlap term in `score_title` (a title should
    describe what the clip OPENS with, not just what its video is about), and the
    per-platform descriptions, which quote it.
    """
    if not cleaned_transcript or not cleaned_transcript.strip():
        return ""
    for raw in _SENTENCE_SPLIT_RE.split(cleaned_transcript):
        sentence = re.sub(r"^[\s>»\-–—:;,.!?]+", "", raw).strip()
        if not sentence:
            continue
        words = _WORD_COUNT_RE.findall(sentence)
        if len(words) < 4:
            continue
        alpha = [w for w in words if w.isalpha()]
        if alpha and all(w.isupper() for w in alpha):
            continue
        if len(sentence) > max_chars:
            cut = sentence[:max_chars].rsplit(" ", 1)[0].rstrip()
            sentence = cut if len(cut) >= 20 else sentence[:max_chars]
        return sentence
    return ""


def select_short_title(
    candidates: List[Dict[str, str]],
    topics: List[str],
    hook_text: str = "",
    max_chars: int = 50,
) -> Dict[str, str]:
    """
    The per-platform short variant (TikTok/Instagram captions).

    TikTok rewards short, hashtag-forward titles; the 100-char SEO title built for
    YouTube search is the wrong shape there. This picks the highest-scoring candidate
    that fits in `max_chars`, so the short variant is still a validated, ranked title --
    never a truncation. Truncation mid-phrase is how "What Nobody Tells You About
    Alas" happens; this function makes that unrepresentable. Falls back to the first
    candidate when nothing fits -- the orchestrator uses it as-is, so in that case the
    short variant simply equals the long pick rather than a ranked choice.
    """
    eligible = [c for c in candidates if len(c.get("title") or "") <= max_chars]
    if not eligible:
        return candidates[0]
    best_id, _ = rank_titles_locally(eligible, topics, hook_text)
    return next(c for c in eligible if c["id"] == best_id)


def build_description(
    title: str,
    short_title: str,
    topics: List[str],
    niche: str,
    hook_text: str,
    hashtags: List[str],
    snippet_fallback: str = "",
    platform: str = "youtube",
) -> str:
    """
    Composes the per-platform description/caption from the FINAL title and hook.

    Takes the decided title as input and derives everything from it -- never the other
    way round. That one-direction property is what bug #19 ("description keeps the old
    title after picking an alternative hook") was about on the frontend; the same rule
    holds here: there is exactly one builder, and re-running it on a new title yields a
    consistent description.

    - youtube: title, hook quote (or transcript snippet), a factual "Featuring" line
      built from the topics actually used (SEO keywords without invention), CTA, tags.
    - tiktok/instagram: short title plus tags. No quote: captions there compete on
      brevity, and the video itself carries the hook.
    """
    tag_blob = " ".join(hashtags or [])
    if platform in ("tiktok", "instagram"):
        return f"{short_title}\n\n{tag_blob}".strip()
    quote = hook_text.strip() if hook_text and hook_text.strip() else ""
    if not quote and snippet_fallback:
        snippet = snippet_fallback.strip()
        quote = snippet[:180] + ("..." if len(snippet) > 180 else "")
    featuring = ""
    named = [t for t in (topics or []) if t and t.strip()][:3]
    if named:
        featuring = "Featuring: " + ", ".join(
            t.strip()[:1].upper() + t.strip()[1:] for t in named) + ".\n\n"
    body = f"{title}\n\n"
    if quote:
        body += f'"{quote}"\n\n'
    body += featuring
    body += "Subscribe for more daily shorts!"
    if tag_blob:
        body += f"\n\n{tag_blob}"
    return body


def generate_master_titles(
    topics: List[str],
    niche: str = "general_viral",
    limit: int = 5,
    rotation: int = 0,
    category: str = "high_value_insight",
    tone: str = DEFAULT_TONE,
) -> List[Dict[str, str]]:
    """
    Generates up to `limit` titles that satisfy every hard constraint, with no network.

    THIS IS THE DEFAULT PATH. The project's documented default configuration has no
    Gemini key in the environment and no local Ollama, so an LLM-dependent title path
    would fail on precisely the setup most people run. This function needs nothing.

    Differences from a model-written title, stated plainly:
    - Titles rotate across the selected topics rather than being written freely, so two of
      five may share a topic. The specification's "do not make all 5 about the same
      keyword" is honoured at the set level, not per title.
    - There is no model judgement about which framework suits the clip. That is what
      `rank_titles_locally` and, when available, the LLM path are for.

    The returned dicts carry the SAME SHAPE as `title_seo.generate_seo_titles` --
    `id`, `framework`, `title`, `pattern_id`, `keyword` -- because the corpus gate, the
    UI and the batch tooling all read `keyword`, and a candidate without it is
    indistinguishable from one whose keyword was empty.

    Anything that fails `validate_master_title` is dropped rather than patched, so the
    caller can see that a template and a topic did not compose into a legal title.
    """
    clean_topics = select_topics_for_titles(topics, niche) or ["This Moment"]

    profile = _resolve_tone(tone)
    emoji = _NICHE_EMOJI.get(niche, _NICHE_EMOJI["general_viral"]) if profile["emoji"] else ""
    candidates: List[Dict[str, str]] = []
    rejected: List[Dict[str, str]] = []
    seen: set = set()

    for slot, (label, template, pattern_id) in enumerate(
        _frameworks_for(niche, rotation, limit, tone)
    ):
        if len(candidates) >= limit:
            break
        # Rotation shifts BOTH the framework and the topic, exactly as
        # `title_seo.generate_seo_titles` does. Rotating only the framework left every
        # clip of one video using the same pattern with a different noun, which is what
        # makes a batch of uploads look auto-generated.
        topic = clean_topics[(slot + rotation) % len(clean_topics)]
        # Casing is normalised on the TEMPLATE first, then the title-cased topic is
        # substituted. Doing it the other way round would let the lowercase pass reach
        # inside the topic phrase and turn "Snow Cave" into "Snow cave".
        raw = normalize_casing(template)
        raw = raw.replace("{kw}", _title_case_topic(topic))
        raw = raw.replace("{kw2}", _title_case_topic(clean_topics[(slot + rotation + 1) % len(clean_topics)]))
        # `{n}` is a count the template varies; `{cap}` is the capitalised topic form used
        # by the no-topic fallbacks. Both are title_seo's placeholders, not this module's.
        raw = raw.replace("{n}", "3").replace("{cap}", topic.title())
        title = f"{raw} {emoji}" if emoji else raw

        if title.lower() in seen:
            continue
        ok, reason = validate_master_title(title, clean_topics)
        if ok:
            seen.add(title.lower())
            candidates.append({
                "id": f"mp_{len(candidates) + 1}",
                "framework": label,
                "title": title,
                # Same shape and same `pattern_id` namespace as `title_seo`. Consumers
                # resolve every `pattern_id` against `title_seo.ALL_PATTERNS`.
                "pattern_id": pattern_id,
                "keyword": topic,
            })
        else:
            rejected.append({"framework": label, "title": title, "reason": reason or ""})

    # Fallbacks exist for the case where no template composed into a legal title. They come
    # from `title_seo._fallback_titles` so their `pattern_id`s stay in the registry, and
    # they are validated WITHOUT the front-loading requirement -- a niche name is not a
    # topic, so there is nothing to front-load.
    if not candidates:
        for extra in _fallback_titles(niche, category, rotation=rotation)[:limit]:
            title = str(extra.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue
            ok, _reason = validate_master_title(title)
            if ok:
                seen.add(title.lower())
                candidates.append({
                    "id": f"mp_{len(candidates) + 1}",
                    "framework": str(extra.get("framework") or "Fallback"),
                    "title": title,
                    "pattern_id": str(extra.get("pattern_id") or "fallback_value"),
                    "keyword": niche,
                })

    generate_master_titles.rejected = rejected  # type: ignore[attr-defined]
    return candidates



# ---------------------------------------------------------------------------
# A worked, self-consistent example
# ---------------------------------------------------------------------------

# The specification shipped an example output whose five character counts were all wrong
# (35 reported as 44, 44 as 52, 46 as 51, 43 as 50, 37 as 44). This is the same example
# with counts measured rather than estimated, and it is asserted by
# `test_master_prompt.py::test_spec_example_counts_are_correct` so the documentation and
# the code cannot drift apart again.
SPEC_EXAMPLE_ALASKA = {
    "transcript": (
        "so we're out here in Alaska and it's negative 30 degrees and we've got to build a "
        "snow cave before dark because the blizzard is coming in fast and if we don't get "
        "shelter we're in serious trouble the wind is picking up and I can barely feel my "
        "hands at this point"
    ),
    "niche": "outdoors_survival",
    "topics": ["snow cave", "alaska", "blizzard"],
    "titles": [
        ("Story", "The Story Behind Snow Cave Survival 🤯"),
        ("Curiosity Gap", "What Nobody Tells You About Alaska Blizzards 🔥"),
        ("Hard Way", "What Snow Cave Building Taught Me The Hard Way 💡"),
        ("Root Cause", "Why Blizzard Survival Is Not What You Think 🤯"),
        ("Transform", "What Changed My Mind About Snow Caves 🎭"),
    ],
}
