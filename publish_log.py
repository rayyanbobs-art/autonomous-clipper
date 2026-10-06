"""
publish_log.py -- append-only record of every title this pipeline ships.

WHY THIS EXISTS. OpusClip closes the loop: published titles feed back into what the
system believes works, which is what makes its Trend dimension and its ranking more
than guesses. This codebase had no feedback loop at all -- nothing recorded what was
published, so every retune worked from memory. This log is the first half of that loop:
an append-only JSONL record of every shipped title, written at the four sites where a
fresh `smart_meta` replaces a clip's title. The second half (setting TRENDING_BOOST
from measured view/CTR data) is a documented procedure on an honest empty map, not
code that pretends the data already exists.

WHAT IS LOGGED. The title that shipped, the short variant, the winning pattern and
keyword, the niche, the hook, how many candidates competed, the clip's virality
scores, and where it came from (clipper / app / batch_rerender / backfill_titles).
Deliberately NOT logged: transcripts, API keys, file bytes, anything credential-shaped.

WHERE IT LIVES. `logs/publish_log.jsonl`, one JSON object per line. `logs/` is
gitignored: the log is operational data, not source, and it must never land in
`output/`, whose `*.json` files are the measured corpus the quality budget reads --
a log line there would be read as a clip.

HERMETICITY. The orchestrator (`generate_smart_title_and_hashtags`) does NOT log by
itself: it runs hundreds of times inside the test suite, and a function that writes to
disk on every call is untestable. Only the four production call sites log, explicitly,
after the title is decided -- and logging never breaks a job: an IO failure prints a
warning and returns None.
"""

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_LOG_PATH = Path(__file__).resolve().parent / "logs" / "publish_log.jsonl"

# Test suites drive the production clip/app/rerender paths with fixture transcripts
# ("sodium broth" videos that never existed). Without redirection those runs append
# FAKE records to the production log -- the exact data-poisoning this log exists to
# prevent, since TRENDING_BOOST will one day be set from it. The path therefore
# resolves at CALL time through this function, and every suite that drives a logging
# path sets CLIPPER_PUBLISH_LOG to a tmp dir in setUpModule (see test_publish_log's
# hermeticity test, which pins this contract from the other side).
LOG_PATH_ENV_VAR = "CLIPPER_PUBLISH_LOG"


def default_log_path() -> Path:
    """Production log path, unless CLIPPER_PUBLISH_LOG redirects (tests)."""
    override = os.environ.get(LOG_PATH_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return DEFAULT_LOG_PATH


def build_publication_record(
    *,
    source: str,
    video_id: str = "",
    filename: str = "",
    smart_meta: Optional[Dict[str, Any]] = None,
    virality_score: Optional[float] = None,
    standalone_probability: Optional[float] = None,
    sponsor_probability: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Assembles one log record from a decided `smart_meta`. Pure: no IO. The timestamp
    is always "now" -- tests that need determinism assert on the other fields.
    """
    meta = smart_meta if isinstance(smart_meta, dict) else {}
    raw_candidates = meta.get("candidates") or []
    candidates = [c for c in raw_candidates if isinstance(c, dict)]
    winning_keyword = ""
    winning_pattern = ""
    winning_framework = str(meta.get("winning_framework") or "")
    for candidate in candidates:
        if candidate.get("title") == meta.get("suggested_title"):
            winning_keyword = str(candidate.get("keyword") or "")
            winning_pattern = str(candidate.get("pattern_id") or "")
            break
    return {
        "event": "title_decided",
        "decided_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": source,
        "video_id": video_id,
        "filename": filename,
        "suggested_title": meta.get("suggested_title", ""),
        "short_title": meta.get("short_title", ""),
        "winning_framework": winning_framework,
        "winning_pattern": winning_pattern,
        "winning_keyword": winning_keyword,
        "niche": meta.get("niche", ""),
        "hook_text": meta.get("hook_text", ""),
        "candidate_count": len(candidates),
        "virality_score": virality_score,
        "standalone_probability": standalone_probability,
        "sponsor_probability": sponsor_probability,
    }


def log_publication(log_path: Any, record: Dict[str, Any]) -> Optional[Path]:
    """
    Appends one record as a JSON line, creating parent directories. Returns the path
    on success. On IO failure prints a warning and returns None: a render job that
    ran for minutes must never die because a log line could not be written. Callers
    must not branch on the return value for correctness -- it is observability only.
    """
    try:
        # Serialize BEFORE touching the filesystem: a record that fails json.dumps
        # must leave no trace, not even an empty file.
        line = json.dumps(record, ensure_ascii=False) + "\n"
        path = Path(log_path)
        if path.parent and str(path.parent):
            path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return path
    except Exception as exc:
        # Exception, not OSError: a record that fails json.dumps (TypeError on an
        # unexpected meta shape) must degrade the same way as an IO failure. The
        # logger sits outside every try in the call sites, so anything propagating
        # here kills a finished job over observability.
        print(f"[publish_log] WARNING: could not append to {log_path} ({exc})",
              flush=True)
        return None


def read_publications(log_path: Any) -> List[Dict[str, Any]]:
    """
    Reads back the log, skipping blank lines and truncated/corrupt lines. A log that
    aborts the reader on one bad line is a log that loses everything after a crash --
    exactly when you need it most. Missing file reads as empty, not as an error.
    """
    path = Path(log_path)
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    # encoding must match the writer: without it, emoji titles mojibake on read
    # under Windows codepages, corrupting exactly the field the log exists to keep.
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records
