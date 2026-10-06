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
