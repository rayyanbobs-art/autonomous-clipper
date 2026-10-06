import time
import requests
import re
from typing import List, Dict, Any, Tuple, Optional, Union
from config import get_jev_api_key, JEV_ENDPOINT

def parse_timestamp_to_seconds(ts: Optional[Union[str, float, int]]) -> Optional[float]:
    """
    Converts various timestamp formats into seconds (float):
      - "10:00" -> 600.0
      - "01:05:30" -> 3930.0
      - "45" / 45 / 45.5 -> 45.0 / 45.5
    Returns None if ts is None, empty string, or invalid.
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        val = float(ts)
        return val if val >= 0.0 and val == val else None
    if not isinstance(ts, str):
        return None

    raw = ts.strip()
    if not raw:
        return None

    # Handle colon-separated timestamps: [HH:]MM:SS or MM:SS
    if ":" in raw:
        parts = raw.split(":")
        try:
            if len(parts) == 2:
                m, s = float(parts[0]), float(parts[1])
                if m < 0 or s < 0:
                    return None
                return m * 60.0 + s
            elif len(parts) == 3:
                h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                if h < 0 or m < 0 or s < 0:
                    return None
                return h * 3600.0 + m * 60.0 + s
            else:
                return None
        except (ValueError, TypeError):
            return None

    # Handle plain numbers (seconds)
    try:
        val = float(raw)
        return val if val >= 0.0 and val == val else None
    except (ValueError, TypeError):
        return None


# Sentinel distinguishing "the API returned an explicit null" from "the key was absent".
# dict.get(key, default) only substitutes the default when the key is MISSING, so a JSON
# null propagates straight into arithmetic and raises TypeError further downstream.
MISSING = object()


def _as_float(value: Any, default: float, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    """Null-safe numeric coercion. Treats None/""/NaN as absent and clamps to [lo, hi]."""
    if value is None or isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num != num:  # NaN
        return default
    if lo is not None and num < lo:
        return lo
    if hi is not None and num > hi:
        return hi
    return num


def _answer(answers: Dict[str, Any], key: str, sub_key: str, default: Any = MISSING) -> Any:
    """
    Reads a nested Jev answer, coercing an explicit JSON null to MISSING so callers can
    distinguish "the model did not answer" from "the model answered zero".
    """
    node = answers.get(key)
    if not isinstance(node, dict):
        return default
    val = node.get(sub_key, default)
    return default if val is None else val

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_GREETING = re.compile(
    r"^\s*(?:hey|hi|hello|what'?s up|yo)[\s,]+(?:there[\s,]+)?(?:guys|everyone|everybody|y'?all|folks|friends)\b"
    r"|^\s*welcome back\b"
    r"|^\s*welcome to (?:the|my|our) channel\b",
    re.IGNORECASE,
)
VAD_PAUSE_SEC = 0.18      # a caption gap this long is a natural speech pause
SNAP_START_SLACK = 8.0    # max seconds trimmed from the head to reach a sentence start
SNAP_END_SLACK = 12.0     # max seconds trimmed from the tail to reach a sentence end


def _is_boundary(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> bool:
    """True when a sentence ends, or a >=180ms pause sits, between two caption items."""
    if before is None or after is None:
        return True
    gap = after["start"] - (before["start"] + before["duration"])
    return gap >= VAD_PAUSE_SEC or bool(_SENTENCE_END.search(before["text"].strip()))


def _snap_window(items: List[Dict[str, Any]], prev_item: Optional[Dict[str, Any]],
                 next_item: Optional[Dict[str, Any]], min_duration: float) -> List[Dict[str, Any]]:
    """Trims a window so it opens on a hook (no greeting, sentence start) and closes on a
    sentence end / pause. Never returns a window shorter than min_duration: falls back to
    the greeting-trimmed window, then to the raw one."""
    def span(seq):
        return (seq[-1]["start"] + seq[-1]["duration"]) - seq[0]["start"] if seq else 0.0

    head = 0
    while head < min(3, len(items) - 1) and _GREETING.search(items[head]["text"]):
        head += 1

    s = head
    for k in range(head, len(items)):
        if items[k]["start"] - items[head]["start"] > SNAP_START_SLACK:
            break
        if (k == head and head > 0) or _is_boundary(items[k - 1] if k > 0 else prev_item, items[k]):
            s = k
            break

    e = len(items) - 1
    last_end = items[-1]["start"] + items[-1]["duration"]
    for k in range(len(items) - 1, s - 1, -1):
        if last_end - (items[k]["start"] + items[k]["duration"]) > SNAP_END_SLACK:
            break
        if _is_boundary(items[k], items[k + 1] if k + 1 < len(items) else next_item):
            e = k
            break

    for candidate in (items[s:e + 1], items[head:]):
        if candidate and span(candidate) >= min_duration:
            return candidate
    return items


def create_windows(
    transcript: List[Dict[str, Any]],
    min_duration: float = 25.0,
    max_duration: float = 55.0,
    step: float = 25.0,
    range_start: Optional[float] = None,
    range_end: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    Groups raw transcript items into contiguous duration windows.
    Each item in transcript has 'text', 'start', and 'duration'.
    Supports restricting window generation to a specific [range_start, range_end] time interval.
    """
    if not transcript:
        return []

    total_duration = transcript[-1]["start"] + transcript[-1]["duration"]
    effective_start = max(0.0, float(range_start)) if range_start is not None else 0.0
    effective_end = min(total_duration, float(range_end)) if range_end is not None else total_duration

    if effective_start >= effective_end or (effective_end - effective_start) < min_duration:
        return []

    windows = []
    current_start = effective_start

    while current_start + min_duration <= effective_end:
        target_end = min(current_start + max_duration, effective_end)
        
        # Collect lines in this window
        lines = []
        actual_start = None
        actual_end = current_start
        items_in_win = []

        first_idx = last_idx = 0
        for idx, item in enumerate(transcript):
            item_start = item["start"]
            item_end = item_start + item["duration"]

            if item_start < effective_start:
                continue
            if item_end > effective_end:
                break
            if item_start > target_end:
                break

            if item_end >= current_start and item_start <= target_end:
                if actual_start is None:
                    actual_start = item_start
                    first_idx = idx
                if (item_end - actual_start) > max_duration:
                    break
                lines.append(item["text"].replace("\n", " ").strip())
                actual_end = max(actual_end, item_end)
                items_in_win.append(item)
                last_idx = idx

        if items_in_win:
            # Never open mid-sentence / on "hey guys", never close mid-thought
            prev_item = transcript[first_idx - 1] if first_idx > 0 else None
            next_item = transcript[last_idx + 1] if last_idx + 1 < len(transcript) else None
            items_in_win = _snap_window(items_in_win, prev_item, next_item, min_duration)
            lines = [it["text"].replace("\n", " ").strip() for it in items_in_win]
            actual_start = items_in_win[0]["start"]
            actual_end = max(it["start"] + it["duration"] for it in items_in_win)
            if windows and windows[-1]["start"] == round(actual_start, 2) and windows[-1]["end"] == round(actual_end, 2):
                current_start += step
                continue

        if lines and actual_start is not None and min_duration <= (actual_end - actual_start) <= max_duration:
            if actual_start < effective_start or actual_end > effective_end:
                current_start += step
                continue

            # 4-Phase Rhythmic Pacing Analysis:
            # Phase 1: Establish (0.0s - 6.0s): Stable hold (>= 2.5s) to anchor viewer
            # Phase 2: Accelerate (6.0s - 35.0s): Dynamic motion cuts (1.2s - 2.5s)
            # Phase 3: Breathe / Punctuate (35.0s - 45.0s): Payoff hold (>= 3.0s)
            # Phase 4: Resolution / Loop (Tail 5s): Seamless boundary taper
            init_hold = items_in_win[0]["duration"] if items_in_win else 0.0
            payoff_hold = items_in_win[-1]["duration"] if items_in_win else 0.0
            mid_items = items_in_win[1:-1] if len(items_in_win) > 2 else []
            mid_valid = (
                sum(1 for it in mid_items if 1.2 <= it["duration"] <= 2.5) / max(1, len(mid_items))
                if mid_items else 0.5
            )
            cadence_score = 0.0
            if init_hold >= 2.5:
                cadence_score += 0.4
            if mid_valid >= 0.4:
                cadence_score += 0.3
            if payoff_hold >= 3.0:
                cadence_score += 0.3

            windows.append({
                "start": round(actual_start, 2),
                "end": round(actual_end, 2),
                "duration": round(actual_end - actual_start, 2),
                "text": " ".join(lines),
                "rhythmic_pacing": {
                    "initial_hold": round(init_hold, 2),
                    "payoff_hold": round(payoff_hold, 2),
                    "cadence_score": round(cadence_score, 2),
                    "follows_4phase": bool(cadence_score >= 0.6)
                }
            })

        current_start += step

    return windows

_HOOK_RE = re.compile(r"\b(why|how|what|never|always|secret|mistake|listen|truth|stop|imagine|the biggest)\b|\?")
_STAKES_RE = re.compile(r"\b(crazy|insane|dangerous|deadly|died|dying|survive|survival|huge|massive|biggest|worst|"
                        r"best|finally|unbelievable|shocking|scared|terrifying|frozen|bear|wolf|storm|emergency|"
                        r"secret|mistake|lost|broke|million|thousand)\b")
_FILLER_RE = re.compile(r"\b(um+|uh+|you know|i mean|kind of|sort of)\b")
_SPONSOR_RE = re.compile(r"\b(sponsor(?:ed)?|promo code|use code|discount code|link in (?:the )?description|"
                         r"brought to you by|affiliate|check out .{0,20}\.com)\b")
_DANGLING_OPENER_RE = re.compile(r"^\s*(and|but|so|because|he|she|they|it|that|this|which)\b")


def local_fallback_score(chunk_text: str) -> Dict[str, Any]:
    """Deterministic offline stand-in for Jev scoring (same output keys), used only when the
    API is unreachable. Same text always yields the same score."""
    t = (chunk_text or "").lower()
    n = max(1, len(t.split()))
    virality = 1.0
    virality += 0.5 if _HOOK_RE.search(t[:80]) else 0.0
    virality += min(0.8, 25.0 * len(_STAKES_RE.findall(t)) / n)
    virality += min(0.3, 0.1 * len(re.findall(r"\b\d+\b", t)))
    virality -= min(0.6, 10.0 * len(_FILLER_RE.findall(t)) / n)
    virality = round(max(0.0, min(3.0, virality)), 2)
    standalone = 0.4 if _DANGLING_OPENER_RE.search(t) else 0.65
    sponsor = 0.8 if _SPONSOR_RE.search(t) else 0.0
    composite = round((virality * 0.7) + (standalone * 3.0 * 0.3), 2)
    if sponsor >= 0.50:
        composite = round(max(0.0, composite - 3.0), 2)
    return {
        "success": True,
        "virality_score": virality,
        "virality_confidence": 0.3,
        "standalone_prob": standalone,
        "sponsor_prob": sponsor,
        "category": "compelling_story" if re.search(r"\b(i|we) (was|were|went|found|saw)\b", t) else "high_value_insight",
        "composite_score": composite,
        "score_source": "local_heuristic",
    }


def score_chunk_with_jev(chunk_text: str, api_key: str = None) -> Dict[str, Any]:
    """
    Sends a chunk of transcript to Jev for System 1 virality, standalone, and category scoring.
    """
    if not api_key:
        api_key = get_jev_api_key()

    if not api_key:
        return {"success": False, "error": "Jev API key is missing. Ensure JEV_API_KEY or config file is set."}

    questions = {
        "virality": {
            "type": "score",
            "instructions": "Rate the viral hook and curiosity potential of this spoken segment for a 30-60 second short video.",
            "criteria": [
                "Boring small talk, pleasantries, or low-value filler",
                "Ordinary conversation with minor interest",
                "Strong insight, useful principle, or memorable story",
                "Exceptional viral hook, controversial take, or shocking statement"
            ]
        },
        "is_standalone": {
            "type": "noul",
            "instructions": "Can a viewer fully understand and appreciate this clip without watching the rest of the podcast?"
        },
        "is_sponsor_read": {
            "type": "noul",
            "instructions": "Does this spoken segment contain a brand sponsorship, advertisement read, promo code, affiliate link, paid partnership, or commercial plug?"
        },
        "category": {
            "type": "choice",
            "instructions": "Identify the primary hook format.",
            "criteria": {
                "high_value_insight": "Actionable business, career, or life advice",
                "shocking_revelation": "Surprising science, statistic, or hidden truth",
                "compelling_story": "Dramatic or humorous real-life story",
                "controversial_opinion": "Strong stance on a hot-button topic",
                "filler_banter": "Casual chit-chat or generic banter"
            }
        }
    }

    payload = {
        "model": "typesafe-ai/jev",
        "state": {"transcript_segment": chunk_text},
        "questions": questions
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    for attempt in range(1, 4):
        try:
            res = requests.post(JEV_ENDPOINT, json=payload, headers=headers, timeout=20)
            if res.status_code == 200:
                data = res.json().get("data", {}).get("answers", {})
                if not isinstance(data, dict):
                    return {"success": False, "error": "Malformed Jev response: 'answers' was not an object."}

                raw_virality = _answer(data, "virality", "score")
                if raw_virality is MISSING:
                    return {"success": False, "error": "Jev response did not contain a 'virality' score."}

                virality = _as_float(raw_virality, 0.0, 0.0, 3.0)
                virality_conf = _as_float(_answer(data, "virality", "confidence", 0.5), 0.5, 0.0, 1.0)
                standalone_prob = _as_float(_answer(data, "is_standalone", "noul", 0.5), 0.5, 0.0, 1.0)
                sponsor_prob = _as_float(_answer(data, "is_sponsor_read", "noul", 0.0), 0.0, 0.0, 1.0)
                category = _answer(data, "category", "choice", "high_value_insight")
                if not isinstance(category, str) or not category:
                    category = "high_value_insight"

                composite_score = round((virality * 0.7) + (standalone_prob * 3.0 * 0.3), 2)
                if sponsor_prob >= 0.50:
                    composite_score = round(max(0.0, composite_score - 3.0), 2)
                
                return {
                    "success": True,
                    "virality_score": virality,
                    "virality_confidence": virality_conf,
                    "standalone_prob": standalone_prob,
                    "sponsor_prob": sponsor_prob,
                    "category": category,
                    "composite_score": composite_score
                }
            elif res.status_code in [429, 500, 502, 503, 504, 529]:
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                else:
                    return {"success": False, "error": f"API rate limit / server error ({res.status_code})"}
            else:
                return {"success": False, "error": f"API error {res.status_code}: {res.text}"}
        except Exception as e:
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Max retries reached"}

def score_chunks_batch_with_jev(chunk_texts: List[str], api_key: str = None, batch_size: int = 5) -> List[Dict[str, Any]]:
    """
    Evaluates multiple candidate windows against Jev System 1, packing several candidates
    into a single request to cut the number of network round-trips for long VODs and
    podcasts.

    Batches are issued sequentially (one in-flight request at a time) and each batch falls
    back to per-chunk scoring if the packed request fails or returns an incomplete answer
    set — a partial answer set is never recorded as a legitimate 0.0 score.
    """
    if not chunk_texts:
        return []

    if not api_key:
        api_key = get_jev_api_key()

    if not api_key:
        return [{"success": False, "error": "Jev API key is missing"} for _ in chunk_texts]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    results: List[Dict[str, Any]] = []

    for start_idx in range(0, len(chunk_texts), batch_size):
        batch = chunk_texts[start_idx:start_idx + batch_size]
        state = {f"candidate_{i}": text for i, text in enumerate(batch)}
        questions = {}

        for i in range(len(batch)):
            questions[f"c{i}_virality"] = {
                "type": "score",
                "instructions": f"Rate the viral hook and curiosity potential of candidate_{i} for a 30-60 second short video.",
                "criteria": [
                    "Boring small talk, pleasantries, or low-value filler",
                    "Ordinary conversation with minor interest",
                    "Strong insight, useful principle, or memorable story",
                    "Exceptional viral hook, controversial take, or shocking statement"
                ]
            }
            questions[f"c{i}_standalone"] = {
                "type": "noul",
                "instructions": f"Can candidate_{i} be fully understood and appreciated without watching the rest of the podcast?"
            }
            questions[f"c{i}_sponsor"] = {
                "type": "noul",
                "instructions": f"Does candidate_{i} contain a brand sponsorship, advertisement read, promo code, affiliate link, paid partnership, or commercial plug?"
            }
            questions[f"c{i}_category"] = {
                "type": "choice",
                "instructions": f"Identify the primary hook format of candidate_{i}.",
                "criteria": {
                    "high_value_insight": "Actionable business, career, or life advice",
                    "shocking_revelation": "Surprising science, statistic, or hidden truth",
                    "compelling_story": "Dramatic or humorous real-life story",
                    "controversial_opinion": "Strong stance on a hot-button topic",
                    "filler_banter": "Casual chit-chat or generic banter"
                }
            }

        payload = {
            "model": "typesafe-ai/jev",
            "state": state,
            "questions": questions
        }

        batch_success = False
        for attempt in range(1, 4):
            try:
                res = requests.post(JEV_ENDPOINT, json=payload, headers=headers, timeout=25)
                if res.status_code == 200:
                    answers = res.json().get("data", {}).get("answers", {})
                    if not isinstance(answers, dict):
                        answers = {}

                    # Only commit the batch if EVERY candidate came back with a real answer.
                    # A truncated / partially-refused / shape-changed response would otherwise
                    # be written out as virality 0.0 with success=True and silently lose
                    # every comparison in the critique gate.
                    parsed: List[Dict[str, Any]] = []
                    complete = True
                    for i in range(len(batch)):
                        raw_vir = _answer(answers, f"c{i}_virality", "score")
                        if raw_vir is MISSING:
                            print(f"  [Jev Batch] Incomplete response: c{i}_virality missing; "
                                  f"falling back to per-candidate scoring for this batch.")
                            complete = False
                            break

                        vir = _as_float(raw_vir, 0.0, 0.0, 3.0)
                        vir_conf = _as_float(_answer(answers, f"c{i}_virality", "confidence", 0.5), 0.5, 0.0, 1.0)
                        standalone = _as_float(_answer(answers, f"c{i}_standalone", "noul", 0.5), 0.5, 0.0, 1.0)
                        sponsor = _as_float(_answer(answers, f"c{i}_sponsor", "noul", 0.0), 0.0, 0.0, 1.0)
                        cat = _answer(answers, f"c{i}_category", "choice", "high_value_insight")
                        if not isinstance(cat, str) or not cat:
                            cat = "high_value_insight"

                        comp = round((vir * 0.7) + (standalone * 3.0 * 0.3), 2)
                        if sponsor >= 0.50:
                            comp = round(max(0.0, comp - 3.0), 2)

                        parsed.append({
                            "success": True,
                            "virality_score": vir,
                            "virality_confidence": vir_conf,
                            "standalone_prob": standalone,
                            "sponsor_prob": sponsor,
                            "category": cat,
                            "composite_score": comp
                        })

                    if complete:
                        results.extend(parsed)
                        batch_success = True
                        break
                    # Incomplete: discard the partial parse and fall through to the
                    # per-candidate fallback below rather than recording bogus zeros.
                elif res.status_code in [429, 500, 502, 503, 504, 529]:
                    time.sleep(2 * attempt)
                    continue
                else:
                    break
            except Exception:
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                break

        # Fallback to sequential if the packed batch call failed or answered only partially
        if not batch_success:
            for text in batch:
                results.append(score_chunk_with_jev(text, api_key=api_key))

    return results

def compute_rubric_scores(
    candidate: Dict[str, Any],
    subtitle_style: str = "bold_pop",
    enable_sponsor_killer: bool = True
) -> Tuple[Dict[str, int], float, str]:
    """
    Evaluates a candidate clip across 6 criteria (1-10 each):
    Hook, Standalone Clarity, Pacing, Payoff, Shareability, Subtitles.
    Evaluates on a true 1-10 scale. Automatically penalizes and rejects sponsor reads.
    """
    # dict.get(key, default) does NOT substitute the default when the stored value is an
    # explicit JSON null, so every field is coerced here instead of at the comparison site.
    text = candidate.get("text") or ""
    virality = _as_float(candidate.get("virality_score"), 1.0, 0.0, 3.0)
    standalone = _as_float(candidate.get("standalone_prob"), 0.5, 0.0, 1.0)
    sponsor_prob = _as_float(candidate.get("sponsor_prob"), 0.0, 0.0, 1.0)
    category = candidate.get("category") or "high_value_insight"
    duration = _as_float(candidate.get("duration"), 40.0, 0.0)

    # 1. Hook (1 to 10)
    first_words = text[:60].lower()
    hook_patterns = [r"\b(why|how|what|never|always|secret|mistake|listen|truth|stop|imagine|the biggest)\b", r"\?"]
    has_hook_pattern = any(re.search(p, first_words) for p in hook_patterns)
    if has_hook_pattern and virality >= 2.0:
        hook_score = 9
    elif has_hook_pattern and virality >= 1.5:
        hook_score = 8
    elif has_hook_pattern or virality >= 1.2:
        hook_score = 7
    elif virality >= 0.8:
        hook_score = 6
    elif category == "filler_banter":
        hook_score = 4
    else:
        hook_score = 5

    # 2. Standalone Clarity (1 to 10)
    if standalone >= 0.80:
        clarity_score = 9
    elif standalone >= 0.65:
        clarity_score = 8
    elif standalone >= 0.45:
        clarity_score = 7
    elif standalone >= 0.30:
        clarity_score = 6
    elif standalone >= 0.15:
        clarity_score = 5
    else:
        clarity_score = 4

    # 3. Pacing (1 to 10)
    # Optimal Shorts pacing: 30s to 50s, high word rate without dead filler
    words_count = len(text.split())
    words_per_sec = words_count / max(1.0, duration)
    if 2.2 <= words_per_sec <= 3.8 and 30 <= duration <= 52:
        pacing_score = 9
    elif 1.8 <= words_per_sec <= 4.2 and 25 <= duration <= 60:
        pacing_score = 8
    elif 1.4 <= words_per_sec:
        pacing_score = 7
    elif category == "filler_banter":
        pacing_score = 5
    else:
        pacing_score = 5

    # 4-Phase Rhythmic Pacing Bonus: reward candidates following initial hold >= 2.5s, middle cuts 1.2-2.5s, payoff >= 3.0s
    if candidate.get("rhythmic_pacing", {}).get("follows_4phase"):
        pacing_score = min(10, max(pacing_score + 1, 9))

    # 4. Payoff (1 to 10)
    if virality >= 2.2 and category != "filler_banter":
        payoff_score = 9
    elif virality >= 1.6 and category != "filler_banter":
        payoff_score = 8
    elif virality >= 1.1:
        payoff_score = 7
    elif category == "filler_banter":
        payoff_score = 5
    else:
        payoff_score = 5

    # 5. Shareability (1 to 10)
    if category in ["high_value_insight", "shocking_revelation", "controversial_opinion"] and virality >= 1.8:
        shareability_score = 9
    elif category in ["high_value_insight", "compelling_story"] and virality >= 1.2:
        shareability_score = 8
    elif category != "filler_banter":
        shareability_score = 7
    elif virality >= 0.9:
        shareability_score = 6
    else:
        shareability_score = 4

    # 6. Subtitles (1 to 10)
    # High-quality word-synced subtitles with modern creator styles score 9
    if subtitle_style in ["bold_pop", "karaoke", "hormozi", "beast", "neon_green", "boxed_clean"]:
        subtitles_score = 9
    else:
        subtitles_score = 8

    # Sponsor Killer Penalty: heavily downgrade commercial plugs and ad reads
    is_sponsor = enable_sponsor_killer and sponsor_prob >= 0.50
    if is_sponsor:
        hook_score = min(hook_score, 3)
        clarity_score = min(clarity_score, 3)
        payoff_score = min(payoff_score, 2)
        shareability_score = min(shareability_score, 2)

    scores = {
        "hook": hook_score,
        "clarity": clarity_score,
        "pacing": pacing_score,
        "payoff": payoff_score,
        "shareability": shareability_score,
        "subtitles": subtitles_score
    }

    average = round(sum(scores.values()) / len(scores), 1)

    # Diagnostic notes (reduced by 50% verbosity per guidelines)
    if is_sponsor:
        notes = f"Lowest criterion: Sponsor Read ({int(sponsor_prob * 100)}% confidence). Commercial plug rejected."
    else:
        lowest_criterion = min(scores, key=scores.get)
        remedy_map = {
            "hook": "Lowest criterion: Hook. Shift window start toward opening question or punchy claim.",
            "clarity": "Lowest criterion: Clarity. Requires context; select more self-contained point.",
            "pacing": "Lowest criterion: Pacing. Contains dead air; tighten clip duration.",
            "payoff": "Lowest criterion: Payoff. Lacks clear resolution; extend boundary to conclusion.",
            "shareability": "Lowest criterion: Shareability. Niche banter; target broadly relatable topic.",
            "subtitles": "Lowest criterion: Subtitles. Optimize subtitle chunk cadence."
        }
        notes = remedy_map.get(lowest_criterion, f"Lowest criterion: {lowest_criterion}.")

    return scores, average, notes

evaluate_candidate_against_rubric = compute_rubric_scores

def critique_gate_search(
    candidate_windows: List[Dict[str, Any]],
    target_clips: int = 3,
    threshold: float = 7.0,
    max_attempts: int = 4,
    subtitle_style: str = "bold_pop",
    enable_sponsor_killer: bool = True,
    api_key: str = None,
    progress_callback = None,
    shortfall_callback = None
) -> List[Dict[str, Any]]:
    """
    Executes the 6-dimension critique gate process with packed-batch Jev evaluation:
    1. Pre-scores candidates in packed batches (fewer network round-trips).
    2. Enforces Sponsor Killer to reject commercial reads and ad spots.
    3. Evaluates candidates against rubric up to max_attempts.

    If a clip slot cannot be filled because every reachable candidate window overlaps an
    already-selected clip, the shortfall is reported through the optional
    `shortfall_callback` rather than being returned as a silently smaller list.
    """
    if not candidate_windows:
        return []

    # Pre-score candidate windows using packed batch evaluation
    unscored_indices = [i for i, c in enumerate(candidate_windows) if "virality_score" not in c]
    if unscored_indices:
        if progress_callback:
            progress_callback(1, 1, f"Scanning {len(unscored_indices)} candidate windows in packed Jev batches...")
        texts_to_score = [candidate_windows[i]["text"] for i in unscored_indices]
        batch_results = score_chunks_batch_with_jev(texts_to_score, api_key=api_key)
        for idx, res in zip(unscored_indices, batch_results):
            if res.get("success"):
                candidate_windows[idx].update(res)
            else:
                # Jev scoring failed (offline / rate-limited): rank with the deterministic
                # local heuristic instead of a flat tie, and keep it flagged as a fallback.
                candidate_windows[idx].update(local_fallback_score(candidate_windows[idx].get("text", "")))
                candidate_windows[idx]["score_failed"] = True

    final_clips = []
    used_intervals = []

    total_candidates = len(candidate_windows)
    stride = max(1, total_candidates // max(1, target_clips))

    for clip_idx in range(target_clips):
        best_attempt = None
        best_avg = -1.0

        base_index = (clip_idx * stride) % total_candidates
        attempt = 0
        search_offset = 0
        max_search = min(total_candidates, max_attempts * 5)

        def _sweep(limit: int) -> None:
            """Evaluates candidates starting at base_index, skipping overlaps. Mutates the
            enclosing scope's best_attempt / best_avg / attempt / search_offset."""
            nonlocal best_attempt, best_avg, attempt, search_offset

            while attempt < max_attempts and search_offset < limit:
                cand_idx = (base_index + search_offset) % total_candidates
                search_offset += 1
                cand = candidate_windows[cand_idx].copy()

                # Check overlap with already finalized clips
                c_start = cand["start"]
                c_end = cand["end"]
                is_overlapping = any(
                    max(0, min(c_end, u_end) - max(c_start, u_start)) > 10.0
                    for u_start, u_end in used_intervals
                )
                if is_overlapping:
                    continue

                attempt += 1

                if progress_callback:
                    progress_callback(clip_idx + 1, attempt, f"Evaluating Attempt {attempt}/{max_attempts} with Jev & Critique Gate...")

                # Sponsor Killer guardrail: Disqualify candidate early if confident it is an ad
                cand_sponsor = _as_float(cand.get("sponsor_prob"), 0.0, 0.0, 1.0)
                if enable_sponsor_killer and cand_sponsor >= 0.55:
                    # Disqualify sponsor read
                    critique_record = {
                        "attempt": attempt,
                        "scores": {"hook": 3, "clarity": 3, "pacing": 5, "payoff": 2, "shareability": 2, "subtitles": 8},
                        "average": 3.8,
                        "passed": False,
                        "notes": f"Disqualified: Brand sponsor read / promo detected ({int(cand_sponsor*100)}% confidence).",
                        "subtitle_style": subtitle_style
                    }
                    cand["critique"] = critique_record
                    continue

                scores, avg, notes = compute_rubric_scores(cand, subtitle_style=subtitle_style, enable_sponsor_killer=enable_sponsor_killer)
                passed = (avg >= threshold)

                critique_record = {
                    "attempt": attempt,
                    "scores": scores,
                    "average": avg,
                    "passed": passed,
                    "notes": notes,
                    "subtitle_style": subtitle_style
                }
                cand["critique"] = critique_record

                if avg > best_avg or best_attempt is None:
                    best_avg = avg
                    best_attempt = cand

                if passed:
                    break

        _sweep(max_search)

        # Windows are 28-55s long and generated with a 25s step, so they overlap heavily and
        # the >10.0s test rejects most neighbours. If the narrow sweep found nothing, widen
        # to the full candidate set before giving up on this clip slot.
        if best_attempt is None and total_candidates > max_search:
            attempt = 0
            search_offset = 0
            _sweep(total_candidates)

        if best_attempt:
            final_clips.append(best_attempt)
            used_intervals.append((best_attempt["start"], best_attempt["end"]))

    if shortfall_callback and len(final_clips) < target_clips:
        shortfall_callback(len(final_clips), target_clips)

    return final_clips

def filter_non_overlapping_clips(scored_clips: List[Dict[str, Any]], top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Selects top-K scoring clips ensuring non-overlapping time intervals.
    """
    sorted_clips = sorted(scored_clips, key=lambda x: _as_float(x.get("composite_score"), 0.0), reverse=True)
    selected = []

    for candidate in sorted_clips:
        if not candidate.get("success"):
            continue
        c_start = _as_float(candidate.get("start"), 0.0)
        c_end = _as_float(candidate.get("end"), 0.0)
        overlap = False
        for s in selected:
            s_start = _as_float(s.get("start"), 0.0)
            s_end = _as_float(s.get("end"), 0.0)
            if max(0, min(c_end, s_end) - max(c_start, s_start)) > 10.0:
                overlap = True
                break
        if not overlap:
            selected.append(candidate)
            if len(selected) >= top_k:
                break

    return selected
