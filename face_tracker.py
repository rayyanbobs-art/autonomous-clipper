import os
import math
import threading
import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

_THREAD_LOCAL = threading.local()
_GLOBAL_CONTINUITY_METADATA: Dict[str, Any] = {}

def get_face_detector(width: int = 640, height: int = 360, score_threshold: float = 0.35):
    if not MODEL_PATH.exists():
        return None
    try:
        detector = getattr(_THREAD_LOCAL, "detector", None)
        detector_size = getattr(_THREAD_LOCAL, "detector_size", None)
        detector_thresh = getattr(_THREAD_LOCAL, "detector_thresh", None)
        if detector is None or detector_size != (width, height) or detector_thresh != score_threshold:
            detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (width, height), score_threshold=score_threshold)
            _THREAD_LOCAL.detector = detector
            _THREAD_LOCAL.detector_size = (width, height)
            _THREAD_LOCAL.detector_thresh = score_threshold
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
    step = max(1, int(fps * 0.15))  # Sample every ~150ms
    threshold = 28.0  # Mean absolute difference threshold for a cut
    
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    current_frame = 0
    while current_frame < total_frames:
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
        # Fast-forward frames sequentially without slow keyframe seeks
        for _ in range(step - 1):
            if not cap.grab():
                break
        
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

        # Scale detection input to optimal YuNet resolution (max dimension <= 640)
        scale_ratio = 1.0
        det_w, det_h = orig_w, orig_h
        if max(orig_w, orig_h) > 640:
            scale_ratio = 640.0 / max(orig_w, orig_h)
            det_w = int(orig_w * scale_ratio)
            det_h = int(orig_h * scale_ratio)

        detector = get_face_detector(det_w, det_h)
        if detector is None:
            return (crop_w, crop_h, str(default_x), crop_y)

        # Step 1: Detect scene/camera cuts
        cuts = detect_scene_cuts(cap, fps, total_frames, duration)

        # Step 2: Analyze speaker face position within each scene
        scene_crops = []
        last_known_x = default_x
        has_seen_face = False
        all_face_cys = []

        for idx in range(len(cuts) - 1):
            t_start = cuts[idx]
            t_end = cuts[idx + 1]
            scene_dur = t_end - t_start
            
            # Sample 2-5 frames evenly across this shot
            num_samples = min(5, max(2, int(scene_dur * 2)))
            sample_times = np.linspace(t_start + 0.05, max(t_start + 0.1, t_end - 0.05), num=num_samples)
            
            faces_in_scene = []
            # Motion salience (fallback when no face is visible: hoods, beanies, looking down)
            motion_cols = None
            motion_pairs = 0
            prev_gray = None
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
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                if prev_gray is not None and prev_gray.shape == gray.shape:
                    cols = cv2.absdiff(gray, prev_gray).sum(axis=0).astype(np.float64)
                    motion_cols = cols if motion_cols is None else motion_cols + cols
                    motion_pairs += 1
                prev_gray = gray
                try:
                    _, faces = detector.detect(small_frame)
                except Exception:
                    continue
                if faces is not None and len(faces) > 0:
                    inv_s = 1.0 / scale_ratio
                    for f in faces:
                        fx, fy, fw, fh = f[0] * inv_s, f[1] * inv_s, f[2] * inv_s, f[3] * inv_s
                        area = fw * fh
                        conf = f[-1]
                        if conf >= 0.35:
                            faces_in_scene.append((area, fx, fy, fw, fh))
                            
            if faces_in_scene:
                # Sort by face area descending (largest face = active speaker in foreground)
                faces_in_scene.sort(key=lambda x: x[0], reverse=True)
                primary_face = faces_in_scene[0]
                primary_cx = primary_face[1] + (primary_face[3] / 2)
                primary_cy = primary_face[2] + (primary_face[4] / 2)

                # Cluster detections around the primary dominant speaker (within half crop width)
                cluster_faces = [
                    f for f in faces_in_scene
                    if abs((f[1] + (f[3] / 2)) - primary_cx) <= (crop_w * 0.5)
                ]
                face_cx = float(np.median([f[1] + (f[3] / 2) for f in cluster_faces])) if cluster_faces else float(primary_cx)
                face_cy = float(np.median([f[2] + (f[4] / 2) for f in cluster_faces])) if cluster_faces else float(primary_cy)
                
                face_min_x = min(f[1] for f in cluster_faces)
                face_max_x = max(f[1] + f[3] for f in cluster_faces)
                face_w = max(f[3] for f in cluster_faces)
                face_h = max(f[4] for f in cluster_faces)

                # Extended subject zone: project downward and outward to capture upper torso,
                # arms, tools, and hand gestures (1.8x face width horizontally, 2.5x face height downward)
                torso_half_w = (face_w * 1.8) / 2.0
                subject_min_x = max(0, min(face_min_x, face_cx - torso_half_w))
                subject_max_x = min(orig_w, max(face_max_x, face_cx + torso_half_w))
                subject_cx = (subject_min_x + subject_max_x) / 2.0

                ideal_x = int(subject_cx - (crop_w / 2))
                clamped_x = max(0, min(orig_w - crop_w, ideal_x))
                
                # Protect subject and torso bounding box so subject is never cut off
                if (subject_max_x - subject_min_x) <= crop_w:
                    if clamped_x > subject_min_x:
                        clamped_x = max(0, int(subject_min_x))
                    if (clamped_x + crop_w) < subject_max_x:
                        clamped_x = min(orig_w - crop_w, int(subject_max_x - crop_w))

                # Hard margin for face box itself (ensure face is never near crop edge)
                margin = 35
                if clamped_x > (face_min_x - margin) and (face_min_x - margin) >= 0:
                    clamped_x = max(0, int(face_min_x - margin))
                if (clamped_x + crop_w) < (face_max_x + margin) and (face_max_x + margin) <= orig_w:
                    clamped_x = min(orig_w - crop_w, int(face_max_x + margin - crop_w))

                if clamped_x % 2 != 0:
                    if (clamped_x + crop_w) >= orig_w:
                        clamped_x -= 1
                    else:
                        clamped_x += 1
                clamped_x = max(0, min(orig_w - crop_w, clamped_x))
                
                last_known_x = clamped_x
                has_seen_face = True
                all_face_cys.append(face_cy)
                scene_crops.append((t_start, t_end, clamped_x, True, face_w))
            else:
                # No face detected in this scene (hood, beanie, sunglasses, looking down).
                # Prefer the motion-salience centroid (hands, tools, moving body) over the
                # static frame center; mean per-pixel diff must clear a sensor-noise floor.
                motion_x = None
                if motion_cols is not None and motion_pairs > 0:
                    total_motion = float(motion_cols.sum())
                    if total_motion / (motion_pairs * det_w * det_h) >= 2.0:
                        centroid_det = float((motion_cols * np.arange(len(motion_cols))).sum() / total_motion)
                        motion_x = int(round(centroid_det / scale_ratio - crop_w / 2))
                if motion_x is not None:
                    # Blend with the last known speaker anchor so a passing object can't yank the frame
                    target_x = int(round(0.5 * motion_x + 0.5 * last_known_x)) if has_seen_face else motion_x
                # Otherwise retain last_known_x with temporal dampening rather than jumping abruptly to center
                elif has_seen_face:
                    # If this scene is prolonged (> 4s without a face), gently drift towards default_x
                    if scene_dur > 4.0:
                        target_x = int(round(last_known_x * 0.75 + default_x * 0.25))
                    else:
                        target_x = last_known_x
                else:
                    target_x = default_x
                if target_x % 2 != 0:
                    if (target_x + crop_w) >= orig_w:
                        target_x -= 1
                    else:
                        target_x += 1
                target_x = max(0, min(orig_w - crop_w, target_x))
                scene_crops.append((t_start, t_end, target_x, False, 0.0))

        if not scene_crops:
            return (crop_w, crop_h, str(default_x), crop_y)

        # Dynamic headroom calculation: anchor face in upper third (35% from top)
        if all_face_cys and orig_h > crop_h:
            median_face_cy = float(np.median(all_face_cys))
            ideal_y = int(median_face_cy - (crop_h * 0.35))
            crop_y = max(0, min(orig_h - crop_h, ideal_y))
            if crop_y % 2 != 0:
                crop_y -= 1

        # Identify consecutive shots with static speaker perspective (|X_{i+1} - X_i| <= 25px)
        # Apply 30-degree focal punch-in zoom on the second take to eliminate jump-cut glitch
        focal_scale_intervals = []
        for i in range(len(scene_crops) - 1):
            sc_curr = scene_crops[i]
            sc_next = scene_crops[i + 1]
            has_face_curr = sc_curr[3] if len(sc_curr) > 3 else True
            has_face_next = sc_next[3] if len(sc_next) > 3 else True
            if has_face_curr and has_face_next and abs(sc_next[2] - sc_curr[2]) <= 25:
                focal_scale_intervals.append((sc_next[0], sc_next[1]))

        # Step 3: Merge adjacent scenes with identical or near-identical crop positions (< 35px difference)
        merged = []
        for sc in scene_crops:
            if not merged:
                merged.append(list(sc))
            else:
                last = merged[-1]
                if abs(sc[2] - last[2]) < 35:
                    last[1] = sc[1]  # Extend existing segment
                else:
                    merged.append(list(sc))

        # Enforce Eye-Trace Continuity:
        # Consecutive shots must not jump the focal center (X-axis) by more than 15% of frame width
        # (Delta X <= 0.15 * crop_w) without dampening
        max_jump = int(round(0.15 * crop_w))
        for i in range(len(merged) - 1):
            x_curr = merged[i][2]
            x_next = merged[i + 1][2]
            delta = x_next - x_curr
            if abs(delta) > max_jump:
                sign = 1 if delta > 0 else -1
                dampened_delta = sign * (max_jump + int(round(0.35 * (abs(delta) - max_jump))))
                new_x = x_curr + dampened_delta
                if new_x % 2 != 0:
                    new_x = new_x - 1 if (new_x + crop_w) >= orig_w else new_x + 1
                new_x = max(0, min(orig_w - crop_w, new_x))
                merged[i + 1][2] = new_x

        # Step 4: Construct dynamic FFmpeg expression with 8-frame linear interpolation across cuts
        if len(merged) == 1:
            single_x = merged[0][2]
            print(f"  [Face Tracker] Static framing across video -> crop_x: {single_x}")
            _save_shot_continuity(video_path, merged, focal_scale_intervals, crop_w, crop_y)
            return (crop_w, crop_h, str(single_x), crop_y)

        dynamic_expr = str(merged[-1][2])
        for i in range(len(merged) - 2, -1, -1):
            m_curr = merged[i]
            m_next = merged[i + 1]
            t_cut = m_curr[1]
            x_curr = m_curr[2]
            x_next = m_next[2]
            dur_next = m_next[1] - t_cut
            trans_dur = min(0.25, dur_next * 0.5)

            if trans_dur >= 0.05 and x_curr != x_next:
                t_trans_end = t_cut + trans_dur
                dynamic_expr = (
                    f"if(lt(t,{t_cut:.2f}),{x_curr},"
                    f"if(lt(t,{t_trans_end:.2f}),{x_curr}+({x_next}-{x_curr})*(t-{t_cut:.2f})/{trans_dur:.2f},"
                    f"{dynamic_expr}))"
                )
            else:
                dynamic_expr = f"if(lt(t,{t_cut:.2f}),{x_curr},{dynamic_expr})"

        print(f"  [Face Tracker] Generated dynamic eye-trace smoothed crop expression across {len(merged)} shot segments (range: 0..{orig_w - crop_w}).")
        _save_shot_continuity(video_path, merged, focal_scale_intervals, crop_w, crop_y)
        return (crop_w, crop_h, dynamic_expr, crop_y)
    finally:
        cap.release()

def _save_shot_continuity(
    video_path: Optional[Path],
    merged_segments: list,
    focal_scale_intervals: list,
    crop_w: int,
    crop_y: int
) -> None:
    global _GLOBAL_CONTINUITY_METADATA
    cuts = []
    for idx, seg in enumerate(merged_segments):
        t_start, t_end, x_val = seg[0], seg[1], seg[2]
        has_face = seg[3] if len(seg) > 3 else True
        fw = seg[4] if len(seg) > 4 else 0.0
        if not has_face:
            stype = "wide"
        elif fw > (crop_w * 0.40):
            stype = "medium_close_up"
        else:
            stype = "talking_head"

        if idx == len(merged_segments) - 1:
            trans = {"type": "CUT_ON_ACTION", "audio_bleed_ms": 0}
        elif idx % 2 == 0:
            trans = {"type": "L_CUT", "audio_bleed_ms": 200}
        else:
            trans = {"type": "J_CUT", "audio_bleed_ms": 200}

        cuts.append({
            "cut_index": idx,
            "source_in": round(float(t_start), 3),
            "source_out": round(float(t_end), 3),
            "shot_type": stype,
            "focal_anchor": {"x": int(x_val), "y": int(crop_y)},
            "transition_out": trans
        })

    key = str(video_path.resolve()) if video_path else "default"
    _GLOBAL_CONTINUITY_METADATA[key] = {
        "cuts": cuts,
        "focal_scale_intervals": focal_scale_intervals
    }
    _GLOBAL_CONTINUITY_METADATA["last"] = _GLOBAL_CONTINUITY_METADATA[key]

def get_shot_continuity_metadata(video_path: Optional[Path] = None) -> Dict[str, Any]:
    global _GLOBAL_CONTINUITY_METADATA
    empty = {"cuts": [], "focal_scale_intervals": []}
    if video_path:
        # Never hand back ANOTHER video's shots: its focal intervals would become
        # mid-shot 1.15x jump zooms on this clip.
        return _GLOBAL_CONTINUITY_METADATA.get(str(video_path.resolve()), empty)
    return _GLOBAL_CONTINUITY_METADATA.get("last", empty)

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

