import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import subprocess
import cv2
import numpy as np

import face_tracker
import video_cutter
from video_cutter import cut_and_format_clip, get_video_duration, has_audio_stream
from config import TEMP_DIR, TARGET_WIDTH, TARGET_HEIGHT

SAMPLE_VIDEO = Path(__file__).parent / "temp" / "test_debug_224_280.mp4"

class TestCinematicFramingAndMusic(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_01_compute_smart_crop_offset_persists_face_and_headroom(self):
        """Verify smart crop calculates valid 9:16 crop dimensions and non-zero framing."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")

        crop_res = face_tracker.compute_smart_crop_offset(SAMPLE_VIDEO, TARGET_WIDTH, TARGET_HEIGHT)
        self.assertIsInstance(crop_res, tuple)
        self.assertEqual(len(crop_res), 4)
        crop_w, crop_h, crop_x_expr, crop_y = crop_res

        self.assertGreater(crop_w, 0)
        self.assertGreater(crop_h, 0)
        self.assertEqual(crop_w % 2, 0)
        self.assertEqual(crop_h % 2, 0)
        # Ensure 9:16 proportion
        self.assertAlmostEqual(crop_w / crop_h, 9 / 16, delta=0.05)
        self.assertIsInstance(crop_x_expr, str)
        self.assertGreaterEqual(crop_y, 0)

    def test_02_detect_scene_cuts_fast_and_accurate(self):
        """Verify fast scene cut detector produces boundary cuts."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")

        cap = cv2.VideoCapture(str(SAMPLE_VIDEO))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            dur = total_frames / fps
            cuts = face_tracker.detect_scene_cuts(cap, fps, total_frames, dur)
            self.assertIsInstance(cuts, list)
            self.assertGreaterEqual(len(cuts), 2)
            self.assertEqual(cuts[0], 0.0)
            self.assertAlmostEqual(cuts[-1], round(dur, 2), delta=0.5)
        finally:
            cap.release()

    def test_03_ambient_audio_asset_exists_and_valid(self):
        """Verify the royalty-free ambient adventure soundtrack is present and valid."""
        audio_dir = Path(__file__).parent / "assets" / "audio"
        if not audio_dir.exists():
            self.skipTest("Optional ambient audio asset directory not present.")
        tracks = list(audio_dir.glob("*.wav")) + list(audio_dir.glob("*.mp3"))
        if not tracks:
            self.skipTest("Optional ambient audio track not present in assets/audio.")
        self.assertGreaterEqual(len(tracks), 1, "At least one ambient audio track must exist.")
        track = tracks[0]
        self.assertGreater(track.stat().st_size, 1000)

    def test_04_slow_zoom_and_bgm_filtergraph_construction(self):
        """Verify slow zoom and background music filters are injected into the FFmpeg command."""
        test_out = Path(self.tmp_dir.name) / "test_out.mp4"

        clip_id = "test1234"
        raw_fake = TEMP_DIR / f"raw_0_10_{clip_id}.mp4"

        orig_sp_run = subprocess.run
        def mock_sp_run(cmd, *args, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
            if "yt_dlp" in cmd_str:
                raw_fake.write_bytes(b"dummy")
            if isinstance(cmd, list) and "-f" in cmd and "null" in cmd:
                return orig_sp_run(cmd, *args, **kwargs)
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("subprocess.run", side_effect=mock_sp_run) as mock_run, \
             patch("video_cutter.generate_synced_subtitles", return_value=False), \
             patch("video_cutter.has_audio_stream", return_value=True), \
             patch("video_cutter.get_video_duration", return_value=10.0), \
             patch("uuid.uuid4") as mock_uuid:

            uuid_mock = MagicMock()
            uuid_mock.hex = clip_id
            mock_uuid.return_value = uuid_mock

            try:
                ok = cut_and_format_clip(
                    youtube_url="https://youtube.com/watch?v=fake123",
                    start_sec=0.0,
                    end_sec=10.0,
                    output_path=test_out,
                    framing_mode="smart_face",
                    enable_slow_zoom=True,
                    enable_bg_music=True,
                    enable_broll=False,
                    enable_outro=False,
                    enable_snappy_cuts=False,
                    enable_punch_zooms=False
                )

                # Inspect FFmpeg render command line passed to subprocess.run
                render_calls = [c for c in mock_run.call_args_list if isinstance(c[0][0], list) and "-filter_complex" in c[0][0]]
                self.assertGreater(len(render_calls), 0)
                call_args = render_calls[0][0][0]
                filter_complex_idx = call_args.index("-filter_complex") + 1
                fc = call_args[filter_complex_idx]

                # Assert zoompan is present in filter complex with face anchor and drift
                self.assertIn("zoompan=", fc)
                self.assertIn("ih*0.30-(ih/zoom*0.30)", fc)
                self.assertIn("sin(on/", fc)

                # Assert background music amix is present
                self.assertIn("amix=inputs=2", fc)
                self.assertIn("afade=t=in", fc)
                self.assertIn("afade=t=out", fc)
            finally:
                video_cutter._BEST_ENCODER_INFO = None
                if raw_fake.exists():
                    raw_fake.unlink()

    def test_05_live_render_with_slow_zoom_and_bgm(self):
        """End-to-end live render on a short 4-second section to verify output video and audio streams."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")

        ffmpeg_exe = video_cutter.get_ffmpeg_path()
        out_clip = Path(self.tmp_dir.name) / "cinematic_short.mp4"

        # Slice 4 seconds directly using ffmpeg
        slice_tmp = Path(self.tmp_dir.name) / "slice_4s.mp4"
        cmd_slice = [
            ffmpeg_exe, "-y", "-ss", "2.0", "-t", "4.0",
            "-i", str(SAMPLE_VIDEO),
            "-c:v", "libx264", "-c:a", "aac",
            str(slice_tmp)
        ]
        import subprocess
        res = subprocess.run(cmd_slice, capture_output=True)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(slice_tmp.exists())

        clip_id = "testcinematic"
        raw_target = TEMP_DIR / f"raw_0_4_{clip_id}.mp4"
        import shutil
        shutil.copy(str(slice_tmp), str(raw_target))

        real_sp_run = subprocess.run

        def custom_sp_run(cmd, *args, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
            if "yt_dlp" in cmd_str:
                m = MagicMock()
                m.returncode = 0
                return m
            return real_sp_run(cmd, *args, **kwargs)

        try:
            with patch("subprocess.run", side_effect=custom_sp_run), \
                 patch("uuid.uuid4") as mock_uuid:
                uuid_mock = MagicMock()
                uuid_mock.hex = clip_id
                mock_uuid.return_value = uuid_mock

                ok = cut_and_format_clip(
                    youtube_url="https://youtube.com/watch?v=fake",
                    start_sec=0.0,
                    end_sec=4.0,
                    output_path=out_clip,
                    framing_mode="smart_face",
                    enable_slow_zoom=True,
                    enable_bg_music=True,
                    bg_music_volume=0.12,
                    enable_broll=False,
                    enable_outro=False,
                    enable_snappy_cuts=False,
                    enable_punch_zooms=False
                )

                self.assertTrue(ok)
                self.assertTrue(out_clip.exists())
                dur = get_video_duration(out_clip, ffmpeg_exe)
                self.assertAlmostEqual(dur, 4.0, delta=0.5)
                self.assertTrue(has_audio_stream(out_clip, ffmpeg_exe))

                # Check output dimensions are 1080x1920
                cap_out = cv2.VideoCapture(str(out_clip))
                w = int(cap_out.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap_out.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap_out.release()
                self.assertEqual(w, TARGET_WIDTH)
                self.assertEqual(h, TARGET_HEIGHT)
        finally:
            if raw_target.exists():
                raw_target.unlink()

    def test_06_wild_den_subtitle_style_and_formatting(self):
        """Verify the dedicated Wild Den viral outdoor subtitle style preset and ASS output."""
        import subtitles
        self.assertIn("wild_den", subtitles.SUBTITLE_STYLES)
        cfg = subtitles.SUBTITLE_STYLES["wild_den"]
        self.assertEqual(cfg["font"], "Impact")
        self.assertTrue(cfg.get("italic"))
        self.assertTrue(cfg.get("bold"))
        self.assertEqual(cfg.get("margin_v"), 930)
        self.assertEqual(cfg.get("max_words_per_line"), 2)
        self.assertTrue(cfg.get("uppercase"))

        sample_words = [
            {"word": "entire", "start": 1.0, "end": 1.4},
            {"word": "thing", "start": 1.45, "end": 1.8},
            {"word": "is", "start": 2.0, "end": 2.2},
            {"word": "frozen", "start": 2.25, "end": 2.7}
        ]
        ass_str = subtitles.build_ass_from_words(sample_words, style_key="wild_den")
        # Check header contains italic=1, bold=1, margin_v=930
        self.assertIn("Style: Default,Impact,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,1,1,0,0,100,100,0,0,1,6,3,2,60,60,930,1", ass_str)
        # Uppercase 2-word chunks; active word highlighted #FFE600 (ASS &H0000E6FF), rest white
        self.assertIn("{\\c&H0000E6FF&}ENTIRE{\\c&H00FFFFFF&} THING", ass_str)
        self.assertIn("ENTIRE {\\c&H0000E6FF&}THING{\\c&H00FFFFFF&}", ass_str)
        self.assertIn("{\\c&H0000E6FF&}IS{\\c&H00FFFFFF&} FROZEN", ass_str)
        self.assertIn("IS {\\c&H0000E6FF&}FROZEN{\\c&H00FFFFFF&}", ass_str)
        # Emojis are opt-in
        self.assertNotRegex(ass_str, "[\U0001F300-\U0001FAFF]")

if __name__ == "__main__":
    unittest.main()
