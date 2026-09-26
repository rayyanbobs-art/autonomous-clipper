"""
Unit test suite for Subtitle Taxonomy implementation in autonomous_clipper.
Tests:
- Netflix Standard (20 CPS, natural case, clean sans-serif, title safe area)
- BBC SDH (High-contrast yellow text, solid black bounding box plate BorderStyle=3)
- Cinematic Auteur (Georgia serif, soft shadow, subtle letterbox framing, \\fad(80,80))
- Kinetic Pop (Impact bold, 1-2 word chunking, \\fscx114 active bounce, emojis)
- /api/subtitle-styles API endpoint
"""

import unittest
from subtitles import SUBTITLE_STYLES, build_ass_from_words
from app import app


class TestSubtitleTaxonomy(unittest.TestCase):
    def setUp(self):
        # 12 synthetic words simulating speech over ~4 seconds
        self.mock_words = [
            {"word": "The", "start": 0.0, "end": 0.25},
            {"word": "secret", "start": 0.28, "end": 0.65},
            {"word": "to", "start": 0.68, "end": 0.85},
            {"word": "building", "start": 0.88, "end": 1.30},
            {"word": "great", "start": 1.35, "end": 1.70},
            {"word": "wealth", "start": 1.75, "end": 2.20},
            {"word": "is", "start": 2.25, "end": 2.45},
            {"word": "relentless", "start": 2.50, "end": 3.00},
            {"word": "focus", "start": 3.05, "end": 3.45},
            {"word": "and", "start": 3.50, "end": 3.65},
            {"word": "speed", "start": 3.70, "end": 4.10},
            {"word": "today", "start": 4.15, "end": 4.50},
        ]

    def test_styles_registered_in_dictionary(self):
        """Verify all 4 new taxonomy styles exist in SUBTITLE_STYLES."""
        for style_key in ["netflix_standard", "bbc_sdh", "cinematic_auteur", "kinetic_pop"]:
            self.assertIn(style_key, SUBTITLE_STYLES)
            style = SUBTITLE_STYLES[style_key]
            self.assertIn("name", style)
            self.assertIn("font", style)
            self.assertIn("size", style)
            self.assertIn("primary_color", style)
            self.assertIn("margin_v", style)

    def test_netflix_standard_specs(self):
        """Netflix standard: Arial, 20-CPS clean sans-serif, natural case, no pop bounce."""
        style = SUBTITLE_STYLES["netflix_standard"]
        self.assertEqual(style["font"], "Arial")
        self.assertFalse(style.get("pop_scale"))
        self.assertIsNone(style.get("highlight_color"))
        self.assertFalse(style.get("uppercase"))
        self.assertEqual(style.get("max_words_per_line"), 7)

        ass = build_ass_from_words(self.mock_words, style_key="netflix_standard", enable_emojis=False)
        self.assertIn("Style: Default,Arial,48", ass)
        self.assertIn("[Events]", ass)
        # Verify natural sentence casing is preserved
        self.assertIn("The secret to building great wealth", ass)
        # Verify no active pop scaling tag exists
        self.assertNotIn("\\fscx11", ass)

    def test_bbc_sdh_specs(self):
        """BBC SDH: High contrast yellow text with opaque black bounding box plate (BorderStyle=3)."""
        style = SUBTITLE_STYLES["bbc_sdh"]
        self.assertEqual(style["border_style"], 3)
        self.assertEqual(style["outline"], 0)
        self.assertEqual(style["shadow"], 0)
        self.assertEqual(style["primary_color"], "&H0000FFFF")  # BBC Yellow
        self.assertEqual(style["back_color"], "&H00000000")     # Solid opaque black

        ass = build_ass_from_words(self.mock_words, style_key="bbc_sdh", enable_emojis=False)
        # BorderStyle 3 in V4+ Style definition
        self.assertIn(",3,0,0,2,", ass)
        self.assertIn("&H0000FFFF", ass)
        self.assertIn("The secret to building great", ass)

    def test_cinematic_auteur_specs(self):
        """Cinematic Auteur: Georgia serif font, soft shadow, and \\fad(80,80) dialogue fade."""
        style = SUBTITLE_STYLES["cinematic_auteur"]
        self.assertEqual(style["font"], "Georgia")
        self.assertTrue(style.get("fade"))
        self.assertFalse(style.get("uppercase"))

        ass = build_ass_from_words(self.mock_words, style_key="cinematic_auteur", enable_emojis=False)
        self.assertIn("Style: Default,Georgia,", ass)
        # Verify smooth fade transition tag is injected into dialogues
        self.assertIn("{\\fad(80,80)}", ass)
        self.assertIn("The secret to building", ass)

    def test_kinetic_pop_specs(self):
        """Kinetic Pop: Impact font, 2-word bursts, active pop bounce \\fscx114, emoji injection."""
        style = SUBTITLE_STYLES["kinetic_pop"]
        self.assertEqual(style["font"], "Impact")
        self.assertTrue(style.get("pop_scale"))
        self.assertEqual(style["highlight_color"], "&H0000FFFF")
        self.assertTrue(style.get("uppercase"))
        self.assertEqual(style.get("max_words_per_line"), 2)

        ass = build_ass_from_words(self.mock_words, style_key="kinetic_pop", enable_emojis=True)
        self.assertIn("Style: Default,Impact,62,", ass)
        # Verify active pop scale bounce and yellow color highlight
        self.assertIn("\\fscx114\\fscy114", ass)
        self.assertIn("\\c&H0000FFFF&", ass)
        # Verify emojis mapped for "wealth" (💎) and "speed" (⚡)
        self.assertIn("💎", ass)
        self.assertIn("⚡", ass)

    def test_api_subtitle_styles_endpoint(self):
        """Test Flask /api/subtitle-styles exposes all styles."""
        client = app.test_client()
        res = client.get("/api/subtitle-styles")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("styles", data)
        styles = data["styles"]
        for key in ["netflix_standard", "bbc_sdh", "cinematic_auteur", "kinetic_pop", "bold_pop"]:
            self.assertIn(key, styles)


if __name__ == "__main__":
    unittest.main()
