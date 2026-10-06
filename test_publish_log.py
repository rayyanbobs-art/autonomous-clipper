"""
test_publish_log.py -- tests for the append-only publication log.

The log is the first half of the feedback loop (the second half is TRENDING_BOOST,
populated from measured data). These tests pin: the record shape, winner resolution,
round-trip durability, corrupt-line tolerance, and the never-break-the-job guarantee.
"""

import json
import unittest
from pathlib import Path
import tempfile

from publish_log import (
    build_publication_record,
    log_publication,
    read_publications,
    default_log_path,
    DEFAULT_LOG_PATH,
    LOG_PATH_ENV_VAR,
)


def _meta():
    return {
        "suggested_title": "What Nobody Tells You About Alaska",
        "short_title": "Alaska Explained",
        "winning_framework": "Curiosity Gap",
        "niche": "outdoors_survival",
        "hook_text": "We are out here in Alaska",
        "candidates": [
            {"id": "mp_1", "framework": "Story", "title": "The Story Behind Snow",
             "pattern_id": "survival_story", "keyword": "snow"},
            {"id": "mp_2", "framework": "Curiosity Gap",
             "title": "What Nobody Tells You About Alaska",
             "pattern_id": "nobody_tells_you", "keyword": "alaska"},
        ],
    }


class TestBuildRecord(unittest.TestCase):
    def test_resolves_winner_from_candidates(self):
        record = build_publication_record(
            source="clipper", video_id="vid1", filename="vid1_0.mp4",
            smart_meta=_meta(), virality_score=8.5)
        self.assertEqual(record["event"], "title_decided")
        self.assertEqual(record["winning_keyword"], "alaska")
        self.assertEqual(record["winning_pattern"], "nobody_tells_you")
        self.assertEqual(record["winning_framework"], "Curiosity Gap")
        self.assertEqual(record["candidate_count"], 2)
        self.assertEqual(record["virality_score"], 8.5)
        self.assertEqual(record["source"], "clipper")
        self.assertTrue(record["decided_at"], "record must carry its own timestamp")

    def test_empty_meta_yields_empty_record_not_a_crash(self):
        record = build_publication_record(source="app", smart_meta={})
        self.assertEqual(record["winning_keyword"], "")
        self.assertEqual(record["candidate_count"], 0)

    def test_malformed_shapes_yield_empty_record_not_a_crash(self):
        """
        Test harnesses mock `generate_smart_title_and_hashtags` with shapes the real
        engine never emits (`"candidates": ["c1"]`, a bare string as the whole meta).
        The logger sits inside production call sites those harnesses drive, so it must
        degrade to an empty record rather than break the job under test -- or in
        production, if a future engine change alters the shape.
        """
        record = build_publication_record(
            source="clipper",
            smart_meta={"suggested_title": "T", "candidates": ["c1", 42, None]})
        self.assertEqual(record["winning_keyword"], "")
        self.assertEqual(record["candidate_count"], 0)
        record = build_publication_record(source="clipper", smart_meta="junk")
        self.assertEqual(record["suggested_title"], "")
        record = build_publication_record(source="clipper", smart_meta=None)
        self.assertEqual(record["suggested_title"], "")

    def test_record_carries_no_transcript_or_secrets(self):
        record = build_publication_record(source="app", smart_meta=_meta())
        blob = json.dumps(record).lower()
        self.assertNotIn("transcript", blob)
        self.assertNotIn("api_key", blob)
        self.assertNotIn("token", blob)


class TestLogRoundTrip(unittest.TestCase):
    def test_append_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "publish_log.jsonl"
            record = build_publication_record(source="clipper", smart_meta=_meta())
            self.assertEqual(log_publication(path, record), path)
            self.assertEqual(log_publication(path, record), path)
            back = read_publications(path)
            self.assertEqual(len(back), 2)
            self.assertEqual(back[0]["suggested_title"],
                             "What Nobody Tells You About Alaska")

    def test_missing_file_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(read_publications(Path(d) / "nope.jsonl"), [])

    def test_corrupt_lines_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "publish_log.jsonl"
            path.write_text(
                '{"event": "title_decided", "source": "app"}\n'
                '\n'
                '{"truncated": true,\n'
                'not json at all\n',
                encoding="utf-8")
            back = read_publications(path)
            self.assertEqual(len(back), 1)
            self.assertEqual(back[0]["source"], "app")

    def test_io_failure_returns_none_without_raising(self):
        """
        A render job that ran for minutes must never die because a log line could not
        be written. A path whose parent is an existing FILE cannot be created.
        """
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "blocker"
            blocker.write_text("in the way", encoding="utf-8")
            self.assertIsNone(
                log_publication(blocker / "publish_log.jsonl",
                                build_publication_record(source="app")))

    def test_env_override_redirects_the_log(self):
        """
        The other half of the hermeticity contract: suites that drive production
        paths with fixture transcripts set CLIPPER_PUBLISH_LOG in setUpModule, and
        default_log_path() must honor it at CALL time (not import time), or the
        redirect silently misses and fake records poison the production log.
        """
        import os
        self.assertEqual(default_log_path(), DEFAULT_LOG_PATH)
        with tempfile.TemporaryDirectory() as d:
            target = str(Path(d) / "test_only.jsonl")
            previous = os.environ.get(LOG_PATH_ENV_VAR)
            os.environ[LOG_PATH_ENV_VAR] = target
            try:
                self.assertEqual(default_log_path(), Path(target))
                log_publication(default_log_path(),
                                build_publication_record(source="test"))
                self.assertEqual(len(read_publications(target)), 1)
            finally:
                if previous is None:
                    os.environ.pop(LOG_PATH_ENV_VAR, None)
                else:
                    os.environ[LOG_PATH_ENV_VAR] = previous
            self.assertEqual(default_log_path(), DEFAULT_LOG_PATH)

    def test_unserializable_record_returns_none_without_raising(self):
        """
        The logger sits outside every try in the call sites. A record that fails
        json.dumps (an unexpected meta shape) must degrade exactly like an IO
        failure -- never propagate into a finished job.
        """
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "publish_log.jsonl"
            self.assertIsNone(log_publication(path, {"bad": {1, 2, 3}}))
            self.assertFalse(path.exists(), "a failed append must leave no trace")

    def test_default_path_lives_outside_the_measured_corpus(self):
        """
        The quality budget reads output/*.json as clips. A log line there would be read
        as a clip. The default path must never be under output/.
        """
        self.assertNotIn("output", DEFAULT_LOG_PATH.parts)
        self.assertTrue(str(DEFAULT_LOG_PATH).endswith(".jsonl"))


if __name__ == "__main__":
    unittest.main()
