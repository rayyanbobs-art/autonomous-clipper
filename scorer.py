import time
import requests
import re
from typing import List, Dict, Any, Tuple
from config import get_jev_api_key, JEV_ENDPOINT

def create_windows(transcript: List[Dict[str, Any]], min_duration: float = 25.0, max_duration: float = 55.0, step: float = 25.0) -> List[Dict[str, Any]]:
    """
    Groups raw transcript items into contiguous duration windows.
    Each item in transcript has 'text', 'start', and 'duration'.
    """
    if not transcript:
        return []

    total_duration = transcript[-1]["start"] + transcript[-1]["duration"]
    windows = []
    current_start = 0.0

    while current_start + min_duration <= total_duration:
        target_end = current_start + max_duration
        
        # Collect lines in this window
        lines = []
        actual_start = None
        actual_end = current_start

        for item in transcript:
            item_start = item["start"]
            item_end = item_start + item["duration"]

            if item_end >= current_start and item_start <= target_end:
                if actual_start is None:
                    actual_start = item_start
                if (item_end - actual_start) > max_duration:
                    break
                lines.append(item["text"].replace("\n", " ").strip())
                actual_end = max(actual_end, item_end)

            if item_start > target_end:
                break

        if lines and actual_start is not None and min_duration <= (actual_end - actual_start) <= max_duration:
            windows.append({
                "start": round(actual_start, 2),
                "end": round(actual_end, 2),
                "duration": round(actual_end - actual_start, 2),
                "text": " ".join(lines)
            })

        current_start += step

    return windows

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
                virality = data.get("virality", {}).get("score", 0.0)
                virality_conf = data.get("virality", {}).get("confidence", 0.5)
                standalone_prob = data.get("is_standalone", {}).get("noul", 0.5)
                sponsor_prob = data.get("is_sponsor_read", {}).get("noul", 0.0)
                category = data.get("category", {}).get("choice", "high_value_insight")
                
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
    Evaluates multiple candidate windows in parallel batches against Jev System 1 in a single request.
    Yields 10x faster processing for long VODs and podcasts by eliminating sequential network round-trips.
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
                    for i in range(len(batch)):
                        vir = answers.get(f"c{i}_virality", {}).get("score", 0.0)
                        vir_conf = answers.get(f"c{i}_virality", {}).get("confidence", 0.5)
                        standalone = answers.get(f"c{i}_standalone", {}).get("noul", 0.5)
                        sponsor = answers.get(f"c{i}_sponsor", {}).get("noul", 0.0)
                        cat = answers.get(f"c{i}_category", {}).get("choice", "high_value_insight")

                        comp = round((vir * 0.7) + (standalone * 3.0 * 0.3), 2)
                        if sponsor >= 0.50:
                            comp = round(max(0.0, comp - 3.0), 2)

                        results.append({
                            "success": True,
                            "virality_score": vir,
                            "virality_confidence": vir_conf,
                            "standalone_prob": standalone,
                            "sponsor_prob": sponsor,
                            "category": cat,
                            "composite_score": comp
                        })
                    batch_success = True
                    break
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

        # Fallback to sequential if batch call failed
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
    text = candidate.get("text", "")
    virality = candidate.get("virality_score", 1.0)
    standalone = candidate.get("standalone_prob", 0.5)
    sponsor_prob = candidate.get("sponsor_prob", 0.0)
    category = candidate.get("category", "high_value_insight")
    duration = candidate.get("duration", 40.0)

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

def critique_gate_search(
    candidate_windows: List[Dict[str, Any]],
    target_clips: int = 3,
    threshold: float = 7.0,
    max_attempts: int = 4,
    subtitle_style: str = "bold_pop",
    enable_sponsor_killer: bool = True,
    api_key: str = None,
    progress_callback = None
) -> List[Dict[str, Any]]:
    """
    Executes the 6-dimension critique gate process with parallel batch Jev evaluation:
    1. Pre-scores candidates in parallel batches (10x faster).
    2. Enforces Sponsor Killer to reject commercial reads and ad spots.
    3. Evaluates candidates against rubric up to max_attempts.
    """
    if not candidate_windows:
        return []

    # Pre-score candidate windows using parallel batch evaluation (10x speedup)
    unscored_indices = [i for i, c in enumerate(candidate_windows) if "virality_score" not in c]
    if unscored_indices:
        if progress_callback:
            progress_callback(1, 1, f"Scanning {len(unscored_indices)} candidate windows in parallel Jev batches...")
        texts_to_score = [candidate_windows[i]["text"] for i in unscored_indices]
        batch_results = score_chunks_batch_with_jev(texts_to_score, api_key=api_key)
        for idx, res in zip(unscored_indices, batch_results):
            if res.get("success"):
                candidate_windows[idx].update(res)
            else:
                candidate_windows[idx]["virality_score"] = 1.0
                candidate_windows[idx]["standalone_prob"] = 0.5
                candidate_windows[idx]["sponsor_prob"] = 0.0
                candidate_windows[idx]["category"] = "high_value_insight"
                candidate_windows[idx]["composite_score"] = 1.15

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

        while attempt < max_attempts and search_offset < max_search:
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
            if enable_sponsor_killer and cand.get("sponsor_prob", 0.0) >= 0.55:
                # Disqualify sponsor read
                critique_record = {
                    "attempt": attempt,
                    "scores": {"hook": 3, "clarity": 3, "pacing": 5, "payoff": 2, "shareability": 2, "subtitles": 8},
                    "average": 3.8,
                    "passed": False,
                    "notes": f"Disqualified: Brand sponsor read / promo detected ({int(cand['sponsor_prob']*100)}% confidence).",
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

        if best_attempt:
            final_clips.append(best_attempt)
            used_intervals.append((best_attempt["start"], best_attempt["end"]))

    return final_clips

def filter_non_overlapping_clips(scored_clips: List[Dict[str, Any]], top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Selects top-K scoring clips ensuring non-overlapping time intervals.
    """
    sorted_clips = sorted(scored_clips, key=lambda x: x.get("composite_score", 0), reverse=True)
    selected = []

    for candidate in sorted_clips:
        if not candidate.get("success"):
            continue
        c_start = candidate["start"]
        c_end = candidate["end"]
        overlap = False
        for s in selected:
            s_start = s["start"]
            s_end = s["end"]
            if max(0, min(c_end, s_end) - max(c_start, s_start)) > 10.0:
                overlap = True
                break
        if not overlap:
            selected.append(candidate)
            if len(selected) >= top_k:
                break

    return selected
