import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import json
import re
import cv2
import numpy as np

import face_tracker
import video_cutter
import scorer
from config import TARGET_WIDTH, TARGET_HEIGHT

SAMPLE_VIDEO = Path(__file__).parent / "temp" / "test_debug_224_280.mp4"

class TestContinuityEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.addCleanup(self.tmp_dir.cleanup)

    def test_01_silence_cuts_micro_fades_and_padding(self):
        """Invariant 1, 4 & Job 1: Verify 20ms micro-crossfades, >=180ms padding, and room tone continuity."""
        input_mp4 = self.tmp_path / "in.mp4"
        input_mp4.write_bytes(b"dummy_in")
        out_mp4 = self.tmp_path / "out.mp4"

        # Simulate silencedetect output with speech separated by 0.5s dead air
        fake_stderr = """
        [silencedetect @ 000001] silence_start: 3.00
        [silencedetect @ 000001] silence_end: 3.50 | silence_duration: 0.50
        """

        captured_cmd = []
        def fake_run(cmd, *args, **kwargs):
            captured_cmd.append(cmd)
            out_mp4.write_bytes(b"dummy_out")
            m = MagicMock()
            m.returncode = 0
            m.stderr = fake_stderr
            return m

        with patch("subprocess.run", side_effect=fake_run), \
             patch("video_cutter.get_video_duration", return_value=6.0):

            ok = video_cutter.apply_snappy_silence_cuts(
                input_video=input_mp4,
                output_video=out_mp4,
                min_silence_sec=0.28,
                silence_thresh_db=-28.0,
                ffmpeg_exe="ffmpeg"
            )
            self.assertTrue(ok)
            self.assertEqual(len(captured_cmd), 2)  # 1st: silencedetect, 2nd: render
            render_args = captured_cmd[1]
            fc_idx = render_args.index("-filter_complex") + 1
            fc = render_args[fc_idx]

            # Invariant 1: Assert 20ms sine-squared micro-crossfades (curve=qsin)
            self.assertIn("afade=t=in:ss=0:d=0.020:curve=qsin", fc)
            self.assertIn(":d=0.020:curve=qsin", fc)

            # Invariant 4: Assert speech silence padding >= 180ms
            # Silence started at 3.00s -> chunk must end at 3.00 + 0.20 = 3.20s (200ms >= 180ms)
            self.assertIn("trim=start=0.000:end=3.200", fc)
            # Next speech starts at 3.50 - 0.05 = 3.45s (50ms vocal pre-roll)
            self.assertIn("trim=start=3.450:end=6.000", fc)

            # Invariant 1 & Task 3: Assert pink noise room tone continuity bed (-48dB)
            self.assertIn("anoisesrc=", fc)
            self.assertIn("c=pink:a=0.004:r=48000", fc)
            # voice + 1 L-cut tail + room tone, un-normalized so the voice is not attenuated 6dB
            self.assertIn("amix=inputs=3:duration=first:dropout_transition=0:normalize=0", fc)

            # Invariant 2: L-cut — outgoing take's audio bleeds 200ms under the next shot,
            # placed exactly at the splice (3.20s output time minus the 20ms fade overlap)
            self.assertIn("atrim=start=3.180:end=3.400", fc)
            self.assertIn("afade=t=out:st=0.020:d=0.200:curve=qsin", fc)
            self.assertIn("adelay=delays=3180:all=1", fc)

    def test_02_outro_crossfade(self):
        """Invariant 1: Verify 25ms equal-power crossfade on outro concatenation."""
        local_mp4 = self.tmp_path / "source.mp4"
        local_mp4.write_bytes(b"dummy_source")
        out_mp4 = self.tmp_path / "final_clip.mp4"
        captured_cmds = []

        def fake_run(cmd, *args, **kwargs):
            captured_cmds.append(cmd)
            for item in cmd:
                if "main_" in str(item) and str(item).endswith(".mp4"):
                    Path(item).write_bytes(b"dummy_main")
                elif "outro_" in str(item) and str(item).endswith(".mp4"):
                    Path(item).write_bytes(b"dummy_outro")
            out_mp4.write_bytes(b"dummy_out")
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        with patch("subprocess.run", side_effect=fake_run), \
             patch("video_cutter.get_ffmpeg_path", return_value="ffmpeg"), \
             patch("video_cutter.get_video_duration", return_value=10.0), \
             patch("video_cutter.has_audio_stream", return_value=True), \
             patch("video_cutter.generate_outro_card", return_value=True), \
             patch("video_cutter.generate_synced_subtitles", return_value=False):

            ok = video_cutter.cut_and_format_clip(
                youtube_url=str(local_mp4),
                start_sec=0.0,
                end_sec=10.0,
                output_path=out_mp4,
                enable_broll=False,
                enable_emojis=False,
                enable_snappy_cuts=False,
                enable_punch_zooms=False,
                enable_outro=True
            )
            self.assertTrue(ok)
            outro_cmds = [c for c in captured_cmds if any("outro_" in str(arg) for arg in c) and "-filter_complex" in c]
            self.assertGreater(len(outro_cmds), 0)
            fc = outro_cmds[0][outro_cmds[0].index("-filter_complex") + 1]

            # Invariant 1: Equal-power 25ms crossfade on outro
            self.assertIn("afade=t=out:st=9.975:d=0.025:curve=qsin", fc)
            self.assertIn("afade=t=in:ss=0:d=0.025:curve=qsin", fc)

    def test_03_edl_json_schema_and_payload(self):
        """Job 3 & 4: Verify continuity EDL JSON payload is written and validates against schema."""
        local_mp4 = self.tmp_path / "source.mp4"
        local_mp4.write_bytes(b"dummy_source")
        out_mp4 = self.tmp_path / "test_clip.mp4"

        def fake_run(cmd, *args, **kwargs):
            out_mp4.write_bytes(b"dummy_out")
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("subprocess.run", side_effect=fake_run), \
             patch("video_cutter.get_ffmpeg_path", return_value="ffmpeg"), \
             patch("video_cutter.get_video_duration", return_value=10.0), \
             patch("video_cutter.has_audio_stream", return_value=True), \
             patch("video_cutter.generate_outro_card", return_value=False), \
             patch("video_cutter.generate_synced_subtitles", return_value=False):

            ok = video_cutter.cut_and_format_clip(
                youtube_url=str(local_mp4),
                start_sec=0.0,
                end_sec=10.0,
                output_path=out_mp4,
                enable_broll=False,
                enable_outro=False,
                enable_snappy_cuts=False,
                enable_punch_zooms=False
            )
            self.assertTrue(ok)

            edl_file = self.tmp_path / "test_clip_edl.json"
            self.assertTrue(edl_file.exists(), "EDL JSON file must be generated alongside the video.")

            data = json.loads(edl_file.read_text(encoding="utf-8"))
            self.assertIn("cuts", data)
            self.assertIn("audio_smoothing", data)
            self.assertIsInstance(data["cuts"], list)
            self.assertGreaterEqual(len(data["cuts"]), 1)

            # Validate cuts array schema
            c0 = data["cuts"][0]
            self.assertIn("cut_index", c0)
            self.assertIn("source_in", c0)
            self.assertIn("source_out", c0)
            self.assertIn("shot_type", c0)
            self.assertIn(c0["shot_type"], ["talking_head", "medium_close_up", "wide"])
            self.assertIn("focal_anchor", c0)
            self.assertIn("x", c0["focal_anchor"])
            self.assertIn("y", c0["focal_anchor"])
            self.assertIn("transition_out", c0)
            self.assertIn(c0["transition_out"]["type"], ["L_CUT", "J_CUT", "CUT_ON_ACTION"])
            self.assertIn("audio_bleed_ms", c0["transition_out"])

            # Validate audio_smoothing schema
            smooth = data["audio_smoothing"]
            self.assertEqual(smooth.get("crossfade_curve"), "S_CURVE")
            self.assertEqual(smooth.get("boundary_crossfade_duration_ms"), 20)
            # This render runs with enable_snappy_cuts=False, so no room-tone bed / L-cuts exist
            self.assertFalse(smooth.get("room_tone_pad_active"))
            self.assertEqual(smooth.get("l_cut_bleed_ms"), 0)

    def test_04_eye_trace_continuity_and_dampening(self):
        """Invariant 3 & Job 2: Verify focal center jumps > 15% are dampened and interpolated over 8 frames."""
        # Mock VideoCapture with 2 scene cuts and face moving across frame
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
        curr_pos = [0]
        def mock_set(prop, val):
            if prop == cv2.CAP_PROP_POS_FRAMES:
                curr_pos[0] = val
        mock_cap.set.side_effect = mock_set
        mock_cap.get.side_effect = lambda prop: curr_pos[0] if prop == cv2.CAP_PROP_POS_FRAMES else {
            cv2.CAP_PROP_FRAME_WIDTH: 1920,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080,
            cv2.CAP_PROP_FRAME_COUNT: 300,
            cv2.CAP_PROP_FPS: 30.0
        }.get(prop, 0.0)

        # Shot 1: x around 200; Shot 2: x around 900 (large jump > 0.15 * 608 = 91px)
        with patch("cv2.VideoCapture", return_value=mock_cap), \
             patch("face_tracker.detect_scene_cuts", return_value=[0.0, 4.0, 8.0]), \
             patch("face_tracker.get_face_detector") as mock_det_factory:

            mock_det = MagicMock()
            # In shot 1: face at x=300; in shot 2: face at x=1400
            def fake_detect(frame):
                f_idx = curr_pos[0]
                if f_idx < 120:
                    faces = np.array([[300, 200, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.9]], dtype=np.float32)
                else:
                    faces = np.array([[1400, 200, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.9]], dtype=np.float32)
                return (None, faces)

            mock_det.detect.side_effect = fake_detect
            mock_det_factory.return_value = mock_det

            crop_w, crop_h, crop_x_expr, crop_y = face_tracker.compute_smart_crop_offset(Path("dummy.mp4"), TARGET_WIDTH, TARGET_HEIGHT)

            # Assert 8-frame (~0.25s) linear interpolation is present in dynamic expression across cut at 4.0s
            self.assertIn("if(lt(t,4.00)", crop_x_expr)
            self.assertIn("(t-4.00)/0.25", crop_x_expr)
            # Verify no single-frame position spikes: expression uses continuous interpolation
            self.assertIn("+", crop_x_expr)
            self.assertIn("*", crop_x_expr)

    def test_05_30_degree_focal_scale_shift(self):
        """Job 2: Verify consecutive cuts of same static speaker trigger 1.15x punch-in zoom."""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.zeros((1080, 1920, 3), dtype=np.uint8))
        mock_cap.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FRAME_WIDTH: 1920,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080,
            cv2.CAP_PROP_FRAME_COUNT: 300,
            cv2.CAP_PROP_FPS: 30.0
        }.get(prop, 0.0)

        # 2 consecutive shots of same speaker from same static perspective (|X_1 - X_0| <= 25px)
        with patch("cv2.VideoCapture", return_value=mock_cap), \
             patch("face_tracker.detect_scene_cuts", return_value=[0.0, 3.0, 6.0]), \
             patch("face_tracker.get_face_detector") as mock_det_factory:

            mock_det = MagicMock()
            def fake_detect(frame):
                faces = np.array([[600, 200, 120, 120, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.9]], dtype=np.float32)
                return (None, faces)
            mock_det.detect.side_effect = fake_detect
            mock_det_factory.return_value = mock_det

            face_tracker.compute_smart_crop_offset(Path("dummy.mp4"), TARGET_WIDTH, TARGET_HEIGHT)
            meta = face_tracker.get_shot_continuity_metadata(Path("dummy.mp4"))

            self.assertIn("focal_scale_intervals", meta)
            self.assertEqual(len(meta["focal_scale_intervals"]), 1)
            self.assertEqual(meta["focal_scale_intervals"][0], (3.0, 6.0))

    def test_06_scorer_4phase_rhythmic_pacing(self):
        """Invariant 5 & Job 3: Verify 4-phase cadence analysis and pacing bonus in scorer.py."""
        transcript = [
            {"text": "Here is the critical opening insight you need to understand.", "start": 0.0, "duration": 3.0}, # Initial hold >= 2.5s
            {"text": "First quick point.", "start": 3.0, "duration": 1.5},                                            # Middle cut 1.2-2.5s
            {"text": "Second quick point.", "start": 4.5, "duration": 1.8},                                           # Middle cut 1.2-2.5s
            {"text": "Third punchy takeaway.", "start": 6.3, "duration": 2.0},                                        # Middle cut 1.2-2.5s
            {"text": "And that is why you should always follow this exact strategy forever.", "start": 8.3, "duration": 3.5} # Payoff hold >= 3.0s
        ]

        windows = scorer.create_windows(transcript, min_duration=5.0, max_duration=15.0, step=5.0)
        self.assertGreaterEqual(len(windows), 1)
        w0 = windows[0]
        self.assertIn("rhythmic_pacing", w0)
        pacing = w0["rhythmic_pacing"]
        self.assertTrue(pacing.get("follows_4phase"))
        self.assertGreaterEqual(pacing.get("initial_hold"), 2.5)
        self.assertGreaterEqual(pacing.get("payoff_hold"), 3.0)

        # Test rubric evaluation rewards this candidate with pacing bonus
        candidate = {
            "text": w0["text"],
            "duration": w0["duration"],
            "virality_score": 2.0,
            "standalone_prob": 0.85,
            "sponsor_prob": 0.0,
            "category": "high_value_insight",
            "rhythmic_pacing": pacing
        }
        scores, avg, notes = scorer.evaluate_candidate_against_rubric(candidate)
        self.assertGreaterEqual(scores["pacing"], 9, "Candidate following 4-phase cadence must receive high pacing score.")

if __name__ == "__main__":
    unittest.main()
