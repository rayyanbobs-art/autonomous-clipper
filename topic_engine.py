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
