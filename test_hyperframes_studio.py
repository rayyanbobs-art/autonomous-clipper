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

if __name__ == "__main__":
    unittest.main()
