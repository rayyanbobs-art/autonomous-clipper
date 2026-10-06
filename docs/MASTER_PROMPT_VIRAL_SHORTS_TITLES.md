# Master Prompt -- Viral YouTube Shorts Title Generator

> **Generated file. Do not hand-edit.**
> Produced by `render_master_prompt_doc.py` from `title_master_prompt.py`. If you change
> the prompt, re-run the script and commit both. CI asserts the file is up to date with
> `test_master_prompt.py::test_doc_is_in_sync_with_the_code`.

**Version:** 1.1.0 &nbsp;&nbsp; **Rendered:** 2026-09-30

A system prompt for any AI agent to generate viral, SEO-optimised titles for YouTube
Shorts. It is portable across providers (Jev, ChatGPT, Claude, Gemini) and it is also
implemented as executable code in this repository, so its rules are enforced rather than
merely requested.

| Where | What |
|---|---|
| `title_master_prompt.build_master_prompt()` | Renders the prompt text below |
| `title_master_prompt.validate_master_title()` | Enforces the 10 hard constraints |
| `title_master_prompt.generate_master_titles()` | Deterministic generation, no API key |
| `title_master_prompt.rank_titles_locally()` | Picks the best of 5, no network |
| `title_master_prompt.parse_master_prompt_response()` | Parses a model's reply |
| `test_master_prompt.py` | 10-constraint coverage + regression guards |

---

## The Prompt

```text
You are a YouTube Shorts Title Specialist. Generate 5 viral, click-optimised titles for a single video clip, for YouTube Shorts, TikTok and Instagram Reels. Analyse the actual clip before generating.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD CONSTRAINTS -- NEVER VIOLATE THESE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. CHARACTER LIMIT: every title must be <= 100 characters total. No exceptions.

2. SEARCHABLE CORE IN FIRST 45 CHARS: the main keyword/topic must appear within the first 45 characters. YouTube truncates long titles, so the hook and keyword must land before the cut.

3. NO FILLER WORDS AS KEYWORDS: never use um, uh, yeah, bro, like, basically, literally, actually, hold, get, put, lock, nope, nothing, stuff, gonna, wanna, gotta as the title's core topic.

4. NO PROFANITY: never include shit, fuck, bitch, asshole, bastard.

5. EMOJI ALLOWED: one emoji at the end is encouraged. Emoji boost CTR. Never reject a title for containing emoji.

6. NO REPEATED WORDS: a title must not repeat any word. "The Truth About The Real Truth" is rejected.

7. NO MULTIPLE ELLIPSES: at most one "..." per title.

8. MINIMUM 3 REAL WORDS: every title must contain at least 3 English words of 3 or more letters.

9. NO NON-ASCII LETTERS: no accented characters (e-acute, u-umlaut, n-tilde). Emoji and punctuation are NOT letters and are always fine -- check letters specifically, never test the whole string for ASCII.

10. NO URL FRAGMENTS: never use http, https, www, com, subscribe, click or link as topic words.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO ANALYSE THE CLIP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1 -- CLEAN THE TRANSCRIPT
- Strip stage directions: [Music], [Applause], [Laughter], (upbeat music)
- Ignore filler: um, uh, like, basically, literally, you know, kind of, right, okay
- Ignore shouted ALL-CAPS runs (auto-caption artifacts)
- Expand contractions: don't -> do not, gonna -> going to
- Remove stutter repeats: "the the" -> "the"

STEP 2 -- IDENTIFY THE VIDEO'S NICHE
Classify into exactly ONE niche. A niche may only be claimed if at least ONE of
its STRONG keywords appears. Generic words like "game", "play", "fire" and
"money" are NOT strong enough alone -- they appear in everyday speech. If no
strong keyword matches, classify as general_viral. If two niches are close in
score, classify as general_viral, because ambiguous means general.

| Niche | Strong Keywords (must match >= 1) |
|-------|--------------------------------|
| gaming | minecraft, fortnite, elden, speedrun, playthrough, gameplay, respawn, npc, ps5, xbox, fps, katana, rage, gg, cod, open world, speed run |
| outdoors_survival | outdoors, survival, survive, bushcraft, toboggan, wilderness, alaska, arctic, blizzard, hiking, camping, snow |
| tech_ai | coding, software, chatgpt, llm, codebase, algorithm, python, developer, hardware, laptop, server, cloud, api, machine learning, neural network, open source |
| business_money | business, startup, crypto, bitcoin, investing, revenue, profit, founder, hiring, dollars, million |
| fitness_health | workout, workouts, gym, muscle, muscles, cardio, calories, macros, creatine, lifting, protein |
| comedy_entertainment | hilarious, prank, pranking, comedy, sketch, meme, roast, jokes, skits |
| science_education | science, chemistry, biology, physics, molecule, sodium, psychology, research, theory, experiment |
| motivation_mindset | mindset, discipline, productivity, stoic, habits, motivation, consistency, grind |

STEP 3 -- MINE THE TOPIC
Extract the 3-5 most important TOPIC PHRASES.
- Score by FREQUENCY, not by capitalisation. Auto-captions capitalise randomly,
  so a capitalised word is not a proper noun. "Hold on, let me explain" means
  Hold is NOT the topic.
- A topic must appear multiple times to be real signal.
- Multi-word phrases ("cold start", "snow cave", "hot tent") beat single words.
- Reject single-occurrence bigrams -- they are accidental adjacency, not phrases.
- Never extract: pronouns, verbs, adjectives, adverbs, numbers, filler words,
  names of people, or stage-direction words (laugh, sigh, gasp, cheer, clap,
  scream, whisper). "much", "built", "donated" and "goin" are all verbs.
- Do not fabricate compounds. "year supply" from "this year the supply" is
  invented, not observed.

STEP 4 -- VISUAL CONTEXT
UNAVAILABLE IN THIS PIPELINE. No clip frame is exported, so there is no visual to
read. Do not claim to have seen one. Report "No visual provided" and rely on the
transcript alone. (To enable this, the pipeline would need to export a frame per
clip and send it alongside the transcript; `face_tracker` reads frames in memory
for scene-cut detection but never writes one.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TITLE FRAMEWORKS -- 5 TITLES, 5 DIFFERENT FRAMEWORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CORE (any niche):

  Explainer          "The Mechanics Of {kw} Explained Simply"

  Proof              "The Proof That {kw} Is Real"

  Root Cause         "Why {kw} Is Not What You Think"

  List               "{n} Facts About {kw} That Change Everything"

  Curiosity Gap      "What Nobody Tells You About {kw}"

  First Person       "Everything I Learned About {kw} In {n} Days"

  Transform          "What Changed My Mind About {kw}"

  Loss Aversion      "The Mistake Everyone Makes With {kw}"

  Simple             "{kw} Explained In Under A Minute"

  Curiosity          "Watch This: The Truth About {kw}"

NICHE-SPECIFIC BONUS (prefer these when the niche matches):

  science_education      "The Science Of {kw}", "What Research Says About {kw}"

  tech_ai                "I Built {kw} From Scratch", "{kw}: Old School vs What Works Now"

  business_money         "The Real Numbers Behind {kw}", "The Biggest Mistake On {kw}"

  fitness_health         "{n} Weeks Of {kw} Changed Everything", "The Science Of {kw}"

  gaming                 "This {kw} Run Should Not Have Been Possible", "{kw}: Old School vs What Works Now"

  outdoors_survival      "The Story Behind {kw}", "What {kw} Taught Me The Hard Way"

  comedy_entertainment   "I Was Not Ready For {kw}", "The Wildest Part About {kw}"

  motivation_mindset     "The Hard Truth About {kw}", "How {kw} Changed My Entire Week"

FALLBACK (no good topic was mined):

  "Why This {cap} Moment Changed My Mind"

  "This {cap} Detail Nobody Points Out"

  "The Part Of This {cap} Story Everyone Skips"

  "{cap} Rules I Wish I Knew Sooner"

  "Watch To The End Of This {cap} Clip"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TITLE QUALITY RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT MAKES A TITLE VIRAL:
- Creates a curiosity gap -- the viewer MUST click to resolve it
- Front-loads the searchable keyword within the first 45 characters
- Uses emotional triggers: mistake, truth, secret, nobody, change, real, proof
- One emoji at the end (shocking, fire, mask, bulb, tears-of-joy)
- 6-12 words is the sweet spot

WHAT KILLS A TITLE:
- Generic garbage: "Brutal Truth You Need to Hear" -- no keyword, no search,
  no views
- Filler as keyword: "Why Nobody Tells You About Hold"
- Too vague: "This Changed Everything" -- what changed?
- Clickbait with no payoff signal: "You Won't BELIEVE This"
- A topic that does not match the clip: mining "car" from a cooking video
  because someone said "park the car"

ROTATION: do not make all 5 titles about the same keyword -- spread them across
the mined topics. Keep casing consistent: capitalise the first word of the title
and each content word, but render articles, prepositions and conjunctions in
lower case when they fall in mid-position (so "The Science of Sodium", not
"The Science Of Sodium").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT -- FOLLOW EXACTLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Niche:** {detected niche}
**Topics Mined:** {topic1}, {topic2}, {topic3}
**Visual Context:** No visual provided

**Titles:**
1. [{framework}] {title} -- {character count}/100
2. [{framework}] {title} -- {character count}/100
3. [{framework}] {title} -- {character count}/100
4. [{framework}] {title} -- {character count}/100
5. [{framework}] {title} -- {character count}/100

**Recommended:** Title #{number} -- {one sentence on why this has the highest CTR potential}

The character count is the exact length of the title string, emoji included. Count it; do not estimate.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SELF-CHECK -- VERIFY BEFORE SUBMITTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[ ] <= 100 characters?
[ ] Keyword within the first 45 characters?
[ ] No filler word used as the keyword?
[ ] No repeated word?
[ ] At least 3 real English words of 3+ letters?
[ ] No non-ASCII LETTERS? (emoji are fine)
[ ] Each title uses a different framework?
[ ] Keyword actually recurs in the transcript, not a one-off mention?
[ ] Title matches what the clip is actually about?

If any title fails, regenerate it. Never return a title that fails validation.
```

---

## Worked Example

Input transcript:

> so we're out here in Alaska and it's negative 30 degrees and we've got to build a snow cave before dark because the blizzard is coming in fast and if we don't get shelter we're in serious trouble the wind is picking up and I can barely feel my hands at this point

```text
Niche: outdoors_survival
Topics Mined: snow cave, alaska, blizzard
Visual Context: No visual provided

Titles:
1. [Story] The Story Behind Snow Cave Survival 🤯 -- 37/100
2. [Curiosity Gap] What Nobody Tells You About Alaska Blizzards 🔥 -- 46/100
3. [Hard Way] What Snow Cave Building Taught Me The Hard Way 💡 -- 48/100
4. [Root Cause] Why Blizzard Survival Is Not What You Think 🤯 -- 45/100
5. [Transform] What Changed My Mind About Snow Caves 🎭 -- 39/100
```

All five pass `validate_master_title`. The measured character counts are **37, 46, 48, 45, 39**.

> The original specification reported `44/52/51/50/44` for these five titles. Every one of
> those was wrong. `test_master_prompt.py::test_spec_example_counts_are_correct` pins the
> measured values so the document cannot drift again.

---

## Deviations From The Original Specification

Five changes were necessary to make the specification work in this codebase. Each is a
correction, not a preference, and each is guarded by a test.

### 1. The niche table is rendered from code, not copied

The specification carried a hand-maintained niche/keyword table. It was verified to be a
faithful subset of `topic_engine.NICHE_STRONG_KEYWORDS` and is consistent with the
two-tier design, so nothing was lost -- but a second copy of a tuned constant is a drift
waiting to happen. The prompt now renders the table from the code, and
`test_prompt_renders_niche_table_from_code` asserts every keyword appears.

### 2. Rule 9 restated as a check on LETTERS

The original read "ASCII LETTERS ONLY", which re-creates a hazard this project already
hit: an earlier whole-title `isascii()` check rejected emoji and had to be undone. Now
stated as "no non-ASCII **letters**; emoji and punctuation are not letters".

### 3. The URL check is scoped to the topic slot (v1.1.0 correction)

v1.0.0 of this module excluded `watch_this` ("Watch This: The Truth About {kw}") and
`fallback_curiosity` ("Watch To The End Of This {cap} Clip"), on the theory that
**watch** -- a member of `title_seo._URLISH` -- made both unusable. That theory was wrong,
and the exclusion was the actual defect:

- `title_seo.validate_title` has no `watch` rule. It checks `_TITLE_BLOCKED` and
  `_FILLER_RE`, neither of which contains "watch". Dozens of `watch_this` titles shipped
  in `output/` and passed the gate. The templates were always fine.
- What rejected them was this module's own URL-fragment check, which scanned every token
  of the whole title. That is an over-broad gate of exactly the kind
  `validate_title`'s docstring warns about: it mistook intentional template prose
  ("Watch This:") for a URL fragment split out of auto-captions ("youtu.be/watch?v=").
- The cost was silent: 3 of 10 rotations returned four titles instead of five.

The URL check now applies to the **topic list** -- where the fragments actually occur as
mined keywords ("The Proof That https Is Real", "...About dot") -- and both templates are
re-admitted. `test_url_check_ignores_template_prose` pins the scoping, and the
rotation-sweep test pins the count.

### 4. The framework templates are `title_seo`'s, not new ones

An earlier draft of the implementation restated all 18 templates with its own labels,
which broke the `pattern_id` contract and lost the niche-specific patterns entirely. The
master prompt now contributes the specification -- constraints, ranking, output format,
parsing -- and inherits the templates.

### 5. STEP 4 is marked UNAVAILABLE

The specification asks for visual analysis of a clip frame. Nothing in the pipeline writes
a frame to disk or sends one to a model: `face_tracker.py` reads frames in memory via
OpenCV for scene-cut detection only. Claiming to see a visual would be a lie the model
would have to invent around, so the step says so explicitly.

---

## Beyond The Specification: OpusClip-Inspired Production Work

The specification covers constraints and ranking. Seven gaps against OpusClip's
title pipeline were then closed, each as enforced code with tests (2026-09-30):

1. **Clip-level topics, video-level niche.** Heterogeneous videos shipped one video
   topic to every clip ("The Proof that Chicken is Real" on a gym clip).
   `build_clip_topics` mines the clip, `merge_topic_pools` leads with clip topics and
   fills from the video pool, the niche stays video-level. The seam-guard update was
   deliberate and is recorded in the guard's own docstring.
2. **Hook-aware ranking.** `extract_hook_text` takes the clip's first substantive
   spoken sentence; the ranker adds +1.5 when the title's keyword is what the clip
   opens with. The meta exposes `hook_text` for the UI and descriptions.
3. **Per-platform titles.** `select_short_title` picks the best validated candidate
   fitting 50 chars for TikTok/Instagram (never a truncation); YouTube keeps the SEO
   title. Exposed as `short_title` in the meta and platform captions.
4. **Trend signal, honestly empty.** `TRENDING_BOOST` starts empty with a
   populate-from-measurement procedure; no invented weights. Activates once publish
   data exists.
5. **Real descriptions.** `build_description` derives YouTube descriptions (title +
   hook quote + factual Featuring line + CTA + tags) and short captions from the
   FINAL title in exactly one place.
6. **Publish logging.** `publish_log.py` appends one JSONL record per shipped title
   at all four title-deciding call sites (`logs/`, gitignored, never `output/`).
   The first half of the feedback loop; the orchestrator itself never logs, keeping
   the test suite hermetic.
7. **Channel voice profiles.** `TONE_PROFILES` (`high_energy` default reproducing
   prior behavior exactly, `professional` without emoji, `playful`) reorder
   frameworks and toggle emoji. Unknown tones fail fast. Threaded keyword-only, so
   all existing callers are unaffected.

---

## The Defect This Prompt Fixes: First Title Wins

Independent of the prompt itself, the largest quality defect found in the pipeline was
this:

```python
# BEFORE -- three separate fallbacks in score_and_rank_titles_with_jev
return candidates[0]["id"], ...   # no key, Jev down, or partial response
```

Five titles were generated, scored by nobody, and **the first was published**. The shipped
title was a function of list order, not quality -- so reordering the generator silently
changed what went live. `rank_titles_locally` replaces all three with a deterministic
CTR-proxy score that needs no network and no API key.

`test_master_prompt.py::test_ranking_is_not_first_wins` puts the clearly-best candidate
**last** and requires that it be chosen, so an implementation that always returns index 0
cannot pass.

---

## Using The Prompt With Any AI Agent

1. Send the prompt above as the system/instructions message.
2. Send the clip transcript as the user message.
3. If the model supports vision, attach a frame -- but note the pipeline does not
   currently supply one, so STEP 4 must stay marked unavailable.
4. Parse the reply with `parse_master_prompt_response()`, then re-validate every title with
   `validate_master_title()`. **Trust the validator, not the model.** A miscounted
   character length is recorded in `count_mismatches` rather than trusted.
