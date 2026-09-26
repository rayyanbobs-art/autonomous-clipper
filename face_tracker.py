import os
import math
import threading
import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

_THREAD_LOCAL = threading.local()

def get_face_detector(width: int = 640, height: int = 360):
    if not MODEL_PATH.exists():
        return None
    try:
        detector = getattr(_THREAD_LOCAL, "detector", None)
        detector_size = getattr(_THREAD_LOCAL, "detector_size", None)
        if detector is None or detector_size != (width, height):
            detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (width, height), score_threshold=0.65)
            _THREAD_LOCAL.detector = detector
            _THREAD_LOCAL.detector_size = (width, height)
        return detector
    except Exception as e:
        print(f"  [Face Tracker] Error initializing detector: {e}")
        return None

def detect_scene_cuts(cap: cv2.VideoCapture, fps: float, total_frames: int, duration: float) -> List[float]:
    """
    Detects shot transitions/camera cuts using fast downscaled frame differencing.
    Returns a sorted list of scene boundary timestamps [0.0, ..., duration].
    """
    if fps is None or math.isnan(fps) or fps <= 0:
        fps = 30.0
    cuts = [0.0]
    prev_gray = None
    step = max(1, int(fps * 0.12))  # Sample every ~120ms
    threshold = 28.0  # Mean absolute difference threshold for a cut
    
    current_frame = 0
    while current_frame < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        
        # 160x90 thumbnail for microsecond-fast comparison
        small = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        if prev_gray is not None:
            diff = np.mean(cv2.absdiff(gray, prev_gray))
            if diff > threshold:
                t = round(current_frame / fps, 2)
                # Enforce minimum shot duration of 0.8s to avoid rapid jumping on flashes
                if t - cuts[-1] >= 0.8:
                    cuts.append(t)
        prev_gray = gray
        current_frame += step
        
    cuts.append(round(duration, 2))
    return cuts

def compute_smart_crop_offset(video_path: Path, target_w: int = 1080, target_h: int = 1920) -> Tuple[int, int, str, int]:
    """
    Analyzes video using YuNet face tracking with per-scene shot boundary detection.
    Computes an optimal, dynamic 9:16 crop expression for FFmpeg that keeps speakers,
    facecams, and gameplay action perfectly centered across camera cuts.
    Returns: (crop_w, crop_h, crop_x_expr, crop_y)
    """
    fb_h = target_h
    fb_w = int(target_h * 9 / 16)
    if fb_w % 2 != 0:
        fb_w += 1
    fb_x = "(iw-ow)/2"

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return (fb_w, fb_h, fb_x, 0)

    try:
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if orig_w <= 0 or orig_h <= 0:
            ret, frame = cap.read()
            if ret and frame is not None:
                orig_h, orig_w = frame.shape[:2]
            else:
                return (fb_w, fb_h, fb_x, 0)

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        raw_fps = cap.get(cv2.CAP_PROP_FPS)
        fps = 30.0 if raw_fps is None or math.isnan(raw_fps) or raw_fps <= 0 else float(raw_fps)
        duration = (total_frames / fps) if fps > 0 else 0.0

        # Calculate 9:16 crop dimensions on source resolution
        if (orig_w / orig_h) < (9 / 16):
            # Taller than 9:16 (e.g. smartphone recordings, reels at 1080x2400)
            crop_w = orig_w
            if crop_w % 2 != 0:
                crop_w -= 1
            crop_h = int(crop_w * 16 / 9)
            if crop_h % 2 != 0:
                crop_h -= 1
            if crop_h > orig_h:
                crop_h = orig_h
            if crop_h % 2 != 0:
                crop_h -= 1
            crop_y = max(0, (orig_h - crop_h) // 2)
            if crop_y % 2 != 0:
                crop_y -= 1
        else:
            # Standard landscape or wider than 9:16 (orig_w / orig_h >= 9 / 16)
            crop_h = orig_h
            if crop_h % 2 != 0:
                crop_h -= 1
            crop_w = int(crop_h * 9 / 16)
            if crop_w % 2 != 0:
                crop_w += 1
            if crop_w > orig_w:
                crop_w = orig_w
                if crop_w % 2 != 0:
                    crop_w -= 1
            crop_y = 0

        default_x = max(0, (orig_w - crop_w) // 2)
        if default_x % 2 != 0:
            default_x += 1
        if (default_x + crop_w) > orig_w:
            default_x = max(0, orig_w - crop_w)

        detector = get_face_detector(orig_w, orig_h)
        if detector is None:
            return (crop_w, crop_h, str(default_x), crop_y)

        # Step 1: Detect scene/camera cuts
        cuts = detect_scene_cuts(cap, fps, total_frames, duration)

        # Step 2: Analyze speaker face position within each scene
        scene_crops = []
        for idx in range(len(cuts) - 1):
            t_start = cuts[idx]
            t_end = cuts[idx + 1]
            scene_dur = t_end - t_start
            
            # Sample 2-5 frames evenly across this shot
            num_samples = min(5, max(2, int(scene_dur * 2)))
            sample_times = np.linspace(t_start + 0.05, max(t_start + 0.1, t_end - 0.05), num=num_samples)
            
            faces_in_scene = []
            for st in sample_times:
                f_idx = min(total_frames - 1, int(st * fps))
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                fh, fw = frame.shape[:2]
                if (fw, fh) != (orig_w, orig_h):
                    detector = get_face_detector(fw, fh)
                if detector is None:
                    continue
                try:
                    _, faces = detector.detect(frame)
                except Exception:
                    continue
                if faces is not None and len(faces) > 0:
                    for f in faces:
                        area = f[2] * f[3]
                        conf = f[-1]
                        if conf >= 0.65:
                            faces_in_scene.append((area, f[0], f[1], f[2], f[3]))
                            
            if faces_in_scene:
                # Sort by face area descending (largest face = active speaker in foreground)
                faces_in_scene.sort(key=lambda x: x[0], reverse=True)
                primary_cx = faces_in_scene[0][1] + (faces_in_scene[0][3] / 2)

                # Cluster detections around the primary dominant speaker (within half crop width)
                # This prevents median calculation from landing on dead space between two distant speakers
                cluster_centers = [
                    f[1] + (f[3] / 2)
                    for f in faces_in_scene
                    if abs((f[1] + (f[3] / 2)) - primary_cx) <= (crop_w * 0.5)
                ]
                face_cx = float(np.median(cluster_centers)) if cluster_centers else float(primary_cx)
                
                ideal_x = int(face_cx - (crop_w / 2))
                clamped_x = max(0, min(orig_w - crop_w, ideal_x))
                
                # Snap near-boundary facecams (e.g. gaming overlays) cleanly to the edge
                if clamped_x < 35:
                    clamped_x = 0
                elif (orig_w - crop_w - clamped_x) < 35:
                    clamped_x = orig_w - crop_w
                    
                if clamped_x % 2 != 0:
                    if (clamped_x + crop_w) >= orig_w:
                        clamped_x -= 1
                    else:
                        clamped_x += 1
                clamped_x = max(0, min(orig_w - crop_w, clamped_x))
                    
                scene_crops.append((t_start, t_end, clamped_x))
            else:
                # No face detected in this scene: safe action center crop (e.g. gameplay, drone shot, B-roll)
                scene_crops.append((t_start, t_end, default_x))

        if not scene_crops:
            return (crop_w, crop_h, str(default_x), crop_y)

        # Step 3: Merge adjacent scenes with identical or near-identical crop positions (< 25px difference)
        merged = []
        for sc in scene_crops:
            if not merged:
                merged.append(list(sc))
            else:
                last = merged[-1]
                if abs(sc[2] - last[2]) < 25:
                    last[1] = sc[1]  # Extend existing segment
                else:
                    merged.append(list(sc))

        # Step 4: Construct dynamic FFmpeg expression
        if len(merged) == 1:
            single_x = merged[0][2]
            print(f"  [Face Tracker] Static framing across video -> crop_x: {single_x}")
            return (crop_w, crop_h, str(single_x), crop_y)

        dynamic_expr = str(merged[-1][2])
        for m in reversed(merged[:-1]):
            t_end, x_val = m[1], m[2]
            dynamic_expr = f"if(lt(t,{t_end:.2f}),{x_val},{dynamic_expr})"
        print(f"  [Face Tracker] Generated dynamic crop expression across {len(merged)} shot segments (range: 0..{orig_w - crop_w}).")
        return (crop_w, crop_h, dynamic_expr, crop_y)
    finally:
        cap.release()

def compute_gaming_split_crop(
    video_path: Path,
    target_w: int = 1080,
    target_cam_h: int = 960
) -> Tuple[int, int, int, int]:
    """
    Computes a focused facecam crop (width, height, x, y) around the streamer/creator
    for the top half of a 9:16 vertical split-screen Short.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return (target_w, target_cam_h, 0, 0)

    try:
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if orig_w <= 0 or orig_h <= 0:
            ret, frame = cap.read()
            if ret and frame is not None:
                orig_h, orig_w = frame.shape[:2]
            else:
                return (target_w, target_cam_h, 0, 0)

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        raw_fps = cap.get(cv2.CAP_PROP_FPS)
        fps = 30.0 if raw_fps is None or math.isnan(raw_fps) or raw_fps <= 0 else float(raw_fps)
        duration = (total_frames / fps) if fps > 0 else 5.0

        scale_ratio = 1.0
        det_w, det_h = orig_w, orig_h
        if max(orig_w, orig_h) > 640:
            scale_ratio = 640.0 / max(orig_w, orig_h)
            det_w = int(orig_w * scale_ratio)
            det_h = int(orig_h * scale_ratio)

        detector = get_face_detector(det_w, det_h)
        faces = []

        if detector is not None and total_frames > 0:
            num_samples = min(6, max(3, int(duration)))
            sample_times = np.linspace(0.5, max(0.8, duration - 0.5), num=num_samples)
            for st in sample_times:
                f_idx = min(total_frames - 1, int(st * fps))
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                if scale_ratio < 1.0:
                    small_frame = cv2.resize(frame, (det_w, det_h), interpolation=cv2.INTER_AREA)
                else:
                    small_frame = frame
                try:
                    _, det = detector.detect(small_frame)
                    if det is not None and len(det) > 0:
                        det_sorted = sorted(det, key=lambda f: f[2] * f[3], reverse=True)
                        best = det_sorted[0]
                        inv_s = 1.0 / scale_ratio
                        faces.append((best[0] * inv_s, best[1] * inv_s, best[2] * inv_s, best[3] * inv_s))
                except Exception:
                    continue

        if faces:
            avg_x = sum(f[0] + f[2] / 2 for f in faces) / len(faces)
            avg_y = sum(f[1] + f[3] / 2 for f in faces) / len(faces)
            avg_h = sum(f[3] for f in faces) / len(faces)
        else:
            avg_x = orig_w * 0.5
            avg_y = orig_h * 0.4
            avg_h = orig_h * 0.25

        cam_h = int(min(orig_h, max(avg_h * 2.8, orig_h * 0.50)))
        if cam_h % 2 != 0:
            cam_h -= 1
        cam_w = int(cam_h * (target_w / target_cam_h))
        if cam_w > orig_w:
            cam_w = orig_w
            cam_h = int(cam_w * (target_cam_h / target_w))
        if cam_w % 2 != 0:
            cam_w -= 1
        if cam_h % 2 != 0:
            cam_h -= 1

        cam_x = int(avg_x - (cam_w / 2))
        cam_x = max(0, min(orig_w - cam_w, cam_x))
        cam_y = int(avg_y - (cam_h * 0.38))
        cam_y = max(0, min(orig_h - cam_h, cam_y))
        if cam_x % 2 != 0:
            cam_x -= 1
        if cam_y % 2 != 0:
            cam_y -= 1

        print(f"  [Gaming Split] Facecam crop -> x:{cam_x}, y:{cam_y}, w:{cam_w}, h:{cam_h} (source: {orig_w}x{orig_h})")
        return (cam_w, cam_h, cam_x, cam_y)
    finally:
        cap.release()

