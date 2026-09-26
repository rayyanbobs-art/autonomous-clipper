import re
import json
import html
import logging
import urllib.request
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_video_id(url_or_id: str) -> str:
    """Extract YouTube video ID from various URL formats or return raw ID."""
    s = url_or_id.strip()
    if len(s) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', s):
        return s

    if not s.startswith('http://') and not s.startswith('https://'):
        if 'youtube.com' in s or 'youtu.be' in s:
            s = 'https://' + s

    if s.startswith('http://') or s.startswith('https://'):
        try:
            parsed = urlparse(s)
            qs = parse_qs(parsed.query)
            if 'v' in qs and len(qs['v']) > 0 and len(qs['v'][0]) == 11:
                return qs['v'][0]

            path_parts = [p for p in parsed.path.split('/') if p]
            if 'youtu.be' in parsed.netloc and path_parts:
                if len(path_parts[0]) == 11:
                    return path_parts[0]

            for keyword in ('shorts', 'embed', 'live', 'v'):
                if keyword in path_parts:
                    idx = path_parts.index(keyword)
                    if idx + 1 < len(path_parts) and len(path_parts[idx + 1]) == 11:
                        return path_parts[idx + 1]
        except Exception:
            pass

    patterns = [
        r'(?:v=|\/)([a-zA-Z0-9_-]{11})(?:[&?]|$)',
        r'youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'shorts\/([a-zA-Z0-9_-]{11})',
        r'embed\/([a-zA-Z0-9_-]{11})',
        r'live\/([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        match = re.search(p, s)
        if match:
            return match.group(1)
    return s

def normalize_channel_url(channel_input: str) -> str:
    """Normalize handle, channel ID, or partial URL to full YouTube /videos URL."""
    s = channel_input.strip()
    if not s:
        return ""

    # Add protocol if missing but domain is present
    if re.match(r'^(?:www\.|m\.)?youtube\.com', s, re.IGNORECASE) or s.startswith('youtube.com'):
        s = 'https://' + s

    if s.startswith('http://') or s.startswith('https://'):
        try:
            parsed = urlparse(s)
            path = parsed.path.rstrip('/')
            # Strip trailing subtabs
            subtabs = ('/videos', '/shorts', '/featured', '/community', '/about', '/channels', '/playlists', '/streams', '/live')
            for subtab in subtabs:
                if path.endswith(subtab):
                    path = path[:-len(subtab)]
                    break
            return f"https://www.youtube.com{path}/videos"
        except Exception:
            pass

    if s.startswith('@'):
        return f"https://www.youtube.com/{s}/videos"
    if s.startswith('UC') and len(s) >= 20:
        return f"https://www.youtube.com/channel/{s}/videos"
    # Treat as handle without @
    return f"https://www.youtube.com/@{s}/videos"

def search_niche_channels(query: str, max_entries: int = 25) -> List[Dict[str, Any]]:
    """
    Search YouTube for a niche query and extract distinct active channels with metadata.
    """
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        'no_warnings': True,
    }
    channels: Dict[str, Dict[str, Any]] = {}
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            res = ydl.extract_info(f'ytsearch{max_entries}:{query}', download=False)
            entries = res.get('entries', []) if res else []
            for entry in entries:
                if not entry:
                    continue
                ch_id = entry.get('channel_id')
                ch_name = entry.get('channel') or entry.get('uploader')
                ch_url = entry.get('channel_url') or entry.get('uploader_url')
                if not ch_id or not ch_name:
                    continue

                view_count = entry.get('view_count') or 0
                sample_vid = {
                    'id': entry.get('id'),
                    'title': entry.get('title'),
                    'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                    'views': view_count,
                    'duration': entry.get('duration'),
                    'thumbnail': (entry.get('thumbnails', [{}])[-1].get('url') if entry.get('thumbnails') else None) or (f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg" if entry.get('id') else None)
                }

                if ch_id not in channels:
                    subs = entry.get('channel_follower_count')
                    thumb = (entry.get('thumbnails', [{}])[-1].get('url') if entry.get('thumbnails') else None) or (f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg" if entry.get('id') else None)
                    channels[ch_id] = {
                        'id': ch_id,
                        'name': ch_name,
                        'url': ch_url,
                        'subs': subs,
                        'thumbnail': thumb,
                        'videos_count': 1,
                        'top_sample_views': view_count,
                        'sample_videos': [sample_vid]
                    }
                else:
                    channels[ch_id]['videos_count'] += 1
                    channels[ch_id]['sample_videos'].append(sample_vid)
                    if view_count > channels[ch_id]['top_sample_views']:
                        channels[ch_id]['top_sample_views'] = view_count
        except Exception as e:
            logger.error(f"Error searching niche channels: {e}")

    # Format and enrich channel results
    results = list(channels.values())
    results.sort(key=lambda x: x['top_sample_views'], reverse=True)
    return results

def get_channel_details(channel_input: str, max_videos: int = 30) -> Dict[str, Any]:
    """
    Fetch comprehensive channel info and recent video upload list with view counts.
    """
    url = normalize_channel_url(channel_input)
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        'playlist_items': f'1:{max_videos}',
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            res = ydl.extract_info(url, download=False)
            if not res:
                raise ValueError("extract_info returned None")
        except Exception as e:
            # Fallback 1: Retry without /videos
            clean_url = url.replace('/videos', '')
            try:
                res = ydl.extract_info(clean_url, download=False)
                if not res:
                    raise ValueError("extract_info returned None")
                url = clean_url
            except Exception as e2:
                # Fallback 2: Search for channel by query if handle was not direct or contained spaces
                try:
                    search_res = ydl.extract_info(f'ytsearch1:{channel_input}', download=False)
                    search_entries = search_res.get('entries', []) if search_res else []
                    if search_entries and search_entries[0]:
                        resolved_url = search_entries[0].get('channel_url') or search_entries[0].get('uploader_url')
                        if resolved_url:
                            norm_resolved = normalize_channel_url(resolved_url)
                            res = ydl.extract_info(norm_resolved, download=False)
                            if not res:
                                raise ValueError("extract_info returned None")
                            url = norm_resolved
                        else:
                            raise ValueError()
                    else:
                        raise ValueError()
                except Exception:
                    logger.error(f"Failed to fetch channel {channel_input}: {e2}")
                    raise ValueError(f"Could not load channel '{channel_input}'. Please verify the channel handle or URL.")

        if not res:
            raise ValueError(f"Could not load channel '{channel_input}'. Please verify the channel handle or URL.")

        ch_name = res.get('title') or res.get('channel') or res.get('uploader') or "Unknown Channel"
        for suffix in (' - Videos', ' - Shorts', ' - Live'):
            if ch_name.endswith(suffix):
                ch_name = ch_name[:-len(suffix)]
                break

        subs = res.get('channel_follower_count') or res.get('subscriber_count')
        entries = res.get('entries', []) or []

        # Fallback to /shorts if /videos has no uploads
        if not entries:
            try:
                base_channel = re.sub(r'/(videos|shorts|live|featured)?/?$', '', url)
                shorts_url = f"{base_channel}/shorts"
                res_shorts = ydl.extract_info(shorts_url, download=False)
                if res_shorts and res_shorts.get('entries'):
                    entries = res_shorts.get('entries') or []
            except Exception:
                pass

        ch_id = res.get('channel_id') or res.get('id')
        dates_map = {}
        if ch_id and str(ch_id).startswith("UC"):
            try:
                rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
                rss_req = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(rss_req, timeout=3) as rss_resp:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(rss_resp.read())
                    ns = {'atom': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
                    for entry in root.findall('atom:entry', ns):
                        v_elem = entry.find('yt:videoId', ns)
                        p_elem = entry.find('atom:published', ns)
                        if v_elem is not None and p_elem is not None and v_elem.text and p_elem.text:
                            dates_map[v_elem.text.strip()] = p_elem.text[:10].replace('-', '')
            except Exception:
                pass

        videos = []

        for rank_idx, v in enumerate(entries):
            if not v:
                continue
            vid_id = v.get('id')
            if not vid_id:
                continue

            views = v.get('view_count') or 0
            dur_secs = v.get('duration') or 0
            minutes = int(dur_secs // 60)
            seconds = int(dur_secs % 60)
            dur_str = f"{minutes}:{seconds:02d}" if dur_secs else "N/A"
            up_date = dates_map.get(vid_id) or v.get('upload_date')

            videos.append({
                'id': vid_id,
                'title': v.get('title') or "Untitled",
                'url': f"https://www.youtube.com/watch?v={vid_id}",
                'views': views,
                'duration_seconds': dur_secs,
                'duration_formatted': dur_str,
                'upload_date': up_date,
                'upload_order': rank_idx,
                'thumbnail': (v.get('thumbnails', [{}])[-1].get('url') if v.get('thumbnails') else None) or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
            })

        return {
            'channel_name': ch_name,
            'channel_id': ch_id,
            'channel_url': res.get('channel_url') or url,
            'subscribers': subs,
            'total_videos_analyzed': len(videos),
            'videos': videos
        }

def _fetch_transcript_ytdlp(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Fallback transcript extractor using yt-dlp's Android player client,
    which bypasses YouTube web IP rate-limits and extracts signed json3 caption events.
    """
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
            if not info:
                return None
            sub_dict = info.get('subtitles') or {}
            auto_dict = info.get('automatic_captions') or {}

            cand = None
            for lang in ['en', 'en-orig', 'en-US', 'en-GB']:
                if lang in sub_dict:
                    cand = sub_dict[lang]
                    break
                if lang in auto_dict:
                    cand = auto_dict[lang]
                    break

            if not cand:
                for lang_list in list(sub_dict.values()) + list(auto_dict.values()):
                    if lang_list:
                        cand = lang_list
                        break

            if not cand:
                return None

            selected_url = None
            selected_ext = None
            for item in cand:
                if item.get('ext') == 'json3':
                    selected_url = item.get('url')
                    selected_ext = 'json3'
                    break
            if not selected_url and cand:
                selected_url = cand[0].get('url')
                selected_ext = cand[0].get('ext', '')

            if not selected_url:
                return None

            req = urllib.request.Request(selected_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_text = resp.read().decode('utf-8', errors='replace')

            snippets = []
            full_parts = []

            # Case A: JSON3 format
            if selected_ext == 'json3' or raw_text.strip().startswith('{'):
                try:
                    data = json.loads(raw_text)
                    for ev in data.get('events', []):
                        segs = ev.get('segs')
                        if not segs:
                            continue
                        text = ''.join(s.get('utf8', '') for s in segs).replace('\n', ' ').strip()
                        if not text:
                            continue
                        start = round(ev.get('tStartMs', 0) / 1000.0, 2)
                        dur = round(ev.get('dDurationMs', 0) / 1000.0, 2)
                        snippets.append({'text': text, 'start': start, 'duration': dur})
                        full_parts.append(text)
                except Exception:
                    pass

            # Case B: WebVTT format
            if not snippets and ('WEBVTT' in raw_text or '-->' in raw_text):
                vtt_re = re.compile(r'((?:(?:\d+:)?\d{2}:\d{2}[\.,]\d{3}))\s*-->\s*((?:(?:\d+:)?\d{2}:\d{2}[\.,]\d{3}))[^\n]*\n((?:(?!\r?\n\r?\n)(?!\d+\r?\n\d).)+)', re.DOTALL)
                for m in vtt_re.finditer(raw_text):
                    s_str, e_str, cue_text = m.group(1), m.group(2), m.group(3)
                    def parse_vtt_ts(ts):
                        parts = ts.replace(',', '.').split(':')
                        if len(parts) == 3:
                            return float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                        elif len(parts) == 2:
                            return float(parts[0])*60 + float(parts[1])
                        return float(parts[0])
                    start = round(parse_vtt_ts(s_str), 2)
                    end = round(parse_vtt_ts(e_str), 2)
                    dur = max(0.1, round(end - start, 2))
                    clean_cue = re.sub(r'<[^>]+>', '', cue_text).replace('\n', ' ').strip()
                    if clean_cue:
                        snippets.append({'text': clean_cue, 'start': start, 'duration': dur})
                        full_parts.append(clean_cue)

            # Case C: XML / TTML / srv1 format
            if not snippets and '<text' in raw_text:
                for m in re.finditer(r'<text(?=[^>]*\bstart="([\d\.]+)")(?=[^>]*\bdur="([\d\.]+)")?[^>]*>(.*?)</text>', raw_text, re.DOTALL):
                    start = round(float(m.group(1)), 2)
                    dur = round(float(m.group(2)), 2) if m.group(2) else 3.0
                    clean_cue = html.unescape(re.sub(r'<[^>]+>', '', m.group(3))).replace('\n', ' ').strip()
                    if clean_cue:
                        snippets.append({'text': clean_cue, 'start': start, 'duration': dur})
                        full_parts.append(clean_cue)

            if snippets:
                return {
                    'video_id': video_id,
                    'available': True,
                    'error': None,
                    'snippets': snippets,
                    'full_text': ' '.join(full_parts)
                }
    except Exception as e:
        logger.warning(f"yt-dlp fallback transcript error for {video_id}: {e}")
    return None

def get_video_transcript(video_id_or_url: str) -> Dict[str, Any]:
    """
    Fetch raw and formatted timestamped transcript for a video using
    youtube-transcript-api with automatic failover to yt-dlp android player.
    """
    video_id = extract_video_id(video_id_or_url)
    api = YouTubeTranscriptApi()

    snippets = None
    try:
        snippets = api.fetch(video_id)
    except Exception:
        try:
            transcript_list = api.list(video_id)
            t = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            snippets = t.fetch()
        except Exception:
            pass

    # If youtube-transcript-api failed or was IP-blocked, invoke yt-dlp Android client fallback
    if not snippets:
        fallback_res = _fetch_transcript_ytdlp(video_id)
        if fallback_res:
            return fallback_res

        return {
            'video_id': video_id,
            'available': False,
            'error': "No captions or transcript available for this video.",
            'snippets': [],
            'full_text': ""
        }

    formatted_snippets = []
    full_text_parts = []

    for item in snippets:
        if isinstance(item, dict):
            text = item.get('text', '') or ''
            start = float(item.get('start', 0.0) or 0.0)
            dur = float(item.get('duration', 0.0) or 0.0)
        else:
            text = getattr(item, 'text', '') or ''
            start = float(getattr(item, 'start', 0.0) or 0.0)
            dur = float(getattr(item, 'duration', 0.0) or 0.0)

        # Clean transcript text artifacts
        text = text.replace('\n', ' ').strip()
        if text:
            formatted_snippets.append({
                'text': text,
                'start': round(start, 2),
                'duration': round(dur, 2)
            })
            full_text_parts.append(text)

    return {
        'video_id': video_id,
        'available': True,
        'error': None,
        'snippets': formatted_snippets,
        'full_text': " ".join(full_text_parts)
    }
