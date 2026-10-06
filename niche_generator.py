import os
import json
import re
import urllib.request
from typing import Dict, List, Any, Optional

def generate_viral_ideas(
    channel_name: str,
    niche: str,
    outlier_titles: List[str],
    hook_framework: str = "Curiosity Gap",
    pacing_wpm: int = 150,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Generate 10 high-CTR video topics, title variations, thumbnail concepts,
    and opening hook scripts. Supports Google Gemini API (free tier),
    local Ollama, or intelligent built-in template synthesis.
    """
    api_key = api_key or os.environ.get("GEMINI_API_KEY")

    # Strategy 1: Google Gemini Free Tier if key is provided
    if api_key:
        ideas = _generate_with_gemini(channel_name, niche, outlier_titles, hook_framework, pacing_wpm, api_key)
        if ideas:
            return ideas

    # Strategy 2: Local Ollama if running
    ollama_ideas = _generate_with_ollama(channel_name, niche, outlier_titles, hook_framework, pacing_wpm)
    if ollama_ideas:
        return ollama_ideas

    # Strategy 3: Built-in Heuristic Synthesizer (100% offline, zero keys needed)
    return _generate_with_templates(channel_name, niche, outlier_titles, hook_framework, pacing_wpm)

def _generate_with_gemini(
    channel_name: str,
    niche: str,
    outlier_titles: List[str],
    hook_framework: str,
    pacing_wpm: int,
    api_key: str
) -> Optional[List[Dict[str, Any]]]:
    prompt = f"""You are an elite YouTube Strategist who models outlier video formulas for faceless YouTube channels.
Target Channel: {channel_name}
Target Niche / Topic: {niche}
Observed Outlier Titles from this channel:
{json.dumps(outlier_titles[:6], indent=2)}

Dominant Hook Framework: {hook_framework}
Pacing: {pacing_wpm} words/minute

Task: Generate exactly 8 distinct viral video concepts modeled after the exact psychological hooks and syntax of these outlier videos.
Return ONLY valid JSON matching this exact array structure:
[
  {{
    "title": "High-CTR Title modeled after outlier syntax",
    "thumbnail_concept": "Detailed description of the visual hook, text elements, and emotional contrast in the thumbnail",
    "hook_script": "The exact word-for-word 45-second opening narration script engineered to maximize retention",
    "outlier_rationale": "Why this specific topic and angle is primed to beat channel baseline views"
  }}
]
"""
    # Gemini 1.5 Flash endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json"
        }
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode('utf-8'))
            text = result['candidates'][0]['content']['parts'][0]['text']
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list):
                        return val
            return parsed if isinstance(parsed, list) else None
    except Exception as e:
        print(f"Gemini API generation error: {e}")
        return None

def _generate_with_ollama(
    channel_name: str,
    niche: str,
    outlier_titles: List[str],
    hook_framework: str,
    pacing_wpm: int
) -> Optional[List[Dict[str, Any]]]:
    url = "http://localhost:11434/api/generate"
    prompt = f"""You are an elite YouTube strategist. Generate 6 viral video ideas for channel '{channel_name}' in niche '{niche}'.
Outlier titles: {json.dumps(outlier_titles[:4])}.
Return JSON array of objects with keys: title, thumbnail_concept, hook_script, outlier_rationale."""
    
    payload = {
        "model": "llama3",
        "prompt": prompt,
        "format": "json",
        "stream": False
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode('utf-8'))
            parsed = json.loads(res.get('response', '[]'))
            if isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list):
                        return val
            return parsed if isinstance(parsed, list) else None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Offline "Topic Cloner" synthesizer.
#
# The previous implementation accepted channel_name / niche / outlier_titles /
# hook_framework / pacing_wpm and then ignored all of them, returning the same 8
# hard-coded strings with only the topic substituted. The engine below actually
# derives its output from the observed outlier titles: it detects which title
# *frames* the channel over-uses, mines the subjects those titles talk about, and
# synthesizes fresh concepts in the winning frames.
# ---------------------------------------------------------------------------

_FRAME_STOPWORDS = {
    # Function words / pronouns / determiners
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "being", "am",
    "it", "its", "this", "that", "these", "those", "you", "your", "yours", "i", "me",
    "my", "mine", "we", "us", "our", "ours", "they", "them", "their", "theirs", "he",
    "him", "his", "she", "her", "hers", "he'd", "she'd", "they'd", "we'd", "you'd",
    "i'd", "it'd", "he'll", "she'll", "they'll", "we'll", "you'll", "i'll", "it'll",
    "he's", "she's", "they're", "we're", "you're", "i'm", "it's", "let's",
    "not", "no", "so", "if", "then", "than", "just", "now", "here", "there",
    "can", "will", "would", "should", "could", "may", "might", "must", "do", "does",
    "did", "done", "doing", "have", "has", "had", "having",
    # Question / clause scaffolding
    "why", "how", "what", "when", "where", "who", "which", "whose", "whom",
    "about", "into", "onto", "over", "under", "through", "during", "before", "after",
    "without", "within", "across", "against", "between", "among", "because", "since",
    # Quantifiers / degree
    "every", "everyone", "everybody", "anyone", "anybody", "somebody", "nobody",
    "someone", "something", "no", "one", "two", "three", "most", "many", "much",
    "more", "less", "least", "all", "some", "any", "each", "both", "few", "very",
    # Adverbs / intensifiers
    "always", "never", "often", "sometimes", "already", "still", "even", "also",
    "actually", "basically", "literally", "really", "truly", "honestly", "probably",
    "maybe", "certainly", "definitely", "simply", "just", "very", "quite", "rather",
    "almost", "anyway", "however", "instead", "again", "back", "down", "out", "off",
    "way", "ways",
    # Verbs
    "get", "gets", "got", "getting", "go", "goes", "going", "went", "gone",
    "make", "makes", "made", "making", "take", "takes", "taking", "took", "taken",
    "come", "comes", "coming", "came", "know", "knows", "knew", "known", "knowing",
    "think", "thinks", "thought", "want", "wants", "wanted", "need", "needs", "needed",
    "use", "uses", "used", "using", "work", "works", "worked", "working",
    "try", "tries", "tried", "trying", "start", "starts", "started", "starting",
    "turn", "turns", "turned", "put", "puts", "putting", "keep", "keeps", "kept",
    "keeping", "let", "lets", "help", "helps", "helped", "become", "becomes",
    "became", "show", "shows", "showed", "see", "sees", "saw", "seen",
    "look", "looks", "looked", "say", "says", "said", "find", "finds", "found",
    "leave", "leaves", "left", "tell", "tells", "told", "ask", "asks", "asked",
    "seem", "seems", "seemed", "feel", "feels", "felt", "happen", "happens",
    "happened", "warn", "warns", "warned", "warning", "ruin", "ruins", "ruined",
    "fix", "fixes", "fixed", "fixing", "stop", "stops", "stopped", "stopping",
    "change", "changes", "changed", "changing", "master", "masters", "mastered",
    "mistake", "mistakes", "mistaken", "teach", "teaches", "taught",
    "warn", "explain", "explains", "explained", "understand", "understands",
    "understood", "appreciate", "appreciates", "appreciated", "waste", "wastes",
    "wasted", "destroy", "destroys", "destroyed", "lose", "loses", "lost",
    "win", "wins", "won", "give", "gives", "gave", "given", "pay", "pays", "paid",
    "buy", "buys", "bought", "sell", "sells", "sold", "build", "builds", "built",
    "break", "breaks", "broke", "broken", "rise", "rises", "rose", "risen",
    "fall", "falls", "fell", "fallen", "increase", "increases", "increased",
    "reduce", "reduces", "reduced", "avoid", "avoids", "avoided",
    "nothing", "something", "everything", "anything",
    # Adjectives / generic descriptors used as clickbait scaffolding
    "big", "small", "huge", "tiny", "great", "good", "bad", "best", "worst",
    "right", "wrong", "real", "true", "false", "full", "empty", "new", "old",
    "young", "fast", "slow", "easy", "hard", "long", "short", "high", "low",
    "own", "same", "different", "other", "another", "first", "second", "third",
    "last", "next", "better", "best", "worse", "worst", "simple", "simplest",
    "hidden", "secret", "truth", "myth", "myths", "facts", "lesson", "lessons",
    "thing", "things", "stuff", "problem", "problems", "issue", "issues",
    "reason", "reasons", "tips", "tricks", "guide", "guides", "tips",
    "time", "times", "day", "days", "week", "weeks", "year", "years",
    "month", "months", "minute", "minutes", "second", "seconds", "part", "parts",
    "actually", "upcoming", "officially", "finally", "simply", "just",
    "video", "videos", "channel", "content", "creator", "audience",
    "guy", "guys", "gal", "gals", "dude", "dudes", "folks", "people", "person",
    # Verbs / participles that commonly appear in title scaffolding
    "survive", "survives", "survived", "surviving", "spend", "spends", "spent",
    "spending", "mention", "mentions", "mentioned", "mentioning", "blow", "blows",
    "blew", "blown", "buy", "buys", "bought", "buying", "know", "knew", "wish",
    "wished", "alone", "cheap", "cheaper", "best", "cheaply", "happen", "happened",
    "learn", "learns", "learned", "learnt", "teaches", "taught", "told", "say",
    "says", "said", "prove", "proves", "proved", "proven", "follow", "follows",
    "followed", "calling", "called", "give", "gives", "gave", "take", "took"
}

# Extra scaffolding words that survive token-shape filtering but still read badly as a
# subject when mined out of a title.
_SUBJECT_BLOCKLIST = {
    "mentions", "survive", "spent", "alone", "bought", "blew", "knew", "wish",
    "spoke", "said", "makes", "made", "wants", "needs", "lets", "helps", "works",
    "happens", "happened", "exists", "exist", "means", "meant", "gives", "takes"
}

# Ordered title "frames" (syntactic shapes) with the regex used to detect them
# on an observed outlier title. Order matters only for tie-breaking.
TITLE_FRAMES = [
    {
        "id": "why_belief",
        "detect": re.compile(r"^\s*why\b", re.I),
        "title": "Why Almost Everyone Gets {subject} Wrong",
        "opening": "There's a belief about {subject_lower} that nearly everybody holds, and nearly everybody has it backwards.",
        "thumb": "Split frame: a confident claim on the left crossed out, the corrected version stamped on the right. Big yellow word: 'WRONG'.",
        "rationale": "Belief-reversal is the highest-CTR shape the channel's outliers already use; mirroring it with a new subject keeps the proven curiosity contract intact.",
    },
    {
        "id": "how_to",
        "detect": re.compile(r"^\s*how (to|i|we)\b", re.I),
        "title": "How To Master {subject} In {minutes} Minutes",
        "opening": "If you want to get genuinely good at {subject_lower}, you don't need more time — you need fewer, better steps.",
        "thumb": "Numbered step cards fanned out over a blurred {subject} background. Accent bar filling to 100%.",
        "rationale": "Utility framing matches the instructional outliers and converts directly into a concrete deliverable viewers can act on.",
    },
    {
        "id": "number_list",
        "detect": re.compile(r"^\s*(\d+|#\d+)\b", re.I),
        "title": "{count} Surprises In {subject} That Changed How I Think",
        "opening": "I didn't go looking for these. They just showed up one after another, and by the end I couldn't unsee them.",
        "thumb": "Grid of small tiles each holding one bold number, with the biggest number rendered enormous in the centre.",
        "rationale": "The list frame is a proven retention structure: it sets a clear finish line, which keeps a cold audience watching to the end.",
    },
    {
        "id": "i_tried",
        "detect": re.compile(r"^\s*(i|we)\b.*\b(tried|tested|spent|quit|stopped|did)\b", re.I),
        "title": "I Tried {subject} For {days} Days. Here's What Happened.",
        "opening": "I gave {subject_lower} a fair shot — {days} full days of it, no skipping and no easy mode.",
        "thumb": "Day counter overlaid on a real first-person frame, slightly desaturated, with a hand-written date stamp.",
        "rationale": "First-person stakes convert abstract topics into a story with an outcome, matching the channel's personal-experiment outliers.",
    },
    {
        "id": "mistake",
        "detect": re.compile(r"\b(mistake|wrong|regret|avoid|stop|don'?t|never)\b", re.I),
        "title": "The Mistake Everyone Makes With {subject}",
        "opening": "Nearly everyone gets {subject_lower} wrong in exactly the same way, and it's the one mistake that costs the most.",
        "thumb": "A single circled detail in sharp focus while everything around it blurs out. Red annotation arrow, label: 'THIS'.",
        "rationale": "Mistake-framing activates loss aversion — the audience stays to avoid the error rather than to gain the tip.",
    },
    {
        "id": "contrarian",
        "detect": re.compile(r"^\s*(stop|quit|unpopular|nobody|no one|everyone)\b", re.I),
        "title": "Stop Doing {subject}. Do This Instead.",
        "opening": "I need to say something about {subject_lower} that will probably get me some pushback, but it's the honest answer.",
        "thumb": "Two panels: a red 'stop' symbol over the old approach, a green check over the new one. High-contrast split.",
        "rationale": "Direct opposition to the consensus scroll-stop, mirroring the contrarian outliers that over-index on this channel.",
    },
    {
        "id": "hidden_truth",
        "detect": re.compile(r"\b(secret|truth|hidden|real|actually|nobody knows|they don'?t)\b", re.I),
        "title": "The Truth About {subject} Nobody Mentions",
        "opening": "The version of {subject_lower} you're usually told is the clean one. The true one is messier, and far more interesting.",
        "thumb": "A half-peeled poster or document with a second, contradictory layer revealed underneath. Label: 'THE OTHER HALF'.",
        "rationale": "Hidden-knowledge framing mirrors the channel's strongest curiosity outliers without copying their subject matter.",
    },
    {
        "id": "story",
        "detect": re.compile(r"^\s*(what|how|when)\b.*\b(happened|told|taught|changed|story)\b", re.I),
        "title": "What {subject} Actually Taught Me",
        "opening": "I want to tell you properly what happened with {subject_lower}, because the short version leaves out the part that mattered.",
        "thumb": "Single cinematic still with a soft vignette, subject centred, a thin line of type across the lower third.",
        "rationale": "Narrative framing suits channels whose outliers lean on story beats and rewards a longer, more atmospheric edit.",
    },
    {
        "id": "versus",
        "detect": re.compile(r"\b(vs\.?|versus|compared?|or)\b", re.I),
        "title": "{subject} vs The Obvious Choice: Which Actually Wins?",
        "opening": "Everyone defaults to the same answer for {subject_lower}. I don't think the default is right.",
        "thumb": "Face-off composition, two subjects flanking a central dividing line, muted colour grade on the challenger side.",
        "rationale": "Comparison framing gives the viewer an explicit decision to make, which reliably lifts comment and completion rates.",
    },
    {
        "id": "warning",
        "detect": re.compile(r"^\s*(warning|alert|urgent|attention)\b", re.I),
        "title": "Warning: {subject} Is About To Change Everything",
        "opening": "Something is shifting underneath {subject_lower} right now, and the people documenting it first are already ahead.",
        "thumb": "High-saturation warning gradient with a single bold word in heavy type, subject imagery pushed behind it.",
        "rationale": "Urgency framing matches the breaking-news outliers and works best when published close to a real change in the space.",
    },
]

# Default frames used when no outlier titles are available to learn from.
_DEFAULT_FRAME_IDS = [
    "why_belief", "how_to", "number_list", "mistake",
    "contrarian", "hidden_truth", "i_tried", "story",
]

_HOOK_OPENERS = {
    "curiosity gap": "Before I explain any of this, you should know what you're actually looking at.",
    "pattern interrupt": "Stop scrolling for thirty seconds. This one is different.",
    "shock / reveal": "I did not expect any of this to go the way it did.",
    "shocking reveal": "I did not expect any of this to go the way it did.",
    "shock / controversy": "I'm going to say the thing most people won't say about this.",
    "open loop": "The ending is the part that matters, so hold on to that thought.",
    "bold claim": "Here's the part nobody in this space wants to say out loud.",
    "story": "This happened to me, and it took me far too long to understand why.",
}

_SUPPORT_BEATS = [
    "Here's the part that surprised me: the same principle held whether the scale was small or large.",
    "Once you see it, you can't stop seeing it — it shows up everywhere, including in places that should have nothing to do with it.",
    "That's the moment the whole thing clicked, and everything after that was just detail.",
    "And the interesting part is that the people who look like they have the biggest advantage aren't the ones who know the most.",
    "The second time it happened I recognised it instantly, which is when I knew it was a real pattern and not a coincidence.",
    "What changed my mind wasn't a single fact. It was watching it hold up again, and again, in a completely different context.",
    "So the useful part isn't the conclusion — it's knowing what to actually watch for.",
    "If you take one thing away, make it this: the obvious explanation is the expensive one."
]


def _normalize_titles(outlier_titles: Any) -> List[str]:
    """Extracts a clean, de-duplicated, bounded list of usable title strings."""
    out: List[str] = []
    seen = set()
    if not isinstance(outlier_titles, (list, tuple)):
        return out
    for raw in outlier_titles:
        if not isinstance(raw, str):
            continue
        t = re.sub(r"\s+", " ", raw).strip()
        if len(t) < 6 or len(t) > 180:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _strip_scaffolding(title: str) -> str:
    """
    Removes the clickbait scaffolding from a title so that only its subject matter remains.

    Without this step the miner treats words like "mistake", "everyone" and "warns" as the
    topic, producing titles such as "The Mistake Everyone Makes With Everyone Makes".
    """
    s = title
    s = re.sub(r"^\s*[\[\(].*?[\]\)]\s*", " ", s)               # [Music] / (Part 2)
    s = re.sub(r"^\s*(?:the|a|an)\s+", " ", s, flags=re.I)
    s = re.sub(r"^\s*\d+\s+", " ", s)                            # "10 Things ..."
    s = re.sub(r"[!?]+$", " ", s)
    s = re.sub(r"[:|\u2013\u2014-]\s.*$", " ", s)               # "Topic: the rest"
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)
    s = re.sub(r"[^A-Za-z0-9'\- ]+", " ", s)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", s)]
    kept = [w for w in words if w.lower() not in _FRAME_STOPWORDS and len(w) > 1]
    return " ".join(kept)


def _is_usable_subject_token(token: str) -> bool:
    """
    Rejects tokens that read badly as a standalone subject even though they survived the
    stopword list: adverbs, comparatives, superlatives and gerunds.
    """
    lw = token.lower()
    if len(lw) < 3 or lw in _FRAME_STOPWORDS or lw in _SUBJECT_BLOCKLIST or lw.isdigit():
        return False
    if len(lw) > 4 and lw.endswith("est"):          # superlative: cheapest, biggest
        return False
    if len(lw) > 4 and lw.endswith("ly"):           # adverb: really, literally
        return False
    if len(lw) > 3 and lw.endswith("ing") and lw not in {"scaling", "training", "farming"}:
        return False                                # gerund: buying, ruining, warning
    return True


def _mine_subjects(titles: List[str], fallback: str) -> List[str]:
    """
    Mines the subject matter the outlier titles actually talk about, returning short
    noun phrases ranked by salience. Falls back to the supplied topic when nothing
    recoverable survives, because a coherent supplied topic always reads better than an
    invented phrase.
    """
    if not titles:
        return [fallback] if fallback else []

    freq: Dict[str, int] = {}
    display: Dict[str, str] = {}

    for t in titles:
        residual = _strip_scaffolding(t)
        words = residual.split()
        for i, w in enumerate(words):
            if not _is_usable_subject_token(w):
                continue
            lw = w.lower()
            freq[lw] = freq.get(lw, 0) + 1
            display.setdefault(lw, w)
            # Two adjacent surviving words form a compound subject (e.g. "laptop fans").
            if i + 1 < len(words):
                nw = words[i + 1].lower()
                if len(nw) >= 3 and nw not in _FRAME_STOPWORDS and nw not in _SUBJECT_BLOCKLIST and not nw.isdigit():
                    pair = f"{lw} {nw}"
                    freq[pair] = freq.get(pair, 0) + 1
                    display.setdefault(pair, f"{w} {words[i + 1]}")

    if not freq:
        return [fallback] if fallback else []

    # Prefer frequent phrases, then single words (they read cleanly in every frame template).
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], " " in kv[0], -len(kv[0]), kv[0]))

    subjects: List[str] = []
    for key, _score in ranked:
        label = display.get(key, key).strip()
        if not label:
            continue
        # Skip candidates already covered by a selected subject (e.g. "fans" after "laptop fans").
        low = label.lower()
        if any(low in s.lower() or s.lower() in low for s in subjects):
            continue
        subjects.append(label)
        if len(subjects) >= 6:
            break

    # If nothing trustworthy survived (all-stopword titles, junk input), use the caller's topic.
    return subjects or ([fallback] if fallback else [])


def _detect_frames(titles: List[str]) -> List[str]:
    """Returns the frame IDs used by the outlier titles, most frequent first."""
    counts: Dict[str, int] = {}
    for t in titles:
        for frame in TITLE_FRAMES:
            if frame["detect"].search(t):
                counts[frame["id"]] = counts.get(frame["id"], 0) + 1
    if not counts:
        return list(_DEFAULT_FRAME_IDS)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [fid for fid, _ in ranked]


def _target_hook_seconds(pacing_wpm: int) -> int:
    """Maps words-per-minute onto a spoken hook length, clamped to a sane 20-70s window."""
    try:
        wpm = float(pacing_wpm)
    except (TypeError, ValueError):
        wpm = 150.0
    wpm = min(max(wpm, 50.0), 300.0)
    # A punchy cold-open runs roughly 0.55x the "comfortable" rate before the hook needs to land.
    seconds = (wpm * 35.0 / 60.0) / 0.55
    return int(min(max(seconds, 20.0), 70.0))


def _build_hook_script(frame: Dict[str, Any], subject: str, hook_framework: str,
                       pacing_wpm: int, variant: int) -> str:
    """
    Assembles a word-count-targeted opening narration for the chosen frame, using the
    dominant hook framework and the requested pacing.
    """
    subject_lower = subject.lower()
    target_seconds = _target_hook_seconds(pacing_wpm)
    target_words = max(30, int(target_seconds / 60.0 * float(pacing_wpm)))

    opener = _HOOK_OPENERS.get((hook_framework or "").strip().lower())
    if not opener:
        opener = _HOOK_OPENERS["curiosity gap"]
    opener = opener.replace("{subject_lower}", subject_lower)

    parts = [opener, frame["opening"].replace("{subject_lower}", subject_lower)]

    # Deterministic rotation through the support beats so successive ideas differ.
    offset = (variant * 3) % len(_SUPPORT_BEATS)
    for i in range(len(_SUPPORT_BEATS)):
        parts.append(_SUPPORT_BEATS[(offset + i) % len(_SUPPORT_BEATS)])
        if len(" ".join(parts).split()) >= target_words:
            break

    script = " ".join(parts)

    # Trim to the target word budget at a sentence boundary where possible.
    words = script.split()
    if len(words) > target_words:
        trimmed = " ".join(words[:target_words])
        cut = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
        script = (trimmed[:cut + 1] if cut > target_words * 0.6 else trimmed).strip()

    return script


def _generate_with_templates(
    channel_name: str,
    niche: str,
    outlier_titles: List[str],
    hook_framework: str = "Curiosity Gap",
    pacing_wpm: int = 150
) -> List[Dict[str, Any]]:
    """
    Offline synthesis that actually models the observed outliers: it learns which title
    frames the channel over-uses, mines the subjects those titles discuss, and generates
    fresh concepts inside the winning frames at the requested pacing.
    """
    topic_kw = niche if niche else (channel_name if channel_name else "This Topic")
    titles = _normalize_titles(outlier_titles)
    subjects = _mine_subjects(titles, topic_kw)
    frame_ids = _detect_frames(titles)

    frames_by_id = {f["id"]: f for f in TITLE_FRAMES}
    # Build the working frame list: learned frames first, then defaults to fill out the set.
    ordered_ids = frame_ids + [fid for fid in _DEFAULT_FRAME_IDS if fid not in frame_ids]
    ordered_frames = [frames_by_id[fid] for fid in ordered_ids]

    learned = set(frame_ids)

    minutes_pool = [10, 15, 20, 30]
    days_pool = [7, 14, 30]

    results: List[Dict[str, Any]] = []
    seen_titles = set()
    for i, frame in enumerate(ordered_frames):
        subject = subjects[i % len(subjects)] if subjects else topic_kw

        title = frame["title"].format(
            subject=subject,
            minutes=minutes_pool[i % len(minutes_pool)],
            count=(i % 3) + 5,
            days=days_pool[i % len(days_pool)]
        )

        # Guarantee output variety even when the same subject recurs.
        guard = 2
        base_title = title
        while title.lower() in seen_titles and guard < 4:
            title = f"{base_title} ({['Part 2', 'The Full Story', 'And Why'][guard - 2]})"
            guard += 1
        seen_titles.add(title.lower())

        if frame["id"] in learned:
            rationale = frame["rationale"]
        else:
            rationale = (
                f"Extension frame, not yet proven on this channel: used to test whether "
                f"{hook_framework or 'the current'} framing can carry a second concept "
                f"about {subject} without repeating an existing outlier's exact angle."
            )

        results.append({
            "title": title,
            "thumbnail_concept": frame["thumb"].replace("{subject}", subject),
            "hook_script": _build_hook_script(frame, subject, hook_framework, pacing_wpm, i),
            "outlier_rationale": rationale
        })
        if len(results) >= 8:
            break

    return results
