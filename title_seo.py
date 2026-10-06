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

# Where the searchable core should appear for YouTube to surface the title.
#
# **This is a design target, not an enforced invariant** -- nothing in the engine reads it,
# and it should not be mistaken for a guarantee. What is actually true, measured on the real
# 45-clip corpus: 225 of 225 generated candidates front-load their keyword, because real
# mined keywords average 5 characters and top out at 12.
#
# The binding constraint is the TEMPLATE PREFIX, not the keyword. With a 45-char window, a
# 12-character keyword (the real maximum) needs a prefix of at most 33 characters. Three
# templates were over that and were shortened: `mistake_money` had a 39-character prefix,
# which pushed the keyword out for *any* keyword over 6 characters -- a real defect, found
# by `test_searchable_core_is_front_loaded_even_for_a_long_keyword`. `the_mistake`,
# `watch_this` and `nobody_tells_you` sit at 28-32 and are fine at real keyword lengths.
#
# Beyond roughly 17 characters some prefixes cannot fit at all -- a 25-character keyword
# would need a 20-character prefix, and the bank's longest unavoidable prefix is 24. That is
# a physical limit of the window, not a bug, and the test probes the real maximum rather than
# an unreachable one.
#
# It is deliberately NOT enforced by `validate_title`. Rejecting a title for a soft
# readability preference is exactly the over-broad-gate failure that once invalidated 100% of
# output; a 45-char window is a preference, not a publication rule.
FRONT_LOAD_CHARS = 45

_EMOJI = {
    "shocking_revelation": "\U0001F92F",
    "controversial_opinion": "\U0001F525",
    "compelling_story": "\U0001F3AD",
    "high_value_insight": "\U0001F4A1",
    "filler_banter": "\U0001F602",
}

SEO_PATTERNS: List[Dict[str, str]] = [
    {"id": "how_it_works", "framework": "Explainer", "template": "The Mechanics Of {kw} Explained Simply"},
    {"id": "the_proof", "framework": "Proof", "template": "The Proof That {kw} Is Real"},
    {"id": "why_it_happens", "framework": "Root Cause", "template": "Why {kw} Is Not What You Think"},
    {"id": "count_facts", "framework": "List", "template": "{n} Facts About {kw} That Change Everything"},
    {"id": "nobody_tells_you", "framework": "Curiosity Gap", "template": "What Nobody Tells You About {kw}"},
    {"id": "i_tried", "framework": "First Person", "template": "Everything I Learned About {kw} In {n} Days"},
    {"id": "before_after", "framework": "Transform", "template": "What Changed My Mind About {kw}"},
    {"id": "the_mistake", "framework": "Loss Aversion", "template": "The Mistake Everyone Makes With {kw}"},
    {"id": "explained_plainly", "framework": "Simple", "template": "{kw} Explained In Under A Minute"},
    {"id": "watch_this", "framework": "Curiosity", "template": "Watch This: The Truth About {kw}"},
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
    {"id": "evidence", "framework": "Evidence", "template": "What Research Says About {kw}"},
    {"id": "build_log", "framework": "Build Log", "template": "I Built {kw} From Scratch"},
    {"id": "comparison", "framework": "Comparison", "template": "{kw}: Old School vs What Works Now"},
    {"id": "income_breakdown", "framework": "Breakdown", "template": "The Real Numbers Behind {kw}"},
    {"id": "mistake_money", "framework": "Loss Aversion", "template": "The Biggest Mistake On {kw}"},
    {"id": "transformation", "framework": "Transform", "template": "{n} Weeks Of {kw} Changed Everything"},
    {"id": "run_showcase", "framework": "Showcase", "template": "This {kw} Run Should Not Have Been Possible"},
    {"id": "survival_story", "framework": "Story", "template": "The Story Behind {kw}"},
    {"id": "lesson", "framework": "Lesson", "template": "What {kw} Taught Me The Hard Way"},
    {"id": "reaction", "framework": "Reaction", "template": "I Was Not Ready For {kw}"},
    {"id": "setup_punch", "framework": "Comedy", "template": "The Wildest Part About {kw}"},
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
_PATTERNS_BY_ID: Dict[str, Dict[str, str]] = {p["id"]: p for p in ALL_PATTERNS}

# Bare URL tokens. `mine_topic_phrases` rejects a whole URL, but auto-captions also split
# one into individual words, and each of those is alphanumeric and unblocked -- so without
# this they became valid-looking titles like "What Nobody Tells You About dot" and "The
# Proof That https Is Real". `hashtag_engine` grew the same guard independently.
_URLISH = {
    "http", "https", "www", "com", "org", "net", "edu", "gov", "io", "co",
    "htm", "html", "php", "aspx", "url", "link", "links", "linkinbio",
    "subscribe", "watch", "dot", "click", "href",
}

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
            and w.lower() not in _URLISH
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

    **This is NOT a safety or XSS gate, and it should not become one.** It accepts
    `<script>alert(1)</script>`, RTL-override and zero-width characters, embedded newlines
    and NULs, and bare URLs, all of which it will happily pass. That is deliberate: the
    real defences are upstream and downstream, and both were verified --
    `_sanitize_phrase`'s `[A-Za-z][A-Za-z'-]*` cannot match `<`, `>`, a newline, a NUL, a
    ZWSP or an RLO, so a hostile topic cannot inject markup through the keyword slot; and
    every consumer in `templates/index.html` runs the value through `escapeHtml` before it
    reaches an `innerHTML` sink. Read the "last line of defence" line above as "the last
    line of defence against publishing a *bad title*", not against publishing dangerous
    markup. Anything that widens this gate must be justified against the failure recorded
    in the first paragraph: an over-broad gate silently invalidates 100% of output.
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
        # `[:limit]` is required, not cosmetic: this branch returned 5 titles for every
        # limit except 5, so `limit=0` produced five titles. The pattern path below does
        # slice. Task 5 is the next consumer of this API and would have inherited it.
        return _fallback_titles(niche, category, rotation=rotation)[:limit]

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
