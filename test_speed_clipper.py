import unittest
from pathlib import Path
from video_cutter import (
    get_ffmpeg_path,
    get_video_duration,
    apply_snappy_silence_cuts,
    detect_audio_energy_spikes,
    get_bold_font_path,
    generate_outro_card
)

SAMPLE_VIDEO = Path("d:/software/myst/autonomous_clipper/temp/ishowspeed_sample.mp4")
TEMP_DIR = Path("d:/software/myst/autonomous_clipper/temp")

class TestSpeedClipperFeatures(unittest.TestCase):

    def test_01_ffmpeg_and_font(self):
        """Test ffmpeg detection and system bold font detection."""
        ffmpeg_exe = get_ffmpeg_path()
        self.assertTrue(Path(ffmpeg_exe).exists(), "FFmpeg binary must exist.")
        font_path = get_bold_font_path()
        self.assertIsNotNone(font_path, "A system bold font should be detected.")
        print(f"\n[Test 1] FFmpeg: {ffmpeg_exe} | Font: {font_path}")

    def test_02_video_duration(self):
        """Test retrieving video duration via ffprobe."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")
        dur = get_video_duration(SAMPLE_VIDEO)
        self.assertGreater(dur, 40.0, "Sample video duration should be ~47.7 seconds.")
        print(f"\n[Test 2] Sample Video Duration: {dur:.2f}s")

    def test_03_detect_audio_energy_spikes(self):
        """Test vocal energy spike detection for punch-in zoom jump cuts."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")
        spikes = detect_audio_energy_spikes(SAMPLE_VIDEO, spike_db_threshold=7.0)
        self.assertIsInstance(spikes, list)
        self.assertGreater(len(spikes), 0, "Should detect reaction vocal peaks in high-energy clip.")
        for start, end in spikes:
            self.assertGreater(end, start)
            self.assertAlmostEqual(end - start, 1.2, delta=0.01)
        print(f"\n[Test 3] Detected {len(spikes)} energy spikes: {spikes[:3]}...")

    def test_04_generate_outro_card(self):
        """Test 1-second high-contrast SUBSCRIBE outro card generation."""
        out_outro = TEMP_DIR / "unit_test_outro.mp4"
        if out_outro.exists():
            out_outro.unlink()
        ok = generate_outro_card(out_outro, duration_sec=1.0)
        self.assertTrue(ok, "Outro card generation should succeed.")
        self.assertTrue(out_outro.exists())
        dur = get_video_duration(out_outro)
        self.assertAlmostEqual(dur, 1.0, delta=0.1)
        print(f"\n[Test 4] Outro Card generated successfully. Duration: {dur:.2f}s")

    def test_05_apply_snappy_silence_cuts(self):
        """Test automatic dead-air silence removal."""
        if not SAMPLE_VIDEO.exists():
            self.skipTest("Sample video not found.")
        out_snappy = TEMP_DIR / "unit_test_snappy.mp4"
        if out_snappy.exists():
            out_snappy.unlink()
        ok = apply_snappy_silence_cuts(SAMPLE_VIDEO, out_snappy, min_silence_sec=0.28, silence_thresh_db=-28.0)
        self.assertTrue(ok, "Silence jump cuts should succeed.")
        self.assertTrue(out_snappy.exists())
        orig_dur = get_video_duration(SAMPLE_VIDEO)
        cut_dur = get_video_duration(out_snappy)
        self.assertLess(cut_dur, orig_dur, "Trimmed video duration should be less than original.")
        print(f"\n[Test 5] Dead-air removed: {orig_dur:.2f}s -> {cut_dur:.2f}s (saved {orig_dur - cut_dur:.2f}s)")

    def test_06_compute_gaming_split_crop(self):
        """Test compute_gaming_split_crop facecam tracking."""
        from face_tracker import compute_gaming_split_crop
        crop = compute_gaming_split_crop(SAMPLE_VIDEO, target_w=1080, target_cam_h=960)
        self.assertIsInstance(crop, tuple)
        self.assertEqual(len(crop), 4)
        cw, ch, cx, cy = crop
        self.assertGreater(cw, 0)
        self.assertGreater(ch, 0)
        self.assertGreaterEqual(cx, 0)
        self.assertGreaterEqual(cy, 0)
        print(f"\n[Test 6] Gaming Split Crop computed: w={cw}, h={ch}, x={cx}, y={cy}")

if __name__ == "__main__":
    unittest.main()
