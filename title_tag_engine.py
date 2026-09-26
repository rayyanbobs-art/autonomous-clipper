import re
import os
import time
import requests
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from config import get_jev_api_key, JEV_ENDPOINT

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

NICHE_HASHTAGS = {
    "gaming": ["#gaming", "#gamingshorts", "#gamer", "#gameplay", "#pcgaming", "#gamingcommunity"],
    "outdoors_survival": ["#outdoors", "#survival", "#bushcraft", "#camping", "#wilderness", "#adventure"],
    "tech_ai": ["#tech", "#ai", "#artificialintelligence", "#software", "#coding", "#techtok"],
    "business_money": ["#business", "#money", "#entrepreneur", "#finance", "#wealth", "#investing"],
    "fitness_health": ["#fitness", "#gym", "#workout", "#health", "#bodybuilding", "#fitnesstips"],
    "comedy_entertainment": ["#comedy", "#funny", "#humor", "#viral", "#entertainment", "#relatable"],
    "science_education": ["#science", "#facts", "#didyouknow", "#education", "#learning", "#curiosity"],
    "motivation_mindset": ["#motivation", "#mindset", "#discipline", "#success", "#selfgrowth", "#inspiration"],
    "general_viral": ["#shorts", "#viral", "#trending", "#fyp", "#explore", "#shortsfeed"]
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

    # 3. High-signal multi-word phrases (e.g. "Outdoor Boys", "Snow Cave", "Hot Tent", "Toboggan Run", "Issue Triage")
    words = re.findall(r"\b[a-zA-Z0-9_\-']+\b", cleaned)
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower().strip("'"), words[i + 1].lower().strip("'")
        if (w1 not in COMMON_STOPWORDS and w2 not in COMMON_STOPWORDS and 
            w1 not in SENTENCE_STARTERS and len(w1) > 2 and len(w2) > 2 and
            w1 not in {"ve", "re", "ll", "got", "let"} and w2 not in {"ve", "re", "ll", "got", "let"}):
            bigram = f"{words[i].capitalize()} {words[i + 1].capitalize()}"
            scores[bigram] = scores.get(bigram, 0.0) + 8.5

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
        if lower_p not in COMMON_STOPWORDS and lower_p not in SENTENCE_STARTERS:
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


def generate_candidate_titles(transcript_text: str, category: str = "high_value_insight") -> List[Dict[str, str]]:
    """
    Generates 5 distinct high-CTR viral candidate titles using proven short-form hook frameworks.
    """
    cleaned = clean_transcript_text(transcript_text)
    entities = extract_topical_entities(cleaned)
    punchy_quote = find_punchy_quote(cleaned)

    top_entity = entities[0][0] if entities else "This Secret"
    sub_entity = entities[1][0] if len(entities) > 1 else ""

    KNOWN_LOCATIONS = {"alaska", "japan", "mountains", "forest", "desert", "arctic", "woods", "snow", "wilderness"}
    top_lower = top_entity.lower()
    sub_lower = sub_entity.lower() if sub_entity else ""

    # If top entity is a geographic location and secondary entity is a subject, swap them
    if top_lower in KNOWN_LOCATIONS and sub_entity and sub_lower not in KNOWN_LOCATIONS:
        top_entity, sub_entity = sub_entity, top_entity
        top_lower, sub_lower = sub_lower, top_lower

    niche = classify_niche_heuristic(cleaned)
    candidates = []

    # 1. Curiosity Gap / Mystery Hook
    if niche == "outdoors_survival":
        if sub_lower in KNOWN_LOCATIONS:
            title_curiosity = f"Surviving in {sub_entity} With {top_entity} 🥶"
        elif top_lower in KNOWN_LOCATIONS:
            title_curiosity = f"The Secret To Surviving In {top_entity} ❄️"
        else:
            title_curiosity = f"Why Nobody Talks About {top_entity}... 😳"
    elif niche == "tech_ai":
        title_curiosity = f"Why Nobody Talks About {top_entity} in 2026... ⚡"
    elif niche == "gaming":
        title_curiosity = f"The Secret {top_entity} Strat Nobody Uses 🤫"
    else:
        title_curiosity = f"Why Nobody Tells You The Truth About {top_entity}... 😳"
    candidates.append({"id": "curiosity_gap", "framework": "Curiosity Gap", "title": title_curiosity})

    # 2. Pattern Interrupt / Warning Hook
    if niche == "tech_ai":
        title_warning = f"STOP Doing This With {top_entity} (Huge Mistake) ❌"
    elif niche == "gaming":
        title_warning = f"NEVER Do This If You Want To Beat {top_entity} ❌"
    elif top_lower in KNOWN_LOCATIONS or sub_lower in KNOWN_LOCATIONS:
        loc = top_entity if top_lower in KNOWN_LOCATIONS else sub_entity
        title_warning = f"The Biggest Mistake People Make in {loc} ❌"
    else:
        title_warning = f"The Biggest Mistake People Make With {top_entity} ❌"
    candidates.append({"id": "pattern_interrupt", "framework": "Pattern Interrupt", "title": title_warning})

    # 3. Punchy Spoken Quote Hook
    if punchy_quote:
        title_quote = f'"{punchy_quote}" 🤯'
    else:
        title_quote = f"I Couldn't Believe What Happened With {top_entity}..."
    candidates.append({"id": "spoken_quote", "framework": "Spoken Highlight", "title": title_quote})

    # 4. Shocking Realization / Controversy
    if niche == "outdoors_survival":
        if sub_lower in KNOWN_LOCATIONS:
            title_shock = f"Building In {sub_entity}: The {top_entity} Experiment 🏔️"
        elif top_lower in KNOWN_LOCATIONS:
            title_shock = f"Surviving In {top_entity}: What Actually Happened 🏔️"
        else:
            title_shock = f"The Real Truth About {top_entity} That Was Hidden 🤯"
    elif niche == "tech_ai":
        title_shock = f"How {top_entity} Just Changed The Entire Industry Forever ⚡"
    elif niche == "gaming":
        title_shock = f"This Broke Everything We Knew About {top_entity} 🤯"
    else:
        title_shock = f"The Real Truth About {top_entity} That Was Hidden 🤯"
    candidates.append({"id": "shock_revelation", "framework": "Shock / Reveal", "title": title_shock})

    # 5. Actionable Blueprint / High-Value Guide
    if niche == "outdoors_survival":
        if top_lower in KNOWN_LOCATIONS or sub_lower in KNOWN_LOCATIONS:
            loc = top_entity if top_lower in KNOWN_LOCATIONS else sub_entity
            title_action = f"How To Survive In {loc} (Step By Step) 🏕️"
        else:
            title_action = f"How To Master {top_entity} That Actually Works 🏕️"
    elif niche == "gaming":
        title_action = f"How To Master {top_entity} In 30 Seconds 🏆"
    elif niche in {"tech_ai", "business_money"}:
        title_action = f"How To Master {top_entity} Like A Pro In 2026 📈"
    else:
        title_action = f"The Exact Way To Master {top_entity} (Step By Step) 🚀"
    candidates.append({"id": "actionable_blueprint", "framework": "Actionable Guide", "title": title_action})

    return candidates


def score_and_rank_titles_with_jev(
    candidates: List[Dict[str, str]],
    transcript_text: str,
    api_key: Optional[str] = None
) -> Tuple[str, str, float]:
    """
    Submits candidate titles to Jev System One for parallel CTR judgment.
    Returns: (selected_title_id, predicted_niche, confidence)
    """
    if not api_key:
        api_key = get_jev_api_key()

    # If no key or offline, fall back to heuristic evaluation
    if not api_key:
        return candidates[0]["id"], classify_niche_heuristic(transcript_text), 0.75

    choice_criteria = {c["id"]: c["title"] for c in candidates}

    questions = {
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

    payload = {
        "model": "typesafe-ai/jev",
        "state": {
            "transcript_snippet": transcript_text[:1200],
            "title_options": [c["title"] for c in candidates]
        },
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
            best_id = answers.get("best_title_hook", {}).get("choice", candidates[0]["id"])
            niche = answers.get("content_niche", {}).get("choice", classify_niche_heuristic(transcript_text))
            conf = answers.get("best_title_hook", {}).get("confidence", 0.85)
            return best_id, niche, conf
    except Exception:
        pass

    # Resilient fallback: pick first candidate or candidate with highest curiosity heuristic
    return candidates[0]["id"], classify_niche_heuristic(transcript_text), 0.70


def generate_smart_title_and_hashtags(
    transcript_text: str,
    category: str = "high_value_insight",
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    End-to-end engine generating viral title and tailored hashtags for YouTube Shorts,
    TikTok, and Instagram Reels using Jev System One decisions and transcript NLP.
    """
    cleaned = clean_transcript_text(transcript_text)
    if not cleaned:
        default_title = "Viral Moment You Need To See 🤯"
        default_tags = ["#shorts", "#viral", "#trending", "#fyp", "#explore"]
        return {
            "suggested_title": default_title,
            "suggested_hashtags": default_tags,
            "niche": "general_viral",
            "candidates": [{"id": "default", "framework": "Default", "title": default_title}],
            "platform_metadata": {
                "youtube": {"title": f"{default_title} #shorts", "description": f"{default_title}\n\n{' '.join(default_tags)}", "tags": [t.replace("#", "") for t in default_tags]},
                "tiktok": {"caption": f"{default_title} {' '.join(default_tags)}"},
                "instagram": {"caption": f"{default_title}\n.\n.\n{' '.join(default_tags)}"}
            }
        }

    # 1. Generate 5 diverse candidates
    candidates = generate_candidate_titles(cleaned, category)

    # 2. Evaluate with Jev
    best_id, niche, conf = score_and_rank_titles_with_jev(candidates, cleaned, api_key)

    # Find the winning candidate title
    winning_cand = next((c for c in candidates if c["id"] == best_id), candidates[0])
    best_title = winning_cand["title"]

    # 3. Generate tailored hashtags
    niche_tags = NICHE_HASHTAGS.get(niche, NICHE_HASHTAGS["general_viral"])
    entities = extract_topical_entities(cleaned)
    
    entity_tags = []
    for ent, _ in entities[:3]:
        clean_tag = re.sub(r"[^a-zA-Z0-9]", "", ent).lower()
        if clean_tag and len(clean_tag) > 2 and f"#{clean_tag}" not in niche_tags:
            entity_tags.append(f"#{clean_tag}")

    # Combine: 2-3 niche tags + 2 entity tags + #shorts #viral
    combined_tags = []
    # Always include top niche tags
    for t in niche_tags[:3]:
        if t not in combined_tags:
            combined_tags.append(t)
    # Add entity tags
    for t in entity_tags:
        if t not in combined_tags:
            combined_tags.append(t)
    # Ensure #shorts and #viral
    for base_tag in ["#shorts", "#viral", "#fyp"]:
        if base_tag not in combined_tags and len(combined_tags) < 7:
            combined_tags.append(base_tag)

    final_hashtags = combined_tags[:7]

    # 4. Compile platform-specific metadata packages
    yt_title = f"{best_title} #shorts" if "#shorts" not in best_title else best_title
    yt_desc = (
        f"{best_title}\n\n"
        f"\"{cleaned[:180]}...\"\n\n"
        f"Subscribe for more daily shorts!\n\n"
        f"{' '.join(final_hashtags)}"
    )
    raw_tags = [t.lstrip("#") for t in final_hashtags]

    tiktok_caption = f"{best_title}\n\n{' '.join(final_hashtags)}"
    ig_caption = f"{best_title}\n.\n.\n{' '.join(final_hashtags)}"

    return {
        "suggested_title": best_title,
        "suggested_hashtags": final_hashtags,
        "niche": niche,
        "confidence": conf,
        "candidates": candidates,
        "winning_framework": winning_cand["framework"],
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
