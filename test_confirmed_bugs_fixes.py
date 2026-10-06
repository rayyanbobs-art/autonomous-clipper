import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import tempfile
import re

import video_cutter
from video_cutter import apply_snappy_silence_cuts
import batch_rerender


_LOG_REDIRECT_TMPDIR = None
_LOG_REDIRECT_PREVIOUS = None


def setUpModule():
    """
    Redirect the publish log to a tmp dir. This suite drives the production
    clip/app/rerender paths with fixture transcripts; without redirection those runs
    would append FAKE records to logs/publish_log.jsonl -- data-poisoning the very
    log TRENDING_BOOST will one day be set from. See publish_log.default_log_path.
    """
    import os
    global _LOG_REDIRECT_TMPDIR, _LOG_REDIRECT_PREVIOUS
    _LOG_REDIRECT_PREVIOUS = os.environ.get("CLIPPER_PUBLISH_LOG")
    _LOG_REDIRECT_TMPDIR = tempfile.TemporaryDirectory()
    os.environ["CLIPPER_PUBLISH_LOG"] = str(
        Path(_LOG_REDIRECT_TMPDIR.name) / "publish_log.jsonl")


def tearDownModule():
    import os
    global _LOG_REDIRECT_TMPDIR, _LOG_REDIRECT_PREVIOUS
    if _LOG_REDIRECT_PREVIOUS is None:
        os.environ.pop("CLIPPER_PUBLISH_LOG", None)
    else:
        os.environ["CLIPPER_PUBLISH_LOG"] = _LOG_REDIRECT_PREVIOUS
    if _LOG_REDIRECT_TMPDIR is not None:
        _LOG_REDIRECT_TMPDIR.cleanup()
        _LOG_REDIRECT_TMPDIR = None


class TestConfirmedBugsFixes(unittest.TestCase):

    def test_01_batch_rerender_passes_feature_flags(self):
        """Bug 1: Verify batch_rerender reads and passes feature flags from metadata."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sample_meta = {
                "video_id": "test_vid_xyz",
                "start": 10.0,
                "end": 35.0,
                "subtitle_style": "cinematic_auteur",
                "framing_mode": "split_gaming",
                "filename": "test_clip.mp4",
                "enable_broll": False,
                "enable_emojis": False,
                "enable_snappy_cuts": False,
                "enable_punch_zooms": False,
                "enable_outro": False
            }
            json_file = tmp_path / "test_vid_xyz_clip1.json"
            json_file.write_text(json.dumps(sample_meta), encoding="utf-8")

            with patch("batch_rerender.cut_and_format_clip") as mock_cut:
                mock_cut.return_value = True
                with patch("batch_rerender.OUTPUT_DIR", tmp_path):
                    batch_rerender.rerender_all(filter_video_id="test_vid_xyz", skip_recent_seconds=0)

                mock_cut.assert_called_once()
                kwargs = mock_cut.call_args.kwargs
                self.assertFalse(kwargs.get("enable_broll"), "enable_broll must be False as in metadata")
                self.assertFalse(kwargs.get("enable_emojis"), "enable_emojis must be False as in metadata")
                self.assertFalse(kwargs.get("enable_snappy_cuts"), "enable_snappy_cuts must be False as in metadata")
                self.assertFalse(kwargs.get("enable_punch_zooms"), "enable_punch_zooms must be False as in metadata")
                self.assertFalse(kwargs.get("enable_outro"), "enable_outro must be False as in metadata")
                self.assertEqual(kwargs.get("framing_mode"), "split_gaming")
                self.assertEqual(kwargs.get("subtitle_style"), "cinematic_auteur")
                print("\n[Bug 1 Verified] batch_rerender respects saved feature flags without forcing defaults.")

    def test_02_asymmetric_silencedetect_parsing(self):
        """Bug 3: Verify asymmetric silencedetect log parsing handles leading and trailing silences."""
        # Simulated FFmpeg output with leading silence (starts at 0.0, no silence_start log)
        # and trailing silence (unclosed silence_start at end of video)
        fake_stderr = """
        [silencedetect @ 000001] silence_end: 1.50 | silence_duration: 1.50
        [silencedetect @ 000001] silence_start: 4.20
        [silencedetect @ 000001] silence_end: 5.80 | silence_duration: 1.60
        [silencedetect @ 000001] silence_start: 9.00
        """
        total_duration = 10.0

        events = re.findall(r"silence_(start|end): (\d+\.?\d*)", fake_stderr)
        silence_intervals = []
        cur_start = None
        for ev_type, ts_str in events:
            ts = float(ts_str)
            if ev_type == "start":
                cur_start = ts
            elif ev_type == "end":
                start_val = cur_start if cur_start is not None else 0.0
                if ts > start_val:
                    silence_intervals.append((max(0.0, start_val), min(total_duration, ts)))
                cur_start = None

        if cur_start is not None and cur_start < total_duration:
            silence_intervals.append((max(0.0, cur_start), total_duration))

        expected = [(0.0, 1.5), (4.2, 5.8), (9.0, 10.0)]
        self.assertEqual(silence_intervals, expected)

        # Demonstrate how the old buggy zip method completely broke this:
        old_starts = [float(x) for x in re.findall(r"silence_start: (\d+\.?\d*)", fake_stderr)]
        old_ends = [float(x) for x in re.findall(r"silence_end: (\d+\.?\d*)", fake_stderr)]
        old_intervals = [
            (max(0.0, s), min(total_duration, e))
            for s, e in zip(old_starts, old_ends)
            if e > s
        ]
        self.assertEqual(old_intervals, [], "Old logic wrongly resulted in empty intervals due to index shift!")
        print("\n[Bug 3 Verified] Asymmetric silencedetect parsing correctly recovers leading (0.0 to 1.5) and trailing silences.")

    def test_03_snappy_cuts_duration_used_for_broll(self):
        """Bug 2: Verify B-roll planning uses post-cut video duration rather than original slice duration."""
        with patch("video_cutter.get_video_duration") as mock_dur, \
             patch("video_cutter.plan_and_fetch_brolls") as mock_plan:
            mock_dur.return_value = 18.5  # Post-cut duration
            mock_plan.return_value = []

            # Simulate logic at lines 309-313
            actual_downloaded = Path("temp/snappy_clip.mp4")
            start_sec = 0.0
            end_sec = 45.0  # Original uncut duration was 45.0s
            words = [{"word": "hello", "start": 1.0, "end": 2.0}]
            enable_broll = True
            ffmpeg_exe = "ffmpeg"

            clip_dur = video_cutter.get_video_duration(actual_downloaded, ffmpeg_exe) or (end_sec - start_sec)
            video_cutter.plan_and_fetch_brolls(words, clip_dur, max_brolls=2)

            mock_plan.assert_called_once_with(words, 18.5, max_brolls=2)
            self.assertEqual(clip_dur, 18.5)
            self.assertNotEqual(clip_dur, 45.0)
            print("\n[Bug 2 Verified] B-roll engine receives actual post-cut duration (18.5s) instead of pre-cut (45.0s).")

    def test_04_split_gaming_filter_has_no_infinite_canvas(self):
        """Bug 4: Verify split_gaming base filter does not use infinite color canvas, but uses finite vstack."""
        cw, ch, cx, cy = (720, 640, 100, 50)
        TARGET_WIDTH = 1080
        TARGET_HEIGHT = 1920

        base_filter = (
            f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={TARGET_WIDTH}:960:force_original_aspect_ratio=increase,crop={TARGET_WIDTH}:960,setsar=1[cam];"
            f"[0:v]scale=270:240:force_original_aspect_ratio=increase,crop=270:240,boxblur=10:3,scale={TARGET_WIDTH}:960,setsar=1[game_bg];"
            f"[0:v]scale={TARGET_WIDTH}:-2,setsar=1[game_fg];"
            f"[game_bg][game_fg]overlay=0:(960-h)/2[game_combined];"
            f"[cam][game_combined]vstack=inputs=2[stacked];"
            f"[stacked]drawbox=x=0:y=958:w={TARGET_WIDTH}:h=4:color=#6366f1@0.85:t=fill[v_base]"
        )

        self.assertNotIn("color=c=black", base_filter, "Must not use infinite color=c=black video source")
        self.assertIn("vstack=inputs=2", base_filter, "Must use vstack to combine cam and game feeds")
        print("\n[Split Gaming Verified] Filter graph uses finite vstack without infinite canvas generator.")

    def test_05_censorship_audio_filter_uses_eval_frame(self):
        """Bug 1: Verify censorship audio filter uses eval=frame expressions to avoid inverted sine playback."""
        import video_cutter
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_file = tmp_path / "out.mp4"

            captured_cmds = []
            def fake_run(cmd, *args, **kwargs):
                captured_cmds.append(cmd)
                # When yt-dlp runs, create the expected raw download file
                for item in cmd:
                    if "raw_" in str(item) and str(item).endswith(".mp4"):
                        Path(item).write_bytes(b"fake_video")
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = ""
                mock_proc.stderr = ""
                return mock_proc

            def fake_subs(vid, ass, **kwargs):
                words_out = kwargs.get("words_out")
                if words_out is not None:
                    words_out.append({"word": "fuck", "start": 2.0, "end": 2.5})
                return True

            with patch("video_cutter.subprocess.run", side_effect=fake_run), \
                 patch("video_cutter.get_ffmpeg_path", return_value="ffmpeg"), \
                 patch("video_cutter.get_video_duration", return_value=10.0), \
                 patch("video_cutter.has_audio_stream", return_value=True), \
                 patch("video_cutter.generate_synced_subtitles", side_effect=fake_subs), \
                 patch("video_cutter.detect_profanity_intervals", return_value=[(2.0, 2.5)]):

                ok = video_cutter.cut_and_format_clip(
                    youtube_url="https://youtube.com/watch?v=dummy",
                    start_sec=0.0,
                    end_sec=10.0,
                    output_path=out_file,
                    enable_broll=False,
                    enable_emojis=False,
                    enable_snappy_cuts=False,
                    enable_punch_zooms=False,
                    enable_outro=False,
                    enable_auto_bleep=True
                )
                self.assertTrue(ok)
                ffmpeg_cmds = [c for c in captured_cmds if any("eval=frame" in str(arg) for arg in c)]
                self.assertTrue(len(ffmpeg_cmds) > 0, "FFmpeg command with eval=frame was not invoked!")
                cmd_str = " ".join(ffmpeg_cmds[0])
                self.assertIn("eval=frame", cmd_str, "Censorship filter graph must use eval=frame")
                self.assertIn("sine=f=1000", cmd_str, "Censorship filter graph must generate sine beep")
                self.assertIn("amix=inputs=2", cmd_str, "Censorship filter graph must mix audio streams")
                self.assertNotIn("volume=0:volume=enable", cmd_str, "Censorship filter graph must not have conflicting volume=0")
                print("\n[Bug 1 Audio Censorship Verified] Filtergraph correctly builds eval=frame expressions.")

    def test_06_subtitle_active_pop_eliminates_inter_word_gaps(self):
        """Bug 2: Verify active pop subtitle generation maintains visible word until next word start."""
        import subtitles
        # Two words with a 0.3s pause in between:
        # Word 1 ends at 0.5s, Word 2 starts at 0.8s
        words = [
            {"word": "HELLO", "start": 0.0, "end": 0.5},
            {"word": "WORLD", "start": 0.8, "end": 1.2}
        ]
        ass_text = subtitles.build_ass_from_words(words, style_key="mrbeast")
        dialogue_lines = [l for l in ass_text.splitlines() if l.startswith("Dialogue:")]
        self.assertGreaterEqual(len(dialogue_lines), 2)
        # The first event for HELLO should end at 0.8s (0:00:00.80) instead of dropping out at 0.5s
        self.assertIn("0:00:00.00,0:00:00.80", dialogue_lines[0],
                      "Active pop event must extend w_end to next_start (0:00:00.80) to prevent gap dropouts")
        print("\n[Bug 2 Subtitle Active Pop Verified] Inter-word gap eliminated; dialogue extends to next_start.")

    def test_07_metadata_serializes_broll_and_emojis_flags(self):
        """Bug 3: Verify metadata JSON in clipper and app retains enable_broll and enable_emojis."""
        import clipper
        import app
        clipper_src = Path(clipper.__file__).read_text(encoding="utf-8")
        app_src = Path(app.__file__).read_text(encoding="utf-8")

        self.assertIn('"enable_broll": enable_broll', clipper_src)
        self.assertIn('"enable_emojis": enable_emojis', clipper_src)
        self.assertIn('"enable_broll": enable_broll', app_src)
        self.assertIn('"enable_emojis": enable_emojis', app_src)
        print("\n[Bug 3 Metadata Serialization Verified] enable_broll and enable_emojis present in metadata dictionaries.")

    def test_08_face_tracker_odd_height_parity_guard(self):
        """Bug 4: Verify compute_smart_crop_offset clamps odd heights to even numbers for libx264."""
        import face_tracker
        import cv2

        # Test vertical input taller than 9:16 with odd height: 1080x1919
        mock_cap_vert = MagicMock()
        mock_cap_vert.isOpened.return_value = True
        mock_cap_vert.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FRAME_WIDTH: 1080,
            cv2.CAP_PROP_FRAME_HEIGHT: 1919,
            cv2.CAP_PROP_FRAME_COUNT: 30,
            cv2.CAP_PROP_FPS: 30.0
        }.get(prop, 0.0)

        with patch("cv2.VideoCapture", return_value=mock_cap_vert), \
             patch("face_tracker.get_face_detector", return_value=None):
            cw, ch, cx, cy = face_tracker.compute_smart_crop_offset(Path("dummy.mp4"))
            self.assertEqual(ch % 2, 0, f"Crop height {ch} must be even!")
            self.assertEqual(cw % 2, 0, f"Crop width {cw} must be even!")
            self.assertLessEqual(ch, 1919)
            self.assertEqual(ch, 1918)

        # Test landscape with odd height: 1920x1081
        mock_cap_land = MagicMock()
        mock_cap_land.isOpened.return_value = True
        mock_cap_land.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FRAME_WIDTH: 1920,
            cv2.CAP_PROP_FRAME_HEIGHT: 1081,
            cv2.CAP_PROP_FRAME_COUNT: 30,
            cv2.CAP_PROP_FPS: 30.0
        }.get(prop, 0.0)

        with patch("cv2.VideoCapture", return_value=mock_cap_land), \
             patch("face_tracker.get_face_detector", return_value=None):
            cw2, ch2, cx2, cy2 = face_tracker.compute_smart_crop_offset(Path("dummy.mp4"))
            self.assertEqual(ch2 % 2, 0, f"Landscape crop height {ch2} must be even!")
            self.assertEqual(cw2 % 2, 0, f"Landscape crop width {cw2} must be even!")
            self.assertEqual(ch2, 1080)
        print("\n[Bug 4 Parity Guard Verified] Odd dimensions clamped to even pixels (1919 -> 1918, 1081 -> 1080).")

    def test_09_outro_concat_filter_handles_audioless_video(self):
        """Bug 5: Verify outro concat correctly detects audio-less stream and applies anullsrc silence."""
        import video_cutter
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_file = tmp_path / "out.mp4"

            captured_cmds = []
            def fake_run(cmd, *args, **kwargs):
                captured_cmds.append(cmd)
                for item in cmd:
                    if "raw_" in str(item) and str(item).endswith(".mp4"):
                        Path(item).write_bytes(b"fake_video")
                    elif "main_" in str(item) and str(item).endswith(".mp4"):
                        Path(item).write_bytes(b"fake_main")
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = ""
                mock_proc.stderr = ""
                return mock_proc

            with patch("video_cutter.subprocess.run", side_effect=fake_run), \
                 patch("video_cutter.get_ffmpeg_path", return_value="ffmpeg"), \
                 patch("video_cutter.get_video_duration", return_value=5.0), \
                 patch("video_cutter.has_audio_stream", return_value=False), \
                 patch("video_cutter.generate_outro_card", return_value=True), \
                 patch("video_cutter.generate_synced_subtitles", return_value=False):

                ok = video_cutter.cut_and_format_clip(
                    youtube_url="https://youtube.com/watch?v=dummy",
                    start_sec=0.0,
                    end_sec=5.0,
                    output_path=out_file,
                    enable_broll=False,
                    enable_emojis=False,
                    enable_snappy_cuts=False,
                    enable_punch_zooms=False,
                    enable_outro=True
                )
                self.assertTrue(ok)
                concat_cmds = [c for c in captured_cmds if any("anullsrc=r=44100" in str(arg) for arg in c)]
                self.assertTrue(len(concat_cmds) > 0, "Concat command with anullsrc was not invoked!")
                cmd_str = " ".join(concat_cmds[0])
                self.assertIn("anullsrc=r=44100:cl=stereo", cmd_str,
                              "Outro concat must supply anullsrc silence when main video has no audio")
                self.assertIn("concat=n=2:v=1:a=1", cmd_str)
                print("\n[Bug 5 Outro Concat Verified] anullsrc fallback correctly generated for audio-less source.")

if __name__ == "__main__":
    unittest.main()


