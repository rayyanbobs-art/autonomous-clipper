import re
import statistics
from typing import Dict, List, Any, Optional

def analyze_channel_outliers(channel_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze channel videos to calculate view statistics and identify statistical outlier hits.
    """
    videos = channel_data.get('videos', [])
    if not videos:
        return {
            'channel_name': channel_data.get('channel_name'),
            'channel_id': channel_data.get('channel_id'),
            'channel_url': channel_data.get('channel_url'),
            'subscribers': channel_data.get('subscribers'),
            'total_videos_analyzed': 0,
            'stats': {
                'median_views': 0,
                'mean_views': 0,
                'max_views': 0,
                'outlier_count': 0,
                'top_multiplier': 1.0,
                'outlier_ratio': 0.0
            },
            'videos': [],
            'outliers': []
        }

    view_counts = [v['views'] for v in videos if v['views'] is not None and v['views'] > 0]
    if not view_counts:
        median_views = 0
        mean_views = 0
        max_views = 0
    else:
        median_views = int(statistics.median(view_counts))
        mean_views = int(statistics.mean(view_counts))
        max_views = max(view_counts)

    # Baseline for outlier multiplier (use median to prevent skew from viral hits)
    baseline = median_views if median_views > 0 else (mean_views if mean_views > 0 else 1)

    annotated_videos = []
    outliers = []

    for v in videos:
        views = v['views'] or 0
        multiplier = round(views / baseline, 2) if baseline > 0 else 1.0

        if multiplier >= 4.0:
            badge = "Mega Outlier"
            badge_color = "red"
            tier = 1
        elif multiplier >= 2.0:
            badge = "Breakout Hit"
            badge_color = "amber"
            tier = 2
        elif multiplier >= 1.3:
            badge = "Above Average"
            badge_color = "emerald"
            tier = 3
        elif multiplier >= 0.7:
            badge = "Standard"
            badge_color = "slate"
            tier = 4
        else:
            badge = "Underperforming"
            badge_color = "gray"
            tier = 5

        item = {
            **v,
            'outlier_multiplier': multiplier,
            'badge': badge,
            'badge_color': badge_color,
            'tier': tier
        }
        annotated_videos.append(item)
        if multiplier >= 1.3:
            outliers.append(item)

    # Sort annotated_videos by outlier multiplier descending (High-to-Low) by default
    annotated_videos.sort(key=lambda x: (x.get('outlier_multiplier') or 0, x.get('views') or 0), reverse=True)

    # Sort outliers by multiplier descending
    outliers.sort(key=lambda x: x['outlier_multiplier'], reverse=True)

    top_mult = round(max([v.get('outlier_multiplier', 1.0) for v in annotated_videos], default=1.0), 2)

    return {
        'channel_name': channel_data.get('channel_name'),
        'channel_id': channel_data.get('channel_id'),
        'channel_url': channel_data.get('channel_url'),
        'subscribers': channel_data.get('subscribers'),
        'total_videos_analyzed': len(videos),
        'stats': {
            'median_views': median_views,
            'mean_views': mean_views,
            'max_views': max_views,
            'outlier_count': len(outliers),
            'top_multiplier': top_mult,
            'outlier_ratio': round(len(outliers) / len(videos), 2) if videos else 0
        },
        'videos': annotated_videos,
        'outliers': outliers
    }

def analyze_hook_and_pacing(transcript_data: Dict[str, Any], hook_cutoff_seconds: float = 60.0) -> Dict[str, Any]:
    """
    Deconstruct the first 60 seconds of speech (The Hook), calculate pacing (WPM),
    and classify the psychological hook framework.
    """
    if not transcript_data.get('available'):
        return {
            'available': False,
            'error': transcript_data.get('error', 'No transcript')
        }

    snippets = transcript_data.get('snippets', [])
    if not snippets:
        return {
            'available': False,
            'error': 'Transcript is empty'
        }

    hook_snippets = []
    body_snippets = []

    for s in snippets:
        if s['start'] <= hook_cutoff_seconds:
            hook_snippets.append(s)
        else:
            body_snippets.append(s)

    hook_text = " ".join(s['text'] for s in hook_snippets).strip()
    full_text = transcript_data.get('full_text', '')

    # Pacing calculations
    hook_words = re.findall(r'\b\w+\b', hook_text)
    total_words = re.findall(r'\b\w+\b', full_text)

    hook_duration = hook_snippets[-1]['start'] + hook_snippets[-1]['duration'] if hook_snippets else hook_cutoff_seconds
    hook_duration = max(10.0, min(hook_duration, hook_cutoff_seconds + 10.0))

    hook_wpm = int((len(hook_words) / hook_duration) * 60) if hook_duration > 0 and len(hook_words) > 0 else 0

    total_duration = (snippets[-1]['start'] + snippets[-1]['duration']) if snippets else 1.0
    total_duration = max(1.0, total_duration)
    overall_wpm = int((len(total_words) / total_duration) * 60) if total_duration > 0 else 0

    # Classify Hook Style
    hook_lower = hook_text.lower()
    frameworks_detected = []

    if re.search(r'\?|(why|what if|how did|have you ever|what happens when)', hook_lower):
        frameworks_detected.append({
            'name': 'Curiosity Question',
            'desc': 'Opens with a burning question to spark immediate curiosity.'
        })

    if re.search(r'\b(secret|truth|never|hidden|nobody told|exposed|conspiracy|lie)\b', hook_lower):
        frameworks_detected.append({
            'name': 'The Unspoken Secret / Forbidden Truth',
            'desc': 'Frames the narrative as insider knowledge or suppressed facts.'
        })

    if re.search(r'\b(danger|deadly|fatal|destroyed|impossible|worst|disaster|threat)\b', hook_lower):
        frameworks_detected.append({
            'name': 'High Stakes / Existential Threat',
            'desc': 'Triggers survival instinct and urgent viewer attention.'
        })

    if re.search(r'\b(in \d{4}|years ago|it was a|he was|she was|once|there was a)\b', hook_lower):
        frameworks_detected.append({
            'name': 'Immersion Story Open',
            'desc': 'Pulls the listener straight into the inciting incident of a narrative.'
        })

    if re.search(r'\b(you think|most people believe|actually|contrary to)\b', hook_lower):
        frameworks_detected.append({
            'name': 'Contrarian / Belief Reversal',
            'desc': 'Challenges common assumptions to stop the scroll.'
        })

    if not frameworks_detected:
        frameworks_detected.append({
            'name': 'Direct Exposition',
            'desc': 'Direct topic introduction setting the scene.'
        })

    # Estimate readability (Flesch-Kincaid grade approximation)
    sentences = re.split(r'[.!?]+', hook_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    num_sentences = max(1, len(sentences))
    words_per_sentence = round(len(hook_words) / num_sentences, 1) if hook_words else 0.0

    if not hook_words:
        pacing_rating = "No Speech in Hook"
        readability_level = "N/A"
    else:
        pacing_rating = 'Fast Paced' if hook_wpm > 165 else ('Conversational' if hook_wpm >= 130 else 'Slow / Dramatic')
        readability_level = "Easy / Conversational" if words_per_sentence < 14 else ("Moderate" if words_per_sentence < 20 else "Complex / Dense")

    return {
        'available': True,
        'hook_text': hook_text or "(No spoken dialogue in first 60 seconds)",
        'hook_duration_seconds': round(hook_duration, 1),
        'hook_word_count': len(hook_words),
        'hook_wpm': hook_wpm,
        'overall_wpm': overall_wpm,
        'pacing_rating': pacing_rating,
        'words_per_sentence': words_per_sentence,
        'readability_level': readability_level,
        'hook_frameworks': frameworks_detected,
        'hook_snippets': hook_snippets,
        'total_transcript_words': len(total_words),
        'total_duration_minutes': round(total_duration / 60, 1)
    }
