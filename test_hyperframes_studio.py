import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import json
import shutil

import hyperframes_editor
from hyperframes_editor import (
    ensure_workspace,
    prepare_clean_base_video,
    generate_composition_html,
    STYLES,
    WORKSPACE_DIR
)

SAMPLE_VIDEO = Path(__file__).parent / "temp" / "test_debug_224_280.mp4"

class TestHyperframesStudio(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_01_ensure_workspace_creates_boilerplate_and_gsap(self):
        """Verify ensure_workspace initializes missing directories and scaffolds files."""
        fake_ws = Path(self.tmp_dir.name) / "test_ws"
        self.assertFalse(fake_ws.exists())

        res_ws = ensure_workspace(fake_ws)
        self.assertEqual(res_ws, fake_ws)
        self.assertTrue(fake_ws.exists())
        self.assertTrue((fake_ws / "vendor").exists())
        self.assertTrue((fake_ws / "hyperframes.json").exists())
        self.assertTrue((fake_ws / "package.json").exists())
        self.assertTrue((fake_ws / "meta.json").exists())
        self.assertTrue((fake_ws / "vendor" / "gsap.min.js").exists())

        # Validate hyperframes.json syntax
        hf_data = json.loads((fake_ws / "hyperframes.json").read_text(encoding="utf-8"))
        self.assertIn("registry", hf_data)

    def test_02_prepare_clean_base_video_creates_parent_dir(self):
        """Verify prepare_clean_base_video creates target directory and does not crash with Errno 2."""
        nested_target = Path(self.tmp_dir.name) / "deeply" / "nested" / "dir" / "base_vertical.mp4"
        self.assertFalse(nested_target.parent.exists())

        fake_source = Path(self.tmp_dir.name) / "clip.mp4"
        fake_source.write_bytes(b"dummy video data")

        # Mock ffmpeg delogo call
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            status = prepare_clean_base_video(fake_source, nested_target)

        # Parent directory must have been created
        self.assertTrue(nested_target.parent.exists())

    def test_03_generate_composition_html_validity(self):
        """Verify generate_composition_html produces valid HTML with GSAP tags and styles."""
        words = [
            {"word": "HELLO", "start": 0.1, "end": 0.5},
            {"word": "WORLD", "start": 0.6, "end": 1.0}
        ]
        zooms = [(0.2, 0.4)]
        html = generate_composition_html(
            video_rel_path="base_vertical.mp4",
            duration=5.0,
            words=words,
            zooms=zooms,
            badge_text="⚡ TEST BADGE",
            style_key="mrbeast_hyper",
            zoom_scale=1.15
        )
        self.assertIn("<!doctype html>", html)
        self.assertIn("vendor/gsap.min.js", html)
        self.assertIn("TEST BADGE", html)
        self.assertIn("HELLO", html)
        self.assertIn("WORLD", html)
        self.assertIn('window.__timelines["main"] = tl;', html)

    def test_04_default_workspace_dir_is_internal_to_project(self):
        """Verify WORKSPACE_DIR is inside the project directory, not external D:\\my-video."""
        from config import BASE_DIR
        self.assertEqual(WORKSPACE_DIR.parent, BASE_DIR)
        self.assertEqual(WORKSPACE_DIR.name, "hyperframes_studio")

    def test_05_cli_cmd_non_interactive_flags(self):
        """Verify get_hyperframes_cli_cmd returns non-interactive flags (--yes or bunx)."""
        cmd = hyperframes_editor.get_hyperframes_cli_cmd()
        self.assertIsInstance(cmd, list)
        self.assertGreater(len(cmd), 0)
        # If npx is used, must include --yes
        if "npx" in cmd[0]:
            self.assertIn("--yes", cmd)

    def test_06_run_hyperframes_command_devnull_stdin(self):
        """Verify run_hyperframes_command invokes subprocess with stdin=DEVNULL to prevent hangs."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Hyperframes rendered", stderr="")
            res = hyperframes_editor.run_hyperframes_command(["--version"])
            self.assertEqual(res.returncode, 0)
            mock_run.assert_called_once()
            _, kwargs = mock_run.call_args
            import subprocess
            self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_07_no_subtitle_blur_shield_in_html(self):
        """Verify #subtitle-shield is omitted from composition to avoid obscuring footage with blur."""
        html = generate_composition_html(
            video_rel_path="base_vertical.mp4",
            duration=5.0,
            words=[{"word": "TEST", "start": 0.1, "end": 0.5}],
            zooms=[],
            badge_text="",
            style_key="viral_pop"
        )
        self.assertNotIn("id=\"subtitle-shield\"", html)
        self.assertNotIn("#subtitle-shield", html)

    def test_08_detect_audio_spikes_spacing_and_cap(self):
        """Verify detect_audio_spikes enforces min_spacing and caps max spikes."""
        import numpy as np
        # Synthetic loud samples with frequent bursts
        fs = 16000
        dur_sec = 20
        total_samples = fs * dur_sec
        audio = np.zeros(total_samples, dtype=np.int16)
        # Create loud pulses every 2 seconds
        for sec in range(1, dur_sec, 2):
            audio[int(sec * fs) : int((sec + 0.3) * fs)] = 25000

        fake_clip = Path(self.tmp_dir.name) / "audio_test.mp4"
        fake_clip.write_bytes(b"dummy")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=audio.tobytes())
            spikes = hyperframes_editor.detect_audio_spikes(fake_clip, min_spacing=7.0, max_spikes=3)
            # Must be capped at max_spikes
            self.assertLessEqual(len(spikes), 3)
            # Must be spaced by at least 7.0 seconds
            for i in range(1, len(spikes)):
                self.assertGreaterEqual(spikes[i][0] - spikes[i-1][0], 7.0)

if __name__ == "__main__":
    unittest.main()
