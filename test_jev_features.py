import unittest
import sys
import subprocess
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorer import (
    score_chunk_with_jev,
    score_chunks_batch_with_jev,
    compute_rubric_scores,
    critique_gate_search
)
from subtitles import (
    is_profane_word,
    mask_profane_word,
    detect_profanity_intervals,
    build_ass_from_words
)
from video_cutter import get_ffmpeg_path

class TestJevFeatures(unittest.TestCase):

    def test_01_sponsor_killer_scoring_and_rubric(self):
        """Verify sponsor probability calculation and rubric penalization."""
        # Clean viral chunk
        clean_cand = {
            "text": "The greatest habit of successful people is uninterrupted deep work every single morning.",
            "virality_score": 2.2,
            "standalone_prob": 0.85,
            "sponsor_prob": 0.02,
            "category": "high_value_insight",
            "duration": 35.0
        }
        scores_clean, avg_clean, notes_clean = compute_rubric_scores(clean_cand, enable_sponsor_killer=True)
        self.assertGreaterEqual(avg_clean, 7.0)
        self.assertNotIn("Sponsor Read", notes_clean)

        # Sponsor read chunk
        sponsor_cand = {
            "text": "This episode is sponsored by Athletic Greens. Go to athleticgreens.com for a free 1-year supply of vitamin D.",
            "virality_score": 1.0,
            "standalone_prob": 0.90,
            "sponsor_prob": 0.95,
            "category": "high_value_insight",
            "duration": 35.0
        }
        scores_sponsor, avg_sponsor, notes_sponsor = compute_rubric_scores(sponsor_cand, enable_sponsor_killer=True)
        self.assertLess(avg_sponsor, 5.0)
        self.assertIn("Sponsor Read", notes_sponsor)
        self.assertLessEqual(scores_sponsor["hook"], 3)
        self.assertLessEqual(scores_sponsor["payoff"], 2)

    def test_02_critique_gate_disqualifies_sponsor(self):
        """Verify critique_gate_search drops sponsor reads when enable_sponsor_killer=True."""
        windows = [
            {
                "start": 0.0,
                "end": 35.0,
                "duration": 35.0,
                "text": "Head to squarespace.com/mychannel to save 10% on your first website or domain with promo code.",
                "virality_score": 1.2,
                "standalone_prob": 0.95,
                "sponsor_prob": 0.98,
                "category": "high_value_insight",
                "composite_score": 0.0
            },
            {
                "start": 40.0,
                "end": 75.0,
                "duration": 35.0,
                "text": "Why do 90% of startups fail within two years? Because they build products nobody actually wants.",
                "virality_score": 2.3,
                "standalone_prob": 0.85,
                "sponsor_prob": 0.01,
                "category": "high_value_insight",
                "composite_score": 2.37
            }
        ]

        top_clips = critique_gate_search(
            candidate_windows=windows,
            target_clips=1,
            threshold=7.0,
            max_attempts=3,
            enable_sponsor_killer=True
        )

        self.assertEqual(len(top_clips), 1)
        # Winner must be the non-sponsor startup insight
        self.assertEqual(top_clips[0]["start"], 40.0)
        self.assertLess(top_clips[0]["sponsor_prob"], 0.10)

    def test_03_parallel_batch_evaluation(self):
        """Verify parallel batch scoring formats batches and parses results correctly."""
        from unittest.mock import patch, MagicMock
        chunks = [
            "Every day you don't execute is a day your competitors get ahead of you.",
            "Use discount code PODCAST20 at checkout for twenty percent off your order."
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "answers": {
                    "c0_virality": {"score": 2.5, "confidence": 0.90},
                    "c0_standalone": {"noul": 0.85},
                    "c0_sponsor": {"noul": 0.05},
                    "c0_category": {"choice": "high_value_insight"},
                    "c1_virality": {"score": 0.8, "confidence": 0.95},
                    "c1_standalone": {"noul": 0.90},
                    "c1_sponsor": {"noul": 0.95},
                    "c1_category": {"choice": "filler_banter"}
                }
            }
        }
        with patch("requests.post", return_value=mock_resp):
            results = score_chunks_batch_with_jev(chunks, api_key="mock_key", batch_size=2)
            self.assertEqual(len(results), 2)
            for r in results:
                self.assertTrue(r.get("success"))
                self.assertIn("virality_score", r)
                self.assertIn("standalone_prob", r)
                self.assertIn("sponsor_prob", r)
                self.assertIn("composite_score", r)

            # Chunk 0 should have very low sponsor prob; Chunk 1 should have high sponsor prob
            self.assertLess(results[0]["sponsor_prob"], 0.20)
            self.assertGreater(results[1]["sponsor_prob"], 0.70)

    def test_04_profanity_detection_and_masking(self):
        """Verify profanity detection and masking for subtitles."""
        self.assertTrue(is_profane_word("fuck"))
        self.assertTrue(is_profane_word("fucking"))
        self.assertTrue(is_profane_word("SHIT!"))
        self.assertTrue(is_profane_word("bullshit"))
        self.assertTrue(is_profane_word("bitch"))
        self.assertFalse(is_profane_word("hello"))
        self.assertFalse(is_profane_word("shorts"))

        self.assertEqual(mask_profane_word("fuck"), "f***")
        self.assertEqual(mask_profane_word("fucking"), "f******")
        self.assertEqual(mask_profane_word("shit!"), "s***!")
        self.assertEqual(mask_profane_word("clean"), "clean")

    def test_05_profanity_intervals_and_subtitles(self):
        """Verify timestamp extraction and ASS generation with censored words."""
        words = [
            {"start": 1.0, "end": 1.3, "word": "That"},
            {"start": 1.35, "end": 1.6, "word": "is"},
            {"start": 1.7, "end": 2.1, "word": "fucking"},
            {"start": 2.2, "end": 2.8, "word": "crazy"}
        ]
        intervals = detect_profanity_intervals(words, padding_sec=0.05)
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0][0], 1.65, places=2)
        self.assertAlmostEqual(intervals[0][1], 2.15, places=2)

        # Uncensored ASS
        ass_raw = build_ass_from_words(words, enable_auto_bleep=False)
        self.assertIn("FUCKING", ass_raw)

        # Censored ASS
        ass_bleep = build_ass_from_words(words, enable_auto_bleep=True)
        self.assertIn("F******", ass_bleep)
        self.assertNotIn("FUCKING", ass_bleep)

    def test_06_ffmpeg_audio_bleep_render(self):
        """Verify FFmpeg generates audio filter complex with 1000Hz bleep tone without error."""
        ffmpeg_exe = get_ffmpeg_path()
        out_test = Path("temp/test_bleep_audio.aac")
        intervals = [(1.0, 1.5)]
        bleep_cond = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in intervals])
        audio_filter = (
            f"[0:a]volume=enable='{bleep_cond}':volume=0[censored_voice];"
            f"sine=f=1000:d=3.0,volume=enable='{bleep_cond}':volume=0.35:volume=0[beep];"
            f"[censored_voice][beep]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )

        cmd = [
            ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3.0",
            "-filter_complex", audio_filter,
            "-map", "[a]",
            "-c:a", "aac",
            str(out_test)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        self.assertEqual(res.returncode, 0, f"FFmpeg error: {res.stderr}")
        self.assertTrue(out_test.exists())
        self.assertGreater(out_test.stat().st_size, 0)
        out_test.unlink()

if __name__ == "__main__":
    unittest.main()
