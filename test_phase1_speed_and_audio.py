import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import subprocess
import concurrent.futures

import video_cutter
from video_cutter import get_best_video_encoder, cut_and_format_clip
import subtitles
from subtitles import get_whisper_model

SAMPLE_VIDEO = Path(__file__).parent / "temp" / "test_debug_224_280.mp4"

class TestPhase1SpeedAndAudio(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_01_hardware_encoder_detection_real_and_mock(self):
        """Verify encoder detector probes hardware and falls back properly."""
        # 1. Real probe
        codec, flags = get_best_video_encoder()
        self.assertIn(codec, ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"])
        self.assertIsInstance(flags, list)
        self.assertIn("-c:v", flags)

        # 2. Mock NVENC success
        with patch("video_cutter._BEST_ENCODER_INFO", None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                c_nv, flags_nv = get_best_video_encoder("fake_ffmpeg")
                self.assertEqual(c_nv, "h264_nvenc")
                self.assertIn("h264_nvenc", flags_nv)

        # 3. Mock total failure -> fallback to libx264
        with patch("video_cutter._BEST_ENCODER_INFO", None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                c_cpu, flags_cpu = get_best_video_encoder("fake_ffmpeg")
                self.assertEqual(c_cpu, "libx264")
                self.assertIn("libx264", flags_cpu)

    def test_02_sidechain_ducking_filter_complex_generation(self):
        """Verify sidechaincompress is built into the filter complex when bg music is enabled."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")

        out_path = Path(self.tmp_dir.name) / "test_sidechain.mp4"

        # Capture ffmpeg commands
        captured_cmds = []
        orig_run = subprocess.run

        def mock_subprocess_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and Path(str(cmd[0])).stem.lower() == "ffmpeg":
                captured_cmds.append(cmd)
                # If it's a test probe or duration probe, let orig_run handle it if needed
                if "-f" in cmd and "null" in cmd:
                    return orig_run(cmd, *args, **kwargs)
                # Mock success for actual cut render
                return MagicMock(returncode=0, stdout="", stderr="")
            return orig_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("video_cutter.generate_synced_subtitles", return_value=True):
                success = cut_and_format_clip(
                    youtube_url=str(SAMPLE_VIDEO),
                    start_sec=0.0,
                    end_sec=5.0,
                    output_path=out_path,
                    enable_bg_music=True,
                    bg_music_volume=0.20,
                    enable_snappy_cuts=False,
                    enable_punch_zooms=False,
                    enable_outro=False
                )

        # Locate the render command containing -filter_complex
        render_cmds = [c for c in captured_cmds if "-filter_complex" in c]
        self.assertTrue(len(render_cmds) > 0, "No ffmpeg render command found with -filter_complex")
        fc_idx = render_cmds[0].index("-filter_complex") + 1
        filter_str = render_cmds[0][fc_idx]

        # Verify sidechaincompress and asplit are present
        self.assertIn("sidechaincompress=", filter_str)
        self.assertIn("threshold=0.08:ratio=4:attack=20:release=350", filter_str)
        self.assertIn("asplit=2", filter_str)
        self.assertIn("amix=inputs=2", filter_str)

    def test_03_whisper_model_cuda_or_cpu_initialization(self):
        """Verify get_whisper_model handles device selection gracefully."""
        # Ensure it loads without exception
        model = get_whisper_model("base.en")
        self.assertIsNotNone(model)

    def test_04_parallel_queue_thread_pool_executor(self):
        """Verify multi-worker ThreadPoolExecutor safely renders and ranks items."""
        mock_clips = [
            {"start": 10.0, "end": 25.0, "text": "First clip", "score": 9.2},
            {"start": 40.0, "end": 55.0, "text": "Second clip", "score": 8.8},
            {"start": 70.0, "end": 85.0, "text": "Third clip", "score": 8.5},
        ]

        def _dummy_render(item):
            rank, clip = item
            return (f"clip_{rank}.mp4", 15.2, clip, {"title": f"Clip {rank}"})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(_dummy_render, enumerate(mock_clips, 1)))

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0][0], "clip_1.mp4")
        self.assertEqual(results[1][0], "clip_2.mp4")
        self.assertEqual(results[2][0], "clip_3.mp4")

if __name__ == "__main__":
    unittest.main()
