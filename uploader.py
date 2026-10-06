import os
import json
import time
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from config import BASE_DIR, OUTPUT_DIR

UPLOAD_CONFIG_PATH = BASE_DIR / "upload_config.json"
DEFAULT_CLIENT_SECRETS = BASE_DIR / "client_secrets.json"
DEFAULT_YOUTUBE_TOKEN = BASE_DIR / "youtube_token.json"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

# Google retired the out-of-band (OOB) redirect for installed apps in 2022. The supported
# manual-copy alternative is the loopback redirect: Google redirects the browser to
# http://localhost/?code=... , that page fails to load, and the user copies `code` off the
# address bar. Any port is accepted by Google for loopback clients.
DEFAULT_LOOPBACK_REDIRECT = "http://localhost"

AYRSHARE_POST_URL = "https://app.ayrshare.com/api/post"
AYRSHARE_MEDIA_URL = "https://app.ayrshare.com/api/media/upload"


def load_upload_config() -> Dict[str, Any]:
    """Loads upload configuration (credentials and automation settings)."""
    default_cfg = {
        "youtube": {
            "client_secrets_file": str(DEFAULT_CLIENT_SECRETS),
            "token_file": str(DEFAULT_YOUTUBE_TOKEN),
            "is_authenticated": False,
            "channel_title": "",
            "channel_id": ""
        },
        "ayrshare": {
            "api_key": "",
            "is_configured": False
        },
        "auto_upload": {
            "enabled": False,
            "platforms": ["youtube"],
            "default_privacy": "public"
        }
    }

    if UPLOAD_CONFIG_PATH.exists():
        try:
            with open(UPLOAD_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge with default structure
                for k, v in default_cfg.items():
                    if k not in data:
                        data[k] = v
                    elif isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            if sub_k not in data[k]:
                                data[k][sub_k] = sub_v
                return data
        except Exception:
            pass

    return default_cfg


def save_upload_config(config_data: Dict[str, Any]) -> bool:
    """Saves updated upload configuration to disk."""
    try:
        with open(UPLOAD_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        return True
    except Exception as e:
        print(f"[Upload Config Error] Failed to save config: {e}")
        return False


# =========================================================================
# YOUTUBE DATA API V3 (OAuth2 Flow & Resumable Video Upload)
# =========================================================================

def get_youtube_credentials(config: Optional[Dict[str, Any]] = None):
    """Retrieves valid YouTube OAuth2 credentials, refreshing expired tokens if possible."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    if not config:
        config = load_upload_config()

    token_path = Path(config.get("youtube", {}).get("token_file", str(DEFAULT_YOUTUBE_TOKEN)))
    if not token_path.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
        if creds and creds.valid:
            return creds
    except Exception as e:
        print(f"[YouTube Auth Error] Invalid or expired credentials: {e}")

    return None


def get_youtube_auth_url(redirect_uri: str = DEFAULT_LOOPBACK_REDIRECT) -> Dict[str, Any]:
    """
    Generates the Google OAuth authorization URL for the YouTube Data API.

    Uses the loopback redirect (NOT the retired OOB flow) so the user can authorize via the
    manual copy-paste fallback in Settings.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return {"success": False, "error": "google_auth_oauthlib package is missing."}

    config = load_upload_config()
    secrets_path = Path(config.get("youtube", {}).get("client_secrets_file", str(DEFAULT_CLIENT_SECRETS)))

    if not secrets_path.exists():
        return {
            "success": False,
            "error": f"client_secrets.json not found at {secrets_path}. Download your OAuth client secret from Google Cloud Console."
        }

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), YOUTUBE_SCOPES, redirect_uri=redirect_uri)
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return {"success": True, "auth_url": auth_url, "redirect_uri": redirect_uri}
    except Exception as e:
        return {"success": False, "error": str(e)}


def start_youtube_local_auth(open_browser: bool = True) -> Dict[str, Any]:
    """
    Runs Google's modern loopback OAuth flow using run_local_server.
    Opens the default browser, waits for user approval, exchanges code, and saves credentials.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        return {"success": False, "error": "Google API packages missing."}

    config = load_upload_config()
    secrets_path = Path(config.get("youtube", {}).get("client_secrets_file", str(DEFAULT_CLIENT_SECRETS)))
    token_path = Path(config.get("youtube", {}).get("token_file", str(DEFAULT_YOUTUBE_TOKEN)))

    if not secrets_path.exists():
        return {"success": False, "error": f"client_secrets.json not found at {secrets_path}"}

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), YOUTUBE_SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", open_browser=open_browser)
        
        # Save token
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        # Fetch channel information
        yt = build("youtube", "v3", credentials=creds)
        ch_resp = yt.channels().list(part="snippet", mine=True).execute()
        ch_title = "My YouTube Channel"
        ch_id = ""
        if ch_resp.get("items"):
            item = ch_resp["items"][0]
            ch_title = item.get("snippet", {}).get("title", "My YouTube Channel")
            ch_id = item.get("id", "")

        # Update upload_config.json
        config["youtube"]["is_authenticated"] = True
        config["youtube"]["channel_title"] = ch_title
        config["youtube"]["channel_id"] = ch_id
        save_upload_config(config)

        return {
            "success": True,
            "channel_title": ch_title,
            "channel_id": ch_id
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def exchange_youtube_code(auth_code: str, redirect_uri: str = DEFAULT_LOOPBACK_REDIRECT) -> Dict[str, Any]:
    """
    Exchanges a manually-copied authorization code for persistent YouTube credentials.
    Must use the same loopback redirect_uri that generated the auth URL.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        return {"success": False, "error": "Google API packages missing."}

    config = load_upload_config()
    secrets_path = Path(config.get("youtube", {}).get("client_secrets_file", str(DEFAULT_CLIENT_SECRETS)))
    token_path = Path(config.get("youtube", {}).get("token_file", str(DEFAULT_YOUTUBE_TOKEN)))

    if not secrets_path.exists():
        return {"success": False, "error": f"client_secrets.json missing at {secrets_path}"}

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), YOUTUBE_SCOPES, redirect_uri=redirect_uri)
        flow.fetch_token(code=auth_code.strip())
        creds = flow.credentials

        # Save token
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        # Fetch channel information
        yt = build("youtube", "v3", credentials=creds)
        ch_resp = yt.channels().list(part="snippet", mine=True).execute()
        ch_title = "Unknown Channel"
        ch_id = ""
        if ch_resp.get("items"):
            item = ch_resp["items"][0]
            ch_title = item.get("snippet", {}).get("title", "My YouTube Channel")
            ch_id = item.get("id", "")

        config["youtube"]["is_authenticated"] = True
        config["youtube"]["channel_title"] = ch_title
        config["youtube"]["channel_id"] = ch_id
        save_upload_config(config)

        return {
            "success": True,
            "channel_title": ch_title,
            "channel_id": ch_id
        }
    except Exception as e:
        return {"success": False, "error": f"Authentication exchange failed: {e}"}


def upload_video_to_youtube(
    video_path: Path,
    title: str,
    description: str,
    tags: List[str],
    privacy_status: str = "public",
    category_id: str = "22"
) -> Dict[str, Any]:
    """
    Performs standard resumable upload of vertical MP4 video to YouTube Data API v3.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
    except ImportError:
        return {"success": False, "error": "google-api-python-client is not installed."}

    if not video_path.exists():
        return {"success": False, "error": f"Video file not found: {video_path}"}

    creds = get_youtube_credentials()
    if not creds:
        return {
            "success": False,
            "error": "YouTube is not authenticated. Please authenticate YouTube in Settings."
        }

    try:
        youtube = build("youtube", "v3", credentials=creds)

        # YouTube Shorts title max 100 chars
        clean_title = title[:100] if len(title) > 100 else title
        clean_tags = [t.lstrip("#") for t in tags][:15]

        body = {
            "snippet": {
                "title": clean_title,
                "description": description,
                "tags": clean_tags,
                "categoryId": category_id
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=1024*1024*4)
        insert_req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        print(f"  [YouTube Upload] Starting upload for '{clean_title}' ({privacy_status})...")
        response = None
        while response is None:
            status, response = insert_req.next_chunk()
            if status:
                progress_pct = int(status.progress() * 100)
                print(f"  [YouTube Upload] Progress: {progress_pct}%")

        video_id = response.get("id")
        short_url = f"https://youtube.com/shorts/{video_id}"
        print(f"  [YouTube Upload Success] Published to YouTube Shorts: {short_url}")

        return {
            "success": True,
            "video_id": video_id,
            "url": short_url,
            "platform": "youtube",
            "privacy": privacy_status
        }
    except HttpError as e:
        error_details = str(e)
        try:
            err_json = json.loads(e.content.decode("utf-8"))
            error_details = err_json.get("error", {}).get("message", error_details)
        except Exception:
            pass
        return {"success": False, "error": f"YouTube API Error: {error_details}"}
    except Exception as e:
        return {"success": False, "error": f"Upload failed: {str(e)}"}


# =========================================================================
# MULTI-PLATFORM SOCIAL API (Ayrshare: TikTok, Instagram Reels, YouTube)
# =========================================================================

def upload_to_social_platforms(
    video_path: Path,
    caption: str,
    platforms: List[str],
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Uploads vertical video directly to TikTok, Instagram Reels, and other platforms
    via Ayrshare Social Posting API.
    """
    if not video_path.exists():
        return {"success": False, "error": f"Video file not found: {video_path}"}

    if not api_key:
        cfg = load_upload_config()
        api_key = cfg.get("ayrshare", {}).get("api_key", "").strip()

    if not api_key:
        return {
            "success": False,
            "error": "Ayrshare API Key is missing. Configure Ayrshare in Settings to post to TikTok and Instagram."
        }

    valid_platforms = [p.lower() for p in platforms if p.lower() in {"tiktok", "instagram", "youtube", "facebook", "twitter"}]
    if not valid_platforms:
        return {"success": False, "error": "No valid platforms selected."}

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    try:
        # Step 1: Upload media file to Ayrshare media storage
        print(f"  [Social API] Uploading media file '{video_path.name}' to Ayrshare...")
        with open(video_path, "rb") as vf:
            files = {"file": (video_path.name, vf, "video/mp4")}
            upload_res = requests.post(AYRSHARE_MEDIA_URL, headers=headers, files=files, timeout=60)

        if upload_res.status_code != 200:
            return {"success": False, "error": f"Media upload failed ({upload_res.status_code}): {upload_res.text}"}

        media_url = upload_res.json().get("url")
        if not media_url:
            return {"success": False, "error": "Ayrshare did not return a valid media URL."}

        # Step 2: Post to targeted platforms
        payload = {
            "post": caption,
            "platforms": valid_platforms,
            "mediaUrls": [media_url]
        }

        # Platform-specific configurations
        if "instagram" in valid_platforms:
            payload["instagramOptions"] = {
                "reels": True,
                "shareToFeed": True
            }
        if "tiktok" in valid_platforms:
            payload["tiktokOptions"] = {
                "privacy": "PUBLIC_TO_EVERYONE",
                "disableComments": False,
                "disableDuet": False,
                "disableStitch": False
            }

        print(f"  [Social API] Publishing to {valid_platforms}...")
        post_res = requests.post(AYRSHARE_POST_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=40)

        if post_res.status_code == 200:
            resp_data = post_res.json()
            post_id = resp_data.get("id")
            post_urls = resp_data.get("postUrl", {})
            return {
                "success": True,
                "post_id": post_id,
                "platforms": valid_platforms,
                "urls": post_urls,
                "response": resp_data
            }
        else:
            return {
                "success": False,
                "error": f"Social API error ({post_res.status_code}): {post_res.text}"
            }
    except Exception as e:
        return {"success": False, "error": f"Social post failed: {str(e)}"}


# =========================================================================
# UNIFIED MULTI-PLATFORM DISPATCHER
# =========================================================================

def upload_clip_to_platforms(
    clip_filename: str,
    title: str,
    description: str,
    hashtags: List[str],
    platforms: List[str],
    privacy_status: str = "public"
) -> Dict[str, Any]:
    """
    Coordinates simultaneous or targeted upload across YouTube, TikTok, and Instagram,
    and updates the clip's metadata JSON file with upload receipts.
    """
    video_path = OUTPUT_DIR / clip_filename
    if not video_path.exists():
        return {"success": False, "error": f"Clip file not found: {clip_filename}"}

    results = {}
    cfg = load_upload_config()
    social_platforms = [p for p in platforms if p in {"tiktok", "instagram"}]

    # 1. YouTube Upload
    if "youtube" in platforms:
        if cfg.get("youtube", {}).get("is_authenticated", False):
            # Direct Native YouTube Data API v3
            yt_res = upload_video_to_youtube(video_path, title, description, hashtags, privacy_status=privacy_status)
            results["youtube"] = yt_res
        elif cfg.get("ayrshare", {}).get("is_configured", False) and str(cfg.get("ayrshare", {}).get("api_key", "") or "").strip():
            # Route YouTube through Ayrshare if native credentials not configured.
            # Also require a non-empty key so a stale/cleared is_configured flag can never
            # silently divert native YouTube uploads to Ayrshare.
            social_platforms.append("youtube")
        else:
            results["youtube"] = {
                "success": False,
                "error": "YouTube not authenticated. Add client_secrets.json or Ayrshare key in Settings."
            }

    # 2. Social Platforms (TikTok, Instagram)
    if social_platforms:
        ayr_key = cfg.get("ayrshare", {}).get("api_key", "")
        caption = f"{title}\n\n{' '.join(hashtags)}"
        social_res = upload_to_social_platforms(video_path, caption, social_platforms, api_key=ayr_key)
        for p in social_platforms:
            results[p] = social_res

    # 3. Update clip metadata JSON
    meta_json = OUTPUT_DIR / clip_filename.replace(".mp4", ".json")
    if meta_json.exists():
        try:
            with open(meta_json, "r", encoding="utf-8") as f:
                meta = json.load(f)
            
            upload_history = meta.get("upload_history", [])
            upload_history.append({
                "timestamp": time.time(),
                "platforms": platforms,
                "title": title,
                "privacy": privacy_status,
                "results": results
            })
            meta["upload_history"] = upload_history
            meta["last_uploaded"] = time.time()
            
            with open(meta_json, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            print(f"[Upload Metadata Error] Failed to update {meta_json.name}: {e}")

    overall_success = any(r.get("success", False) for r in results.values())
    err_msgs = [f"{p.title()}: {r.get('error', 'Failed')}" for p, r in results.items() if not r.get("success", False)]
    err_summary = "; ".join(err_msgs) if err_msgs else "No platforms could be reached."
    return {
        "success": overall_success,
        "error": None if overall_success else err_summary,
        "results": results
    }
