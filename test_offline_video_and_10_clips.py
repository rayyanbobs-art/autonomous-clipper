import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import clipper
import app
import video_cutter
import scorer


class TestOfflineVideoAndTenClips(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_is_local_video_and_extract_video_id(self):
        """Verify local file detection and deterministic 11-character video ID generation."""
        dummy_video = self.tmp_path / "sample_podcast.mp4"
        dummy_video.write_bytes(b"fake_mp4_bytes")

        # Local file should be detected
        self.assertTrue(clipper.is_local_video(str(dummy_video)))
        self.assertTrue(app.is_local_video(str(dummy_video)))

        # Web / YouTube URLs should not be detected as local
        self.assertFalse(clipper.is_local_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertFalse(clipper.is_local_video("dQw4w9WgXcQ"))
        self.assertFalse(clipper.is_local_video(str(self.tmp_path / "nonexistent.mp4")))

        # Local video ID format: exactly 11 characters matching ^[0-9A-Za-z_-]{11}$
        local_id = clipper.extract_video_id(str(dummy_video))
        self.assertEqual(len(local_id), 11)
        self.assertTrue(local_id.startswith("loc_"))

        # Deterministic: same file produces identical ID
        self.assertEqual(local_id, clipper.extract_video_id(str(dummy_video)))

    def test_02_get_local_transcript_caching(self):
        """Verify local transcript transcribes and caches to temp json."""
        dummy_video = self.tmp_path / "interview.mp4"
        dummy_video.write_bytes(b"fake_video")

        mock_segment = MagicMock()
        mock_segment.text = " Welcome to the deep dive on systems architecture. "
        mock_segment.start = 0.5
        mock_segment.end = 4.8

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], None)

        with patch("subtitles.get_whisper_model", return_value=mock_model):
            snippets = clipper.get_local_transcript(dummy_video, video_id="loc_test123")
            self.assertEqual(len(snippets), 1)
            self.assertEqual(snippets[0]["text"], "Welcome to the deep dive on systems architecture.")
            self.assertEqual(snippets[0]["start"], 0.5)
            self.assertEqual(snippets[0]["duration"], 4.3)

            # Check that cached file was written
            cache_file = clipper.TEMP_DIR / "loc_test123_transcript.json"
            self.assertTrue(cache_file.exists())

            # Second call should read from cache without invoking Whisper model
            mock_model.transcribe.reset_mock()
            cached_snippets = clipper.get_local_transcript(dummy_video, video_id="loc_test123")
            self.assertEqual(cached_snippets, snippets)
            mock_model.transcribe.assert_not_called()

            # Cleanup cache
            try:
                cache_file.unlink()
            except Exception:
                pass

    def test_03_cut_and_format_clip_protects_source_file(self):
        """Verify that slicing an offline video slices into temp and never deletes the source video."""
        source_video = self.tmp_path / "my_source_footage.mp4"
        source_video.write_bytes(b"ORIGINAL_FOOTAGE_HEADER")
        out_clip = self.tmp_path / "output_short.mp4"

        captured_commands = []

        def fake_run(cmd, *args, **kwargs):
            captured_commands.append(cmd)
            for item in cmd:
                s_item = str(item)
                if s_item.endswith(".mp4") and s_item != str(source_video):
                    Path(s_item).write_bytes(b"DUMMY_MP4_CONTENT")
            out_clip.write_bytes(b"RENDERED_CLIP")
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        with patch("subprocess.run", side_effect=fake_run), \
             patch("video_cutter.get_ffmpeg_path", return_value="ffmpeg"), \
             patch("video_cutter.get_video_duration", return_value=30.0), \
             patch("video_cutter.has_audio_stream", return_value=True), \
             patch("video_cutter.compute_smart_crop_offset", return_value=(608, 1080, "656", 0)), \
             patch("video_cutter.generate_synced_subtitles", return_value=False):

            success = video_cutter.cut_and_format_clip(
                youtube_url=str(source_video),
                start_sec=10.0,
                end_sec=45.0,
                output_path=out_clip,
                subtitle_style="none",
                enable_broll=False,
                enable_snappy_cuts=False,
                enable_outro=False,
                enable_bg_music=False
            )

            self.assertTrue(success)
            self.assertTrue(out_clip.exists())
            # Invariant: source video MUST still exist and remain uncorrupted!
            self.assertTrue(source_video.exists(), "Source video must not be deleted by cutter!")
            self.assertEqual(source_video.read_bytes(), b"ORIGINAL_FOOTAGE_HEADER")

            # Check that an initial ffmpeg slice command was issued with -ss and -t
            slice_cmds = [c for c in captured_commands if "-ss" in c and str(source_video) in c]
            self.assertEqual(len(slice_cmds), 1)
            self.assertIn("-ss", slice_cmds[0])
            self.assertIn("10.000", slice_cmds[0])

    def test_04_ten_clips_scaling_and_critique_gate(self):
        """Verify candidate window generation and critique gate scaling for 10 clips."""
        # 1200 seconds transcript (20 minutes)
        transcript = []
        for i in range(120):
            start = float(i * 10)
            transcript.append({
                "start": start,
                "duration": 10.0,
                "text": f"Segment number {i} with high impact discussions on technology."
            })

        windows = scorer.create_windows(transcript, min_duration=35.0, max_duration=55.0, step=20.0)
        self.assertGreater(len(windows), 40)

        # Candidate sampling when top_k = 10
        top_k = 10
        effective_candidates = max(15, top_k * 4)  # 40 candidates
        self.assertEqual(effective_candidates, 40)

        # Run critique gate with target_clips = 10
        scored_candidates = [
            dict(w, virality_score=2.0 + (idx % 5) * 0.2, standalone_prob=0.8, sponsor_prob=0.0)
            for idx, w in enumerate(windows[:40])
        ]
        top_clips = scorer.critique_gate_search(
            candidate_windows=scored_candidates,
            target_clips=10,
            threshold=5.0,
            max_attempts=3,
            enable_sponsor_killer=True
        )
        # Should produce up to 10 non-overlapping clips
        self.assertGreaterEqual(len(top_clips), 5)
        self.assertLessEqual(len(top_clips), 10)

    def test_05_api_suggest_ranges_and_browse_file(self):
        """Verify Flask API endpoints handle offline video files."""
        dummy_video = self.tmp_path / "offline_vid.mp4"
        dummy_video.write_bytes(b"offline_data")

        client = app.app.test_client()

        with patch("app.get_video_duration", return_value=300.0):
            res = client.post("/api/video/suggest-ranges", json={"url": str(dummy_video)})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("total_seconds"), 300.0)
            self.assertIn("suggestions", data)

        # Browse file endpoint response structure
        with patch("tkinter.filedialog.askopenfilename", return_value=str(dummy_video)), \
             patch("tkinter.Tk"):
            res_browse = client.post("/api/browse-local-file")
            self.assertEqual(res_browse.status_code, 200)
            data_browse = res_browse.get_json()
            self.assertTrue(data_browse.get("success"))
            self.assertEqual(data_browse.get("file_path"), str(dummy_video))


if __name__ == "__main__":
    unittest.main()
