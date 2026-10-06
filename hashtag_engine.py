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

    **All clips of one video therefore get the same tag set, and that is by design.** The
    topics and niche come from the shared `video_context`, which Task 3 computes once per
    video, so 45 clips across 11 videos produce 11 distinct sets. This docstring previously
    implied per-clip differentiation that does not exist and never did. Per-clip variation
    would mean re-deriving from the clip's own 30 s window, which is the bug Task 3 removed;
    a mutation doing exactly that is killed by the suite.

    Two caveats a caller should know:

      * `reserved_for_topics` counts the RAW topic list, not the tags that survive
        `_clean_tag`. Unusable topics therefore reserve slots nothing fills *and* cost the
        niche a tag: `build_hashtags(["hold","get","bro","yeah"], "general_viral")` returns
        3 tags where `build_hashtags([], "general_viral")` returns 4. Latent on the real
        corpus, where every clip fills its budget.
      * A topic whose cleaned tag equals a niche tag is absorbed by the dedup and its slot
        is reassigned to `#fyp`. No content is lost -- the identical tag is present -- but
        the set differs from one where the collision does not occur.
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


def _plausible_plural(word: str) -> bool:
    """
    Is `word` likely a regular plural, such that dropping its trailing "s" yields a real word?

    YouTube's `tags` field benefits from both forms, so `molecules` should also offer
    `molecule`. But a naive `endswith("s") and len > 4` mangles every short word that merely
    happens to end in s, and that is a REGRESSION against the old `lstrip` path, which did no
    singularisation at all. Measured on the real 45-clip corpus, the naive rule produced junk
    search terms for 3 of 11 videos:

        thanks -> thank      paris -> pari      press -> pres      class -> clas

    Two cheap rules remove every observed false positive without a dictionary:

      * `ss` ending is almost never a plural (`press`, `class`, `boss`), so refuse it.
      * below 7 characters there is no room for a stem plus a suffix, so refuse it. This is
        what excludes `paris` and `thanks`, and it is the boundary the tests pin.

    `molecules` (9) and `carrots` (7) still pass. This is a heuristic, deliberately: a real
    morphological check would need a word list, and the plan rejects new dependencies.
    """
    if not word.endswith("s") or word.endswith("ss"):
        return False
    return len(word) >= 7


def build_metadata_tags(hashtags: List[str], topics: List[str],
                        niche: str = "general_viral") -> List[str]:
    out: List[str] = []
    seen = set()

    def add(value: str) -> None:
        v = value.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)

    # `str(niche)` was turning a None niche into the literal tag "None", which is worse than
    # raising. `classify_niche` only ever returns a NICHE_TAGS key or "general_viral", and
    # the caller already coalesces falsy values, so this is defensive -- but defensively it
    # should degrade to a real tag, not invent one out of the NoneType's name.
    if niche:
        add(str(niche).replace("_", " "))
    for topic in (topics or [])[:6]:
        clean = normalize_for_tag(topic)
        if not clean:
            continue
        add(clean.replace("_", " "))
        if _plausible_plural(clean):
            add(clean[:-1])
    for tag in (hashtags or [])[:4]:
        add(tag.lstrip("#"))

    return out[:15]
