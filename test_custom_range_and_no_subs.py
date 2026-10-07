import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import json

import scorer
import subtitles
import video_cutter
import app
import clipper

class TestCustomRangeAndNoSubs(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.addCleanup(self.tmp_dir.cleanup)

    def test_01_parse_timestamp_to_seconds(self):
        """Verify parse_timestamp_to_seconds handles MM:SS, HH:MM:SS, numbers, None, and invalid inputs."""
        # MM:SS
        self.assertEqual(scorer.parse_timestamp_to_seconds("10:00"), 600.0)
        self.assertEqual(scorer.parse_timestamp_to_seconds("00:45"), 45.0)
        self.assertEqual(scorer.parse_timestamp_to_seconds("21:00"), 1260.0)

        # HH:MM:SS
        self.assertEqual(scorer.parse_timestamp_to_seconds("01:05:30"), 3930.0)
        self.assertEqual(scorer.parse_timestamp_to_seconds("1:00:00"), 3600.0)

        # Raw numbers
        self.assertEqual(scorer.parse_timestamp_to_seconds("45"), 45.0)
        self.assertEqual(scorer.parse_timestamp_to_seconds("600"), 600.0)
        self.assertEqual(scorer.parse_timestamp_to_seconds(120), 120.0)
        self.assertEqual(scorer.parse_timestamp_to_seconds(45.5), 45.5)

        # Empty / None / Invalid
        self.assertIsNone(scorer.parse_timestamp_to_seconds(None))
        self.assertIsNone(scorer.parse_timestamp_to_seconds(""))
        self.assertIsNone(scorer.parse_timestamp_to_seconds("   "))
        self.assertIsNone(scorer.parse_timestamp_to_seconds("invalid"))
        self.assertIsNone(scorer.parse_timestamp_to_seconds("-10:00"))
        self.assertIsNone(scorer.parse_timestamp_to_seconds("-45"))

    def test_02_no_subtitles_preset_and_filtergraph_bypass(self):
        """Verify 'none' style in SUBTITLE_STYLES and that subtitles filter is absent in filter complex."""
        # 1. Preset presence
        self.assertIn("none", subtitles.SUBTITLE_STYLES)
        self.assertEqual(subtitles.SUBTITLE_STYLES["none"]["name"], "No Subtitles")

        # 2. generate_synced_subtitles bypass
        dummy_vid = self.tmp_path / "vid.mp4"
        dummy_vid.write_bytes(b"dummy")
        dummy_ass = self.tmp_path / "subs.ass"
        res = subtitles.generate_synced_subtitles(dummy_vid, dummy_ass, style_key="none")
        self.assertFalse(res)
        self.assertFalse(dummy_ass.exists())

        # 3. cut_and_format_clip filtergraph check
        captured_cmd = []
        def fake_run(cmd, *args, **kwargs):
            captured_cmd.append(cmd)
            for item in cmd:
                s_item = str(item)
                if s_item.endswith(".mp4") and s_item != str(dummy_vid):
                    Path(s_item).write_bytes(b"mock_mp4_output")
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("subprocess.run", side_effect=fake_run), \
             patch("video_cutter.get_video_duration", return_value=40.0), \
             patch("video_cutter.has_audio_stream", return_value=True), \
             patch("video_cutter.compute_smart_crop_offset", return_value=(608, 1080, "656", 0)):

            out_mp4 = self.tmp_path / "output_clip.mp4"
            success = video_cutter.cut_and_format_clip(
                youtube_url=str(dummy_vid),
                start_sec=10.0,
                end_sec=50.0,
                output_path=out_mp4,
                subtitle_style="none",
                enable_snappy_cuts=False,
                enable_broll=False,
                enable_outro=False,
                enable_bg_music=False
            )

            self.assertTrue(success)
            self.assertTrue(out_mp4.exists())
            self.assertTrue(len(captured_cmd) > 0)

            # Find the main ffmpeg render command
            render_cmds = [c for c in captured_cmd if "-filter_complex" in c]
            self.assertTrue(len(render_cmds) > 0)
            fc_idx = render_cmds[0].index("-filter_complex")
            filter_complex = render_cmds[0][fc_idx + 1]

            # Invariant: subtitles filter MUST be absent, clean/main split MUST pass through to [v]
            self.assertNotIn("subtitles=", filter_complex)
            self.assertIn("[v]", filter_complex)

    def test_03_create_windows_time_range_slicing(self):
        """Verify create_windows strictly confines candidate generation to [range_start, range_end]."""
        # Create a mock 20-minute (1200 seconds) transcript
        # 300 snippets of 4 seconds each: 0-4, 4-8, ..., 1196-1200
        transcript = []
        for i in range(300):
            start = float(i * 4)
            transcript.append({
                "start": start,
                "duration": 4.0,
                "text": f"Sentence number {i} discussing survival in the wilderness."
            })

        # Test slicing [600.0, 1200.0] (10 min -> 20 min)
        windows = scorer.create_windows(
            transcript,
            min_duration=35.0,
            max_duration=55.0,
            step=20.0,
            range_start=600.0,
            range_end=1200.0
        )

        self.assertGreater(len(windows), 0)

        # Invariant: Every candidate window MUST satisfy start >= 600.0 and end <= 1200.0
        for w in windows:
            self.assertGreaterEqual(w["start"], 600.0, f"Window start {w['start']} is before range_start 600.0")
            self.assertLessEqual(w["end"], 1200.0, f"Window end {w['end']} is after range_end 1200.0")
            self.assertGreaterEqual(w["duration"], 35.0)
            self.assertLessEqual(w["duration"], 55.0)

        # Assert zero windows before 600.0
        windows_before = [w for w in windows if w["start"] < 600.0]
        self.assertEqual(len(windows_before), 0)

        # Test range where no window can fit (range duration < min_duration)
        narrow_windows = scorer.create_windows(
            transcript,
            min_duration=35.0,
            max_duration=55.0,
            step=20.0,
            range_start=600.0,
            range_end=620.0
        )
        self.assertEqual(narrow_windows, [])

        # Test out-of-range bounds
        past_windows = scorer.create_windows(
            transcript,
            min_duration=35.0,
            max_duration=55.0,
            step=20.0,
            range_start=1500.0,
            range_end=1800.0
        )
        self.assertEqual(past_windows, [])

    def test_04_suggest_ranges_api_endpoint(self):
        """Verify /api/video/suggest-ranges generates valid chapters for long videos."""
        client = app.app.test_client()

        # Mock transcript representing a 21-minute video (1260 seconds)
        mock_transcript = [
            {"start": 0.0, "duration": 4.0, "text": "Welcome to Alaska."},
            {"start": 1256.0, "duration": 4.0, "text": "Thanks for watching."}
        ]

        with patch("app.extract_video_id", return_value="test_vid_123"), \
             patch("app.get_transcript", return_value=mock_transcript):

            res = client.post("/api/video/suggest-ranges", json={"url": "https://www.youtube.com/watch?v=test_vid_123"})
            self.assertEqual(res.status_code, 200)

            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("total_seconds"), 1260.0)
            self.assertEqual(data.get("formatted_duration"), "21m 00s")

            suggestions = data.get("suggestions", [])
            self.assertGreaterEqual(len(suggestions), 3)

            labels = [s["label"] for s in suggestions]
            self.assertIn("Full Video", labels)
            self.assertIn("Opening / Intro", labels)
            self.assertIn("Middle Section", labels)
            self.assertIn("Climax / Ending", labels)

            # Check time formatting
            full_vid = [s for s in suggestions if s["label"] == "Full Video"][0]
            self.assertEqual(full_vid["start"], "00:00")
            self.assertEqual(full_vid["end"], "21:00")

    def test_05_app_generate_payload_integration(self):
        """Verify /api/generate captures and forwards time_range_start and time_range_end."""
        client = app.app.test_client()

        captured_kwargs = {}
        def fake_thread_start(target, args=(), kwargs=None, daemon=True):
            captured_kwargs.update(kwargs or {})
            m = MagicMock()
            return m

        with patch("threading.Thread") as mock_thread:
            res = client.post("/api/generate", json={
                "url": "https://www.youtube.com/watch?v=test_vid_123",
                "top_k": 3,
                "subtitle_style": "none",
                "time_range_start": "10:00",
                "time_range_end": "21:00"
            })
            self.assertEqual(res.status_code, 200)
            self.assertTrue(mock_thread.called)
            thread_call_kwargs = mock_thread.call_args[1].get("kwargs", {})
            self.assertEqual(thread_call_kwargs.get("time_range_start"), "10:00")
            self.assertEqual(thread_call_kwargs.get("time_range_end"), "21:00")

if __name__ == "__main__":
    unittest.main()
