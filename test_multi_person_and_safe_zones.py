import unittest
import numpy as np
import cv2
from pathlib import Path
from tempfile import TemporaryDirectory

import face_tracker
from subtitles import SUBTITLE_STYLES, hex_to_ass_color, build_ass_from_words
import hyperframes_editor
from video_cutter import cut_and_format_clip

class TestMultiPersonAndSafeZones(unittest.TestCase):

    def test_all_27_subtitle_presets_exist(self):
        """Ensure all 27 iconic creator and viral presets are present in subtitles.py."""
        expected_styles = [
            "none", "bold_pop", "karaoke", "boxed_clean", "minimal_caption",
            "hormozi", "beast", "neon_green", "red_punch", "clean_white",
            "netflix_standard", "bbc_sdh", "cinematic_auteur", "kinetic_pop",
            "wild_den", "iman_gadzhi", "joe_rogan", "ali_abdaal", "vox_explainer",
            "andrew_huberman", "david_goggins", "luke_belmar", "diary_of_a_ceo",
            "fintech_pro", "anime_shonen", "retro_vhs", "streetwear_hype"
        ]
        self.assertEqual(len(expected_styles), 27)
        for s in expected_styles:
            self.assertIn(s, SUBTITLE_STYLES, f"Preset '{s}' missing from SUBTITLE_STYLES")
            preset = SUBTITLE_STYLES[s]
            self.assertIn("name", preset)
            self.assertIn("label", preset)

    def test_hyperframes_presets_coverage(self):
        """Ensure HyperFrames editor supports all creator styles."""
        creator_keys = [
            "wild_den", "hormozi", "beast", "bold_pop", "karaoke",
            "iman_gadzhi", "joe_rogan", "ali_abdaal", "vox_explainer",
            "david_goggins", "luke_belmar", "diary_of_a_ceo", "fintech_pro"
        ]
        for k in creator_keys:
            self.assertIn(k, hyperframes_editor.STYLES, f"Preset '{k}' missing from hyperframes_editor.STYLES")
            self.assertIn("active_color", hyperframes_editor.STYLES[k])

    def test_hex_to_ass_color_conversion(self):
        """Verify hex colors are converted into ASS &HAABBGGRR format with inverted BGR."""
        # Pure White
        self.assertEqual(hex_to_ass_color("#FFFFFF"), "&H00FFFFFF")
        # Pure Red #FF0000 -> &H000000FF
        self.assertEqual(hex_to_ass_color("#FF0000"), "&H000000FF")
        # Pure Blue #0000FF -> &H00FF0000
        self.assertEqual(hex_to_ass_color("#0000FF"), "&H00FF0000")
        # Hormozi Yellow #FFE600 -> &H0000E6FF
        self.assertEqual(hex_to_ass_color("#FFE600"), "&H0000E6FF")
        # Already ASS format
        self.assertEqual(hex_to_ass_color("&H002BD0FF"), "&H002BD0FF")

    def test_build_ass_with_custom_style_overrides(self):
        """Verify build_ass_from_words correctly applies custom font, colors, size, and margins."""
        words = [
            {"word": "Never", "start": 0.0, "end": 0.4},
            {"word": "give", "start": 0.45, "end": 0.8},
            {"word": "up", "start": 0.85, "end": 1.2}
        ]
        custom_style = {
            "font": "Montserrat",
            "size": 72,
            "primary_color": "#00FF00", # Pure green -> &H0000FF00
            "highlight_color": "#FF00FF", # Magenta -> &H00FF00FF
            "outline": 8.0,
            "margin_v": 960,
            "uppercase": True
        }
        ass_text = build_ass_from_words(words, style_key="bold_pop", custom_style=custom_style)
        self.assertIn("Montserrat", ass_text)
        self.assertIn("72", ass_text)
        self.assertIn("&H0000FF00", ass_text)
        self.assertIn(",960,1", ass_text)
        self.assertIn("NEVER", ass_text)

    def test_detect_wide_text_or_graphics(self):
        """Verify wide text edge gradient detector detects horizontal text bands."""
        # 1. Plain blank frame: should return False
        blank = np.zeros((720, 1280), dtype=np.uint8)
        self.assertFalse(face_tracker.detect_wide_text_or_graphics(blank))

        # 2. Synthetic wide banner spanning 70% width with strong horizontal edges
        banner_frame = np.zeros((720, 1280), dtype=np.uint8)
        # Create high-contrast alternating text stripes spanning from x=100 to x=1100 (width=1000px, 78% of 1280)
        cv2.putText(banner_frame, "CHALLENGE SCOREBOARD: $1,000,000 SURVIVAL", (100, 360),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, 255, 3)
        detected = face_tracker.detect_wide_text_or_graphics(banner_frame)
        self.assertTrue(detected, "Failed to detect wide text banner")

    def test_shot_continuity_metadata_stores_wide_fit_intervals(self):
        """Verify _save_shot_continuity and get_shot_continuity_metadata track wide_fit_intervals."""
        fake_video = Path("temp_test_video.mp4")
        segments = [
            [0.0, 3.5, 420, True, 150.0],
            [3.5, 7.0, 800, True, 180.0]
        ]
        wide_intervals = [(0.0, 3.5)]
        face_tracker._save_shot_continuity(
            fake_video,
            segments,
            focal_scale_intervals=[],
            crop_w=608,
            crop_y=0,
            wide_fit_intervals=wide_intervals
        )
        meta = face_tracker.get_shot_continuity_metadata(fake_video)
        self.assertEqual(meta.get("wide_fit_intervals"), [(0.0, 3.5)])

if __name__ == "__main__":
    unittest.main()
