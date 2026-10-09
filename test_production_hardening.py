import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import time
import shutil
import threading

import app
import video_cutter
import clipper
from config import TEMP_DIR, OUTPUT_DIR

class TestProductionHardening(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_01_admission_control_queue_capacity(self):
        """Verify /api/generate returns 429 when queued and running jobs exceed 100."""
        client = app.app.test_client()
        with patch.dict(app.JOBS, {}, clear=True):
            # Fill 100 jobs
            for i in range(100):
                app.JOBS[f"mock_{i:03d}"] = {
                    "job_id": f"mock_{i:03d}",
                    "status": "queued" if i % 2 == 0 else "running",
                    "_created_at": time.time()
                }

            res = client.post("/api/generate", json={
                "url": "https://www.youtube.com/watch?v=test_full_queue"
            })
            self.assertEqual(res.status_code, 429)
            data = res.get_json()
            self.assertIn("Job queue is full", data.get("error", ""))

    def test_02_job_temp_directory_isolation_and_cleanup(self):
        """Verify run_clipping_job creates isolated job_temp_dir and cleans it up in finally."""
        job_id = "test_iso_1"
        test_job_dir = TEMP_DIR / job_id
        if test_job_dir.exists():
            shutil.rmtree(test_job_dir, ignore_errors=True)

        app.JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "_created_at": time.time()
        }

        with patch("app.is_local_video", return_value=False), \
             patch("app.extract_video_id", return_value="fake_id"), \
             patch("app.get_transcript", side_effect=RuntimeError("Intentional failure for test")):
            app.run_clipping_job(job_id, "https://www.youtube.com/watch?v=fake_id", 1, 5)

        self.assertEqual(app.JOBS[job_id]["status"], "failed")
        self.assertIn("Intentional failure for test", app.JOBS[job_id]["error"])
        # Verified: job_temp_dir cleaned up in finally
        self.assertFalse(test_job_dir.exists(), "job_temp_dir must be deleted after job termination")

    def test_03_boot_sweep_cleanup_stale_directories(self):
        """Verify _cleanup_stale_temp_dirs removes orphan 8-character hex directories."""
        stale_dir_1 = TEMP_DIR / "1a2b3c4d"
        stale_dir_2 = TEMP_DIR / "deadbeef"
        normal_dir = TEMP_DIR / "broll"

        stale_dir_1.mkdir(parents=True, exist_ok=True)
        stale_dir_2.mkdir(parents=True, exist_ok=True)
        normal_dir.mkdir(parents=True, exist_ok=True)

        (stale_dir_1 / "stale.mp4").write_bytes(b"dummy")
        (stale_dir_2 / "stale.mp4").write_bytes(b"dummy")

        app._cleanup_stale_temp_dirs()

        self.assertFalse(stale_dir_1.exists(), "stale_dir_1 should be swept")
        self.assertFalse(stale_dir_2.exists(), "stale_dir_2 should be swept")
        self.assertTrue(normal_dir.exists(), "shared asset directories must be preserved")

    def test_04_watchdog_terminates_stuck_running_jobs(self):
        """Verify _prune_jobs marks jobs running longer than 20 minutes as failed."""
        now = time.time()
        with patch.dict(app.JOBS, {}, clear=True):
            app.JOBS["stuck_job"] = {
                "job_id": "stuck_job",
                "status": "running",
                "progress": 40,
                "_started_at": now - 1500,  # 25 minutes ago
                "_created_at": now - 1500
            }
            app.JOBS["healthy_job"] = {
                "job_id": "healthy_job",
                "status": "running",
                "progress": 40,
                "_started_at": now - 120,   # 2 minutes ago
                "_created_at": now - 120
            }

            app._prune_jobs(now=now)

            self.assertEqual(app.JOBS["stuck_job"]["status"], "failed")
            self.assertIn("20-minute", app.JOBS["stuck_job"]["error"])
            self.assertEqual(app.JOBS["healthy_job"]["status"], "running")

    def test_05_resilient_429_audio_fallback(self):
        """Verify get_transcript engages yt-dlp audio download and local whisper on 429."""
        mock_snippets = [{"text": "Hello world", "start": 0.0, "duration": 2.5}]
        with patch("clipper.get_video_transcript", side_effect=RuntimeError("HTTP Error 429: Too Many Requests")), \
             patch("subprocess.run") as mock_subproc, \
             patch("clipper.get_local_transcript", return_value=mock_snippets) as mock_local:

            # Mock successful yt-dlp run and dummy audio file creation
            def fake_run(cmd, **kwargs):
                out_path_idx = cmd.index("-o") + 1
                out_path = Path(cmd[out_path_idx])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"fake_audio")
                m = MagicMock()
                m.returncode = 0
                return m

            mock_subproc.side_effect = fake_run

            with tempfile.TemporaryDirectory() as td:
                res = clipper.get_transcript("blocked_vid_123", temp_dir=Path(td))
                self.assertEqual(res, mock_snippets)
                self.assertTrue(mock_subproc.called)
                self.assertTrue(mock_local.called)

    def test_06_video_cutter_threads_and_semaphore(self):
        """Verify video_cutter includes -threads 4 and uses _GPU_SEMAPHORE."""
        self.assertIsNotNone(video_cutter._GPU_SEMAPHORE)
        with patch("video_cutter._BEST_ENCODER_INFO", None):
            codec, flags = video_cutter.get_best_video_encoder()
            self.assertIn("-threads", flags)
            self.assertIn("4", flags)

if __name__ == "__main__":
    unittest.main()
