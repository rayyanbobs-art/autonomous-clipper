import re
import os
import time
import requests
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from config import get_jev_api_key, JEV_ENDPOINT
from hashtag_engine import build_hashtags, build_metadata_tags
from topic_engine import classify_niche, mine_topic_phrases, NICHE_TAGS
from title_seo import generate_seo_titles, validate_title, _rotation_for
from title_master_prompt import (
    generate_master_titles,
    rank_titles_locally,
    build_master_prompt,
    build_clip_topics,
    merge_topic_pools,
    extract_hook_text,
    select_short_title,
    build_description,
    DEFAULT_TONE,
)

# Stopwords & filler tokens to suppress when extracting core topic entities
COMMON_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can",
    "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't",
    "down", "during", "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't", "have",
    "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him",
    "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't",
    "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor",
    "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out",
    "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some",
    "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to",
    "too", "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's",
    "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've",
    "your", "yours", "yourself", "yourselves",
    # Spoken filler tokens
    "um", "uh", "like", "you know", "kind of", "sort of", "literally", "actually", "basically",
    "yeah", "yep", "oh", "okay", "alright", "well", "gonna", "wanna", "gotta", "thing", "things",
    "stuff", "really", "just", "hey", "right", "said", "say", "saying", "tell", "telling", "look",
    "music", "applause", "laughter"
}

NICHE_KEYWORDS = {
    "gaming": ["game", "gaming", "gamer", "boss", "weapon", "level", "play", "gameplay", "fps", "elden ring", "minecraft", "fortnite", "cod", "steam", "switch", "ps5", "xbox", "npc", "speedrun", "mod"],
    "outdoors_survival": ["outdoor", "snow", "cave", "tent", "mountain", "alaska", "survival", "bushcraft", "camp", "camping", "fire", "hike", "hiking", "woods", "forest", "fish", "fishing", "hunt", "toboggan"],
    "tech_ai": ["ai", "code", "coding", "software", "api", "model", "python", "computer", "app", "algorithm", "data", "tech", "developer", "prompt", "chatgpt", "llm", "jev", "bot", "cloud"],
    "business_money": ["business", "money", "income", "cash", "finance", "sales", "client", "profit", "investing", "rich", "company", "startup", "crypto", "bitcoin", "market", "wealth", "revenue", "dollar"],
    "fitness_health": ["workout", "gym", "muscle", "diet", "protein", "training", "weight", "exercise", "body", "health", "fitness", "sleep", "calories", "fat", "cardio", "lifting"],
    "comedy_entertainment": ["funny", "laugh", "hilarious", "crazy", "joke", "pranking", "meme", "roast", "weird", "insane", "dumb", "epic", "fail"],
    "science_education": ["science", "facts", "did you know", "education", "study", "experiment", "space", "earth", "biology", "physics", "brain", "psychology", "history", "ancient", "universe"],
    "motivation_mindset": ["mindset", "discipline", "success", "habits", "focus", "goal", "hard work", "productive", "advice", "life", "stoic", "motivation", "wisdom"]
}


COMMON_FIRST_NAMES = {
    "luke", "tommy", "nate", "john", "jack", "mike", "dave", "chris", "alex", "sam", "dan",
    "tom", "ben", "mark", "paul", "steve", "james", "boys", "guy", "guys", "girl", "girls",
    "mom", "dad", "brother", "sister", "friend", "dude"
}

SENTENCE_STARTERS = {
    "let", "look", "well", "now", "then", "just", "so", "there", "here", "this", "that",
    "when", "what", "how", "why", "where", "who", "if", "and", "but", "or", "in", "on",
    "at", "to", "for", "with", "from", "after", "before", "because", "hey", "today",
    "tonight", "tomorrow", "yesterday", "welcome", "everyone", "guys", "folks", "people",
    "somebody", "nobody", "everybody", "someone", "something", "maybe", "first", "second"
}

# High-frequency verbs, adjectives and adverbs. These are the words that made the old bigram
# rule invent junk entities ("year supply", "real reason") that then became bogus hashtags.
NON_ENTITY_WORDS = {
    "get", "gets", "got", "getting", "go", "goes", "going", "went", "gone", "make", "makes",
    "making", "made", "take", "takes", "taking", "took", "taken", "come", "comes", "coming",
    "came", "know", "knows", "knew", "known", "knowing", "think", "thinks", "thought",
    "want", "wants", "wanted", "need", "needs", "needed", "use", "uses", "used", "using",
    "work", "works", "worked", "working", "try", "tries", "tried", "trying", "start",
    "starts", "started", "starting", "turn", "turns", "turned", "put", "puts", "putting",
    "keep", "keeps", "kept", "keeping", "let", "lets", "help", "helps", "helped", "become",
    "becomes", "became", "show", "shows", "showed", "see", "sees", "saw", "seen", "look",
    "looks", "looked", "say", "says", "said", "find", "finds", "found", "leave", "leaves",
    "left", "put", "tell", "tells", "told", "ask", "asks", "asked", "seem", "seems",
    "seemed", "feel", "feels", "felt", "happen", "happens", "happened", "guy", "guys",
    "big", "small", "huge", "tiny", "great", "good", "bad", "best", "worst", "right",
    "wrong", "real", "true", "false", "full", "empty", "new", "old", "young", "fast",
    "slow", "easy", "hard", "long", "short", "high", "low", "own", "same", "different",
    "other", "another", "every", "many", "much", "more", "most", "less", "least",
    "very", "really", "quite", "rather", "almost", "always", "never", "often", "sometimes",
    "actually", "basically", "literally", "probably", "maybe", "anyway", "however",
    "still", "already", "even", "also", "back", "down", "over", "under", "again",
    "before", "after", "around", "through", "during", "without", "within", "across",
    "everywhere", "somewhere", "anywhere", "definitely", "probably", "certainly"
}

_DOMAIN_TERMS = {
    kw.lower()
    for kw_list in NICHE_KEYWORDS.values()
    for kw in kw_list
}


def _is_domain_term(word_lower: str) -> bool:
    """True when the token is a recognised domain keyword for any tracked niche."""
    if word_lower in _DOMAIN_TERMS:
        return True
    return any(len(tok) > 2 and tok in word_lower for tok in _DOMAIN_TERMS if " " in tok)


def _entity_to_tag(entity: str) -> Optional[str]:
    """
    Converts a scored entity into a single valid hashtag token.

    A multi-word entity used to be space-stripped into one unbroken string, producing
    fabricated tags such as #athleticgreens and #yearsupply. Only ONE real word is used now:
    the head of the phrase (its last content word), falling back to the longest content word.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", entity or "") if len(t) > 2]
    if not tokens:
        return None

    content = [t for t in tokens if t.lower() not in COMMON_STOPWORDS and t.lower() not in NON_ENTITY_WORDS]
    if not content:
        return None

    # Head of the phrase = its last content word ("Snow Cave" -> #cave, "Hot Tent" -> #tent)
    chosen = content[-1]
    if len(chosen) < 3:
        chosen = max(content, key=len)
    if len(chosen) < 3:
        return None
    return f"#{chosen.lower()}"


def clean_transcript_text(text: str) -> str:
    """Removes subtitle markers, brackets, and redundant whitespace."""
    if not text:
        return ""
    text = re.sub(r"\[.*?\]", " ", text)
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_topical_entities(text: str) -> List[Tuple[str, float]]:
    """
    Extracts prominent named entities, multi-word compound phrases, numbers,
    and high-frequency domain keywords, scored by viral importance.
    """
    cleaned = clean_transcript_text(text)
    if not cleaned:
        return []

    scores: Dict[str, float] = {}
    text_lower = cleaned.lower()

    # 1. Number + noun combos (e.g. "6 Feet Under", "200 Milliseconds", "30 Seconds")
    numbered_phrases = re.findall(r"\b\d+\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\b", cleaned)
    for phrase in numbered_phrases:
        p_clean = phrase.strip().title()
        scores[p_clean] = scores.get(p_clean, 0.0) + 7.5

    # 2. Boost domain niche keywords heavily (e.g. "Snow", "Cave", "Alaska", "Survival", "AI", "Jev")
    for niche, kw_list in NICHE_KEYWORDS.items():
        for kw in kw_list:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                scores[kw.title()] = scores.get(kw.title(), 0.0) + 7.0

    # 3. Lowercase compound domain terms (e.g. "cold start", "hot tent").
    #    The old version fired on ANY adjacent pair of non-stopwords with the highest weight
    #    of any rule (8.5), which invented junk entities ("year supply", "real reason") that
    #    then outranked real proper nouns and became broken hashtags. A compound is now only
    #    accepted when at least one word is a recognised domain keyword, and it scores BELOW
    #    a single proper noun.
    words = re.findall(r"\b[a-zA-Z0-9_\-']+\b", cleaned)
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower().strip("'"), words[i + 1].lower().strip("'")
        if not (len(w1) > 2 and len(w2) > 2):
            continue
        if (w1 in COMMON_STOPWORDS or w2 in COMMON_STOPWORDS or
                w1 in SENTENCE_STARTERS or w2 in SENTENCE_STARTERS or
                w1 in NON_ENTITY_WORDS or w2 in NON_ENTITY_WORDS or
                w1.isdigit() or w2.isdigit()):
            continue
        # Require a domain anchor so generic verb+noun pairs are never promoted.
        if not _is_domain_term(w1) and not _is_domain_term(w2):
            continue
        bigram = f"{words[i]} {words[i + 1]}"
        scores[bigram] = scores.get(bigram, 0.0) + 7.6

    # 4. Multi-word Proper Noun phrases (e.g. "Outdoor Boys", "Elden Ring")
    multi_proper = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", cleaned)
    for phrase in multi_proper:
        p_clean = phrase.strip()
        lower_p = p_clean.lower()
        if lower_p not in COMMON_STOPWORDS and lower_p not in SENTENCE_STARTERS:
            scores[p_clean] = scores.get(p_clean, 0.0) + 8.0

    # 5. Standalone Proper Nouns (e.g. "Alaska", "Jev")
    single_proper = re.findall(r"\b[A-Z][a-z]{2,}\b", cleaned)
    for p in single_proper:
        lower_p = p.lower()
        if lower_p not in COMMON_STOPWORDS and lower_p not in SENTENCE_STARTERS and lower_p not in NON_ENTITY_WORDS:
            weight = 2.0 if lower_p in COMMON_FIRST_NAMES else 6.0
            scores[p] = scores.get(p, 0.0) + weight

    # Deduplicate: if a single word is contained in a higher-scoring multi-word phrase, penalize the single word
    final_scores: Dict[str, float] = dict(scores)
    for phrase, score in scores.items():
        if " " in phrase:
            for part in phrase.split():
                if part in final_scores and final_scores[part] <= score:
                    final_scores[part] *= 0.25

    sorted_entities = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    filtered = [e for e in sorted_entities if e[1] >= 4.0]
    return filtered[:6] if filtered else sorted_entities[:4]


def find_punchy_quote(text: str) -> Optional[str]:
    """Finds the punchiest, most emotionally resonant spoken sentence (4-12 words)."""
    cleaned = clean_transcript_text(text)
    raw_clauses = re.split(r"[.!?,\n]+", cleaned)
    candidates = []
    
    strong_triggers = {
        "truth", "secret", "never", "always", "guarantee", "shocking", "insane",
        "hate", "love", "crazy", "killer", "broken", "mistake", "best", "worst", "stop",
        "buckle up", "changed", "warning", "exposed"
    }

    for c in raw_clauses:
        c_strip = c.strip().strip('"\'')
        words = c_strip.split()
        if 4 <= len(words) <= 12:
            score = 0.0
            lower_words = [w.lower() for w in words]
            for tw in strong_triggers:
                if tw in lower_words:
                    score += 4.0
            if words[0].lower() in {"i", "we", "he", "they", "you", "this"}:
                score += 2.0
            candidates.append((c_strip, score))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    best_quote = candidates[0][0]
    return best_quote[0].upper() + best_quote[1:] if best_quote else None


def classify_niche_heuristic(text: str) -> str:
    """Classifies the content niche using keyword density heuristics."""
    text_lower = clean_transcript_text(text).lower()
    scores = {niche: 0 for niche in NICHE_KEYWORDS}

    for niche, kw_list in NICHE_KEYWORDS.items():
        for kw in kw_list:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                scores[niche] += 2

    best_niche = max(scores.items(), key=lambda x: x[1])
    if best_niche[1] > 0:
        return best_niche[0]
    return "general_viral"


def resolve_title_topics(
    transcript_text: str,
    video_context: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], str]:
    """
    The single place the title topic pool is decided. Returns (merged_pool, niche).

    Clip specificity over video coverage. The video pool keeps every clip of one video
    anchored to the same subject, but for a heterogeneous video it ships wrong titles
    ("The Proof that Chicken is Real" on a gym clip, video o1_FvfJD8fg). So the clip's
    own usable topics lead and the video pool fills the remainder. The niche stays
    video-level -- that is what preserves the 0/11 disagreement property.

    A caller may pre-attach `clip_topics` (mined however it likes); otherwise they are
    mined here from the clip text the caller already passed. Nothing is read from
    anywhere else, so the seam property holds: the engine uses caller-supplied context
    plus the clip text it was handed, and nothing it went and found on its own.

    Both `generate_candidate_titles` and `generate_smart_title_and_hashtags` resolve
    through here so generation and ranking can never disagree about the pool.
    """
    if video_context is None:
        video_context = build_video_context(transcript_text)

    video_topics = video_context.get("topics") or []
    niche = video_context.get("niche") or "general_viral"

    clip_topics = video_context.get("clip_topics")
    if clip_topics is None:
        clip_topics = build_clip_topics(transcript_text, niche)
    return merge_topic_pools(clip_topics, video_topics), niche


def generate_candidate_titles(
    transcript_text: str,
    category: str = "high_value_insight",
    video_context: Optional[Dict[str, Any]] = None,
    *,
    tone: str = DEFAULT_TONE,
) -> List[Dict[str, str]]:
    """
    Generates 5 validated, SEO-optimised candidate titles for one clip.

    Previously this produced 5 niche-gated templates filled with a per-clip "entity",
    62% of which collapsed to "Why Nobody Tells You The Truth About {garbage}" because
    the entity came from casing-driven proper-noun matching on auto-captions.
    """
    if video_context is None:
        video_context = build_video_context(transcript_text)

    topics, niche = resolve_title_topics(transcript_text, video_context)
    rotation = _rotation_for(transcript_text)

    # The master prompt is the primary path. It states the constraints explicitly and its
    # output is validated by `validate_master_title`, which layers the front-loading,
    # repeated-word, ellipsis and minimum-real-word rules on top of the publication gate.
    master = generate_master_titles(
        topics,
        niche=niche,
        limit=5,
        rotation=rotation,
        tone=tone,
    )
    if len(master) >= 3:
        return master

    # Anything shorter means the master prompt's templates did not compose against these
    # topics. `generate_seo_titles` is kept as a second opinion rather than deleted: it
    # draws on a different pattern set, so the two fail on different inputs.
    return generate_seo_titles(
        topics,
        niche=niche,
        category=category,
        limit=5,
        rotation=rotation,
    )


def _as_float(val: Any, default: float) -> float:
    """Safe numeric conversion for Jev responses; handles None, strings, and NaN."""
    if val is None or isinstance(val, bool):
        return default
    try:
        f = float(val)
        return default if f != f else f
    except (ValueError, TypeError):
        return default


def score_and_rank_titles_with_jev(
    candidates: List[Dict[str, str]],
    transcript_text: str,
    api_key: Optional[str] = None,
    *,
    topics: Optional[List[str]] = None,
    hook_text: str = "",
    visual_context: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, float]:
    """
    Submits candidate titles to Jev System One for parallel CTR judgment.
    Returns: (selected_title_id, predicted_niche, confidence)

    WHEN JEV IS UNAVAILABLE, THE BEST CANDIDATE IS NOW ACTUALLY SELECTED.

    Every fallback here used to return `candidates[0]["id"]`, which is the first title in
    the list and has nothing to do with quality. Five titles were generated, scored by
    nobody, and the first was published -- the other four were computed and discarded. So
    the published title was a function of list order, and any change to the generator's
    ordering silently changed what shipped. `rank_titles_locally` replaces that with a
    deterministic CTR-proxy score that needs no network and no key.

    `topics`, `hook_text`, and `visual_context` are keyword-only and optional so the existing
    positional call signature is unchanged; when `topics` is absent the topics are re-mined
    from the transcript, which is what the caller would have passed anyway.
    """
    if not candidates:
        raise ValueError("score_and_rank_titles_with_jev requires at least one candidate")

    resolved_topics = topics
    if resolved_topics is None:
        resolved_topics = [p for p, _ in mine_topic_phrases(transcript_text or "", limit=8)]

    if not api_key:
        api_key = get_jev_api_key()

    # If no key or offline, fall back to deterministic local ranking
    if not api_key:
        best_id, _score = rank_titles_locally(candidates, resolved_topics, hook_text)
        return best_id, classify_niche_heuristic(transcript_text), 0.75

    choice_criteria = {c["id"]: c["title"] for c in candidates}

    questions: Dict[str, Any] = {
        "best_title_hook": {
            "type": "choice",
            "instructions": "Select the title hook that creates the strongest curiosity gap, emotional urgency, and highest click-through rate (CTR) for YouTube Shorts, TikTok, and Instagram Reels.",
            "criteria": choice_criteria
        },
        "content_niche": {
            "type": "choice",
            "instructions": "Classify the exact content niche of this short-form video clip.",
            "criteria": {
                "gaming": "Video games, playthroughs, game tips",
                "outdoors_survival": "Wilderness, outdoor adventures, camping, bushcraft, survival",
                "tech_ai": "Artificial intelligence, software, coding, hardware, gadgets",
                "business_money": "Business, finance, entrepreneurship, investing, wealth",
                "fitness_health": "Workouts, gym, bodybuilding, health, nutrition",
                "comedy_entertainment": "Funny moments, pranks, memes, humor",
                "science_education": "Interesting facts, science, learning, psychology, history",
                "motivation_mindset": "Self-discipline, success, productivity, stoicism",
                "general_viral": "General interest, trending news, storytelling"
            }
        },
        "clickbait_penalty": {
            "type": "noul",
            "instructions": "Is this title misleading, deceptive, or promising something that neither the transcript nor the visuals deliver?"
        },
        "hook_ctr_potential": {
            "type": "score",
            "instructions": "Rate the overall viral appeal and click-through potential of this clip's spoken hook.",
            "criteria": [
                "Boring or generic hook",
                "Mildly interesting hook",
                "Strong curiosity hook with high engagement",
                "Exceptional viral hook with breakthrough potential"
            ]
        }
    }

    state_payload: Dict[str, Any] = {
        "transcript_snippet": transcript_text[:1200],
        "title_options": [c["title"] for c in candidates]
    }
    if visual_context:
        state_payload["visual_clip_context"] = visual_context
        questions["visual_clip_alignment"] = {
            "type": "noul",
            "instructions": "Does the highest-ranked title accurately reflect what is physically visible in the real-time visual clip context?"
        }

    payload = {
        "model": "typesafe-ai/jev",
        "state": state_payload,
        "questions": questions
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(JEV_ENDPOINT, json=payload, headers=headers, timeout=8)
        if res.status_code == 200:
            answers = res.json().get("data", {}).get("answers", {})
            best_id = answers.get("best_title_hook", {}).get("choice")
            conf = _as_float(answers.get("best_title_hook", {}).get("confidence"), 0.85)
            clickbait_prob = _as_float(answers.get("clickbait_penalty"), 0.0)
            visual_prob = _as_float(answers.get("visual_clip_alignment"), 1.0)

            valid_id = bool(best_id and any(c["id"] == best_id for c in candidates))

            # Calibrated Decision Guardrails (Jev Protocol):
            # 1. Low confidence (< 0.60): Jev decision rule mandates making call locally or falling back
            # 2. Clickbait penalty: If probability > 0.50, title is deceptive; reject pick
            # 3. Visual alignment: If visual context was provided and alignment probability < 0.40, visual mismatch; reject pick
            if valid_id and conf >= 0.60 and clickbait_prob <= 0.50 and visual_prob >= 0.40:
                niche = answers.get("content_niche", {}).get("choice", classify_niche_heuristic(transcript_text))
                return best_id, niche, conf
    except Exception:
        pass

    # Resilient fallback: deterministic local ranking, not "the first one".
    best_id, _score = rank_titles_locally(candidates, resolved_topics, hook_text)
    return best_id, classify_niche_heuristic(transcript_text), 0.70


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


def generate_smart_title_and_hashtags(
    transcript_text: str,
    category: str = "high_value_insight",
    api_key: Optional[str] = None,
    *,
    video_context: Optional[Dict[str, Any]] = None,
    tone: str = DEFAULT_TONE,
    visual_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    End-to-end engine generating viral title and tailored hashtags for YouTube Shorts,
    TikTok, and Instagram Reels using Jev System One decisions and transcript NLP.

    `tone` selects the channel voice ("high_energy", "professional", "playful"); see
    `title_master_prompt.TONE_PROFILES`. The default reproduces all prior behavior.
    `visual_context` provides optional real-time video frame/clip metadata (scene, action, OCR)
    which Jev verifies via visual_clip_alignment.
    """
    if video_context is None:
        video_context = build_video_context(transcript_text)

    cleaned = clean_transcript_text(transcript_text)
    if not cleaned:
        # The clip's own text cleaned to nothing -- a stage direction only ("[Music]",
        # "[Applause]", "(upbeat music)", "[BLANK_AUDIO]" all reduce to ""). That is
        # reachable: scorer admits any window with a non-empty line list, and a silent or
        # laughter-only clip has exactly that shape.
        #
        # This used to short-circuit with a hardcoded five-tag pad
        # (`#shorts #viral #trending #fyp #explore`) and a `replace("#", "")` strip of its
        # own making -- i.e. the OLD engine, still emitting five tags with no relation to
        # the content, on a path that also THREW AWAY the `video_context` it had just been
        # handed. That is precisely the bug class Task 5 exists to remove, and because the
        # path was untested nothing noticed: a mutant that modernised those five tags
        # passed the entire 261-test suite.
        #
        # It now goes through the same engine as every other clip. A silent clip still gets
        # a generic result -- there is genuinely nothing to say about it -- but it is
        # derived, it is a consistent size, and if the video's context survived, the niche
        # and topics are used.
        default_title = "Viral Moment You Need To See 🤯"
        topics_for_tags = video_context.get("topics") or []
        resolved_niche = video_context.get("niche") or "general_viral"
        default_tags = build_hashtags(topics_for_tags, resolved_niche)
        raw_tags = build_metadata_tags(default_tags, topics_for_tags, resolved_niche)
        tag_blob = " ".join(default_tags)
        return {
            "suggested_title": default_title,
            "suggested_hashtags": default_tags,
            "niche": resolved_niche,
            "candidates": [{"id": "default", "framework": "Default", "title": default_title}],
            "platform_metadata": {
                "youtube": {"title": f"{default_title} #Shorts", "description": f"{default_title}\n\n{tag_blob}", "tags": raw_tags},
                "tiktok": {"caption": f"{default_title} {tag_blob}"},
                "instagram": {"caption": f"{default_title}\n.\n.\n{tag_blob}"}
            }
        }

    # 1. Generate 5 diverse candidates (clip topics lead, video pool fills).
    candidates = generate_candidate_titles(
        cleaned, category, video_context=video_context, tone=tone
    )

    # 2. Evaluate with Jev, or rank locally when Jev is unavailable. Ranked against the
    # same merged pool the titles were composed from, resolved once through the helper,
    # with the clip's opening hook so titles describing what the viewer hears first win.
    title_topics, _ = resolve_title_topics(cleaned, video_context)
    hook_text = extract_hook_text(cleaned)
    best_id, niche, conf = score_and_rank_titles_with_jev(
        candidates, cleaned, api_key, topics=title_topics, hook_text=hook_text,
        visual_context=visual_context
    )

    # Find the winning candidate title
    winning_cand = next((c for c in candidates if c["id"] == best_id), candidates[0])
    best_title = winning_cand["title"]

    # 3. Tags: a short, topic-anchored set. Replaces the 7-tag quota pad.
    topics_for_tags = video_context.get("topics") or []
    resolved_niche = video_context.get("niche") or "general_viral"
    final_hashtags = build_hashtags(topics_for_tags, resolved_niche)
    raw_tags = build_metadata_tags(final_hashtags, topics_for_tags, resolved_niche)

    # 4. Platform-specific packaging. YouTube gets the full SEO title; TikTok and
    # Instagram get the short variant (<= 50 chars, still a validated ranked title,
    # never a truncation). Descriptions are derived from the FINAL title in one place.
    short_cand = select_short_title(candidates, title_topics, hook_text)
    short_title = short_cand["title"]
    yt_title = f"{best_title} #shorts" if "#shorts" not in best_title else best_title
    yt_desc = build_description(
        best_title, short_title, title_topics, resolved_niche, hook_text,
        final_hashtags, snippet_fallback=cleaned, platform="youtube",
    )
    tiktok_caption = build_description(
        best_title, short_title, title_topics, resolved_niche, hook_text,
        final_hashtags, platform="tiktok",
    )
    ig_body = build_description(
        best_title, short_title, title_topics, resolved_niche, hook_text,
        final_hashtags, platform="instagram",
    )
    ig_caption = ig_body.replace("\n\n", "\n.\n.\n", 1) if "\n\n" in ig_body else ig_body

    return {
        "suggested_title": best_title,
        "short_title": short_title,
        "suggested_hashtags": final_hashtags,
        "niche": resolved_niche,
        "confidence": conf,
        "candidates": candidates,
        "winning_framework": winning_cand["framework"],
        "hook_text": hook_text,
        "visual_grounding": bool(visual_context),
        "platform_metadata": {
            "youtube": {
                "title": yt_title,
                "description": yt_desc,
                "tags": raw_tags
            },
            "tiktok": {
                "caption": tiktok_caption
            },
            "instagram": {
                "caption": ig_caption
            }
        }
    }
