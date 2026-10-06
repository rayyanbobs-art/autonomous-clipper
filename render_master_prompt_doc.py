"""
render_master_prompt_doc.py -- generates docs/MASTER_PROMPT_VIRAL_SHORTS_TITLES.md.

The markdown is GENERATED, not hand-written, so the documented prompt and the prompt the
pipeline actually sends cannot disagree. The same discipline as the niche table: render
from the code, assert they match, never keep two copies.

Run:  python render_master_prompt_doc.py
Then:  git diff --exit-code docs/MASTER_PROMPT_VIRAL_SHORTS_TITLES.md   # must be clean
"""

import io
from datetime import date

import title_master_prompt as mp
from title_seo import ALL_PATTERNS

OUT = "docs/MASTER_PROMPT_VIRAL_SHORTS_TITLES.md"


def example_block() -> str:
    rows = []
    for index, (label, title) in enumerate(mp.SPEC_EXAMPLE_ALASKA["titles"], 1):
        ok, reason = mp.validate_master_title(title, mp.SPEC_EXAMPLE_ALASKA["topics"])
        rows.append(
            f"{index}. [{label}] {title} -- {len(title)}/100"
            f"{'' if ok else '  <!-- REJECTED: ' + str(reason) + ' -->'}"
        )
    counts = ", ".join(str(len(t)) for _, t in mp.SPEC_EXAMPLE_ALASKA["titles"])
    return "\n".join(rows), counts


def main() -> None:
    example, counts = example_block()

    header = f"""# Master Prompt -- Viral YouTube Shorts Title Generator

> **Generated file. Do not hand-edit.**
> Produced by `render_master_prompt_doc.py` from `title_master_prompt.py`. If you change
> the prompt, re-run the script and commit both. CI asserts the file is up to date with
> `test_master_prompt.py::test_doc_is_in_sync_with_the_code`.

**Version:** {mp.MASTER_PROMPT_VERSION} &nbsp;&nbsp; **Rendered:** {date.today().isoformat()}

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
| `test_master_prompt.py` | {len(mp.HARD_CONSTRAINTS)}-constraint coverage + regression guards |

---

## The Prompt

```text
{mp.build_master_prompt()}
```

---

## Worked Example

Input transcript:

> {mp.SPEC_EXAMPLE_ALASKA["transcript"]}

```text
Niche: {mp.SPEC_EXAMPLE_ALASKA["niche"]}
Topics Mined: {", ".join(mp.SPEC_EXAMPLE_ALASKA["topics"])}
Visual Context: No visual provided

Titles:
{example}
```

All five pass `validate_master_title`. The measured character counts are **{counts}**.

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

v1.0.0 of this module excluded `watch_this` ("Watch This: The Truth About {{kw}}") and
`fallback_curiosity` ("Watch To The End Of This {{cap}} Clip"), on the theory that
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
"""

    io.open(OUT, "w", encoding="utf-8").write(header)
    print(f"wrote {OUT} ({len(header)} chars)")


if __name__ == "__main__":
    main()
