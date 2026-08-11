from __future__ import annotations

from dataclasses import dataclass

from .feature_quality import extract_patch, is_edge_ambiguous
from .tracking_types import TrackSample
from .utils import is_finite_point


@dataclass(frozen=True, slots=True)
class LKSettings:
    window_size: int = 21
    pyramid_levels: int = 3
    termination_count: int = 30
    termination_epsilon: float = 0.01
    minimum_eigen_threshold: float = 0.0001
    maximum_lk_error: float = 50.0
    maximum_fb_error: float = 1.0
    maximum_motion: float = 96.0
    edge_margin: int = 16
    enable_forward_backward: bool = True
    enable_appearance_check: bool = True
    appearance_patch_size: int = 15
    minimum_appearance_correlation: float = 0.7
    enable_edge_ambiguity_check: bool = True
    edge_response_patch_size: int = 15
    minimum_corner_ratio: float = 0.10


def _point_inside(x: float, y: float, width: int, height: int, margin: int) -> bool:
    return margin <= x < (width - margin) and margin <= y < (height - margin)


def track_point_sequence(
    frames: list[tuple[int, object]],
    start_point: tuple[float, float],
    settings: LKSettings,
) -> tuple[list[TrackSample], str | None]:
    import cv2
    import numpy as np

    if len(frames) < 1:
        return [], "no_frames"
    samples = [TrackSample(frames[0][0], float(start_point[0]), float(start_point[1]), valid=True)]
    point = np.array([[start_point]], dtype=np.float32)
    termination = None

    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        max(1, int(settings.termination_count)),
        max(0.000001, float(settings.termination_epsilon)),
    )
    lk_args = {
        "winSize": (int(settings.window_size), int(settings.window_size)),
        "maxLevel": max(0, int(settings.pyramid_levels)),
        "criteria": criteria,
        "minEigThreshold": max(0.0, float(settings.minimum_eigen_threshold)),
    }

    for index in range(len(frames) - 1):
        frame_a, gray_a = frames[index]
        frame_b, gray_b = frames[index + 1]
        next_point, status, err = cv2.calcOpticalFlowPyrLK(gray_a, gray_b, point, None, **lk_args)
        lk_error = float(err[0][0]) if err is not None else None
        if status is None or int(status[0][0]) != 1:
            termination = "lk_status_failed"
            break
        x, y = float(next_point[0][0][0]), float(next_point[0][0][1])
        if not is_finite_point(x, y):
            termination = "non_finite"
            break
        height, width = gray_b.shape[:2]
        if not _point_inside(x, y, width, height, int(settings.edge_margin)):
            termination = "outside_or_edge"
            break
        if lk_error is not None and lk_error > settings.maximum_lk_error:
            termination = "lk_error"
            break
        previous_x, previous_y = float(point[0][0][0]), float(point[0][0][1])
        motion = ((x - previous_x) ** 2 + (y - previous_y) ** 2) ** 0.5
        if motion > settings.maximum_motion:
            termination = "maximum_motion"
            break
        if _edge_ambiguous(gray_b, x, y, settings):
            termination = "edge_ambiguity"
            break
        if _appearance_changed(gray_a, gray_b, previous_x, previous_y, x, y, settings):
            termination = "appearance_change"
            break

        fb_error = None
        if settings.enable_forward_backward:
            back_point, back_status, _ = cv2.calcOpticalFlowPyrLK(gray_b, gray_a, next_point, None, **lk_args)
            if back_status is None or int(back_status[0][0]) != 1:
                termination = "fb_status_failed"
                break
            bx, by = float(back_point[0][0][0]), float(back_point[0][0][1])
            fb_error = ((bx - previous_x) ** 2 + (by - previous_y) ** 2) ** 0.5
            if fb_error > settings.maximum_fb_error:
                termination = "fb_error"
                break

        samples.append(TrackSample(frame_b, x, y, lk_error=lk_error, fb_error=fb_error, valid=True))
        point = next_point

    return samples, termination


def track_points_batch(
    frames: list[tuple[int, object]],
    start_points: list[tuple[float, float]],
    settings: LKSettings,
    progress_cb=None,
) -> tuple[list[list[TrackSample]], list[str | None]]:
    """Track many points through an ordered frame sequence with one LK call per frame pair."""
    import cv2
    import numpy as np

    if not frames:
        return [], []
    if not start_points:
        return [], []

    samples = [
        [TrackSample(frames[0][0], float(point[0]), float(point[1]), valid=True)]
        for point in start_points
    ]
    terminations: list[str | None] = [None for _ in start_points]
    active_indices = list(range(len(start_points)))
    points = np.asarray(start_points, dtype=np.float32).reshape((-1, 1, 2))

    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        max(1, int(settings.termination_count)),
        max(0.000001, float(settings.termination_epsilon)),
    )
    lk_args = {
        "winSize": (int(settings.window_size), int(settings.window_size)),
        "maxLevel": max(0, int(settings.pyramid_levels)),
        "criteria": criteria,
        "minEigThreshold": max(0.0, float(settings.minimum_eigen_threshold)),
    }

    for index in range(len(frames) - 1):
        if len(active_indices) == 0:
            break
        _, gray_a = frames[index]
        frame_b, gray_b = frames[index + 1]
        next_points, status, err = cv2.calcOpticalFlowPyrLK(gray_a, gray_b, points, None, **lk_args)
        if status is None or next_points is None:
            for track_index in active_indices:
                terminations[track_index] = terminations[track_index] or "lk_status_failed"
            break

        fb_errors = [None] * len(active_indices)
        if settings.enable_forward_backward:
            back_points, back_status, _ = cv2.calcOpticalFlowPyrLK(gray_b, gray_a, next_points, None, **lk_args)
        else:
            back_points = back_status = None

        height, width = gray_b.shape[:2]
        next_active_indices = []
        next_active_points = []
        flat_status = status.reshape((-1,))
        flat_err = err.reshape((-1,)) if err is not None else [None] * len(active_indices)
        for local_index, track_index in enumerate(active_indices):
            previous_x, previous_y = float(points[local_index, 0, 0]), float(points[local_index, 0, 1])
            x, y = float(next_points[local_index, 0, 0]), float(next_points[local_index, 0, 1])
            lk_error = float(flat_err[local_index]) if flat_err[local_index] is not None else None
            reason = _sample_failure_reason(
                flat_status[local_index],
                x,
                y,
                width,
                height,
                previous_x,
                previous_y,
                lk_error,
                settings,
            )
            fb_error = None
            if reason is None and settings.enable_forward_backward:
                if back_status is None or back_points is None or int(back_status.reshape((-1,))[local_index]) != 1:
                    reason = "fb_status_failed"
                else:
                    bx, by = float(back_points[local_index, 0, 0]), float(back_points[local_index, 0, 1])
                    fb_error = ((bx - previous_x) ** 2 + (by - previous_y) ** 2) ** 0.5
                    fb_errors[local_index] = fb_error
                    if fb_error > settings.maximum_fb_error:
                        reason = "fb_error"
            if reason is None and _appearance_changed(gray_a, gray_b, previous_x, previous_y, x, y, settings):
                reason = "appearance_change"
            if reason is None and _edge_ambiguous(gray_b, x, y, settings):
                reason = "edge_ambiguity"
            if reason is None:
                samples[track_index].append(
                    TrackSample(frame_b, x, y, lk_error=lk_error, fb_error=fb_error, valid=True)
                )
                next_active_indices.append(track_index)
                next_active_points.append((x, y))
            else:
                terminations[track_index] = terminations[track_index] or reason

        active_indices = next_active_indices
        points = np.asarray(next_active_points, dtype=np.float32).reshape((-1, 1, 2))
        if progress_cb:
            progress_cb(index + 1, len(frames) - 1, len(active_indices))

    return samples, terminations


def track_points_step(gray_a, gray_b, points: list[tuple[float, float]], settings: LKSettings):
    """Track many points across one frame pair."""
    import cv2
    import numpy as np

    if not points:
        return []
    point_array = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        max(1, int(settings.termination_count)),
        max(0.000001, float(settings.termination_epsilon)),
    )
    lk_args = {
        "winSize": (int(settings.window_size), int(settings.window_size)),
        "maxLevel": max(0, int(settings.pyramid_levels)),
        "criteria": criteria,
        "minEigThreshold": max(0.0, float(settings.minimum_eigen_threshold)),
    }
    next_points, status, err = cv2.calcOpticalFlowPyrLK(gray_a, gray_b, point_array, None, **lk_args)
    if status is None or next_points is None:
        return [(None, None, None, "lk_status_failed") for _ in points]
    if settings.enable_forward_backward:
        back_points, back_status, _ = cv2.calcOpticalFlowPyrLK(gray_b, gray_a, next_points, None, **lk_args)
    else:
        back_points = back_status = None

    height, width = gray_b.shape[:2]
    flat_status = status.reshape((-1,))
    flat_err = err.reshape((-1,)) if err is not None else [None] * len(points)
    results = []
    for index, (previous_x, previous_y) in enumerate(points):
        x, y = float(next_points[index, 0, 0]), float(next_points[index, 0, 1])
        lk_error = float(flat_err[index]) if flat_err[index] is not None else None
        reason = _sample_failure_reason(
            flat_status[index],
            x,
            y,
            width,
            height,
            previous_x,
            previous_y,
            lk_error,
            settings,
        )
        fb_error = None
        if reason is None and settings.enable_forward_backward:
            if back_status is None or back_points is None or int(back_status.reshape((-1,))[index]) != 1:
                reason = "fb_status_failed"
            else:
                bx, by = float(back_points[index, 0, 0]), float(back_points[index, 0, 1])
                fb_error = ((bx - previous_x) ** 2 + (by - previous_y) ** 2) ** 0.5
                if fb_error > settings.maximum_fb_error:
                    reason = "fb_error"
        if reason is None and _appearance_changed(gray_a, gray_b, previous_x, previous_y, x, y, settings):
            reason = "appearance_change"
        if reason is None and _edge_ambiguous(gray_b, x, y, settings):
            reason = "edge_ambiguity"
        results.append((x, y, lk_error, fb_error, reason))
    return results


def _sample_failure_reason(status, x, y, width, height, previous_x, previous_y, lk_error, settings):
    if int(status) != 1:
        return "lk_status_failed"
    if not is_finite_point(x, y):
        return "non_finite"
    if not _point_inside(x, y, width, height, int(settings.edge_margin)):
        return "outside_or_edge"
    if lk_error is not None and lk_error > settings.maximum_lk_error:
        return "lk_error"
    motion = ((x - previous_x) ** 2 + (y - previous_y) ** 2) ** 0.5
    if motion > settings.maximum_motion:
        return "maximum_motion"
    return None


def _appearance_changed(gray_a, gray_b, previous_x: float, previous_y: float, x: float, y: float, settings: LKSettings) -> bool:
    if not bool(getattr(settings, "enable_appearance_check", True)):
        return False
    threshold = float(getattr(settings, "minimum_appearance_correlation", 0.45))
    if threshold <= -1.0:
        return False
    similarity = _patch_correlation(
        gray_a,
        gray_b,
        previous_x,
        previous_y,
        x,
        y,
        int(getattr(settings, "appearance_patch_size", 15)),
    )
    return similarity is not None and similarity < threshold


def _edge_ambiguous(gray, x: float, y: float, settings: LKSettings) -> bool:
    if not bool(getattr(settings, "enable_edge_ambiguity_check", True)):
        return False
    return is_edge_ambiguous(
        gray,
        x,
        y,
        int(getattr(settings, "edge_response_patch_size", 15)),
        float(getattr(settings, "minimum_corner_ratio", 0.10)),
    )


def _patch_correlation(gray_a, gray_b, ax: float, ay: float, bx: float, by: float, size: int) -> float | None:
    size = max(5, int(size))
    if size % 2 == 0:
        size += 1
    patch_a = _extract_patch(gray_a, ax, ay, size)
    patch_b = _extract_patch(gray_b, bx, by, size)
    if patch_a is None or patch_b is None:
        return None
    a = patch_a.astype("float32", copy=False)
    b = patch_b.astype("float32", copy=False)
    raw_score = _normalized_correlation(a, b)
    gradient_score = _normalized_correlation(_gradient_magnitude(a), _gradient_magnitude(b))
    scores = [score for score in (raw_score, gradient_score) if score is not None]
    if not scores:
        return None
    return max(scores)


def _normalized_correlation(a, b) -> float | None:
    if a.shape != b.shape or a.size == 0:
        return None
    a = a - float(a.mean())
    b = b - float(b.mean())
    energy_a = float((a * a).sum())
    energy_b = float((b * b).sum())
    if energy_a <= 1e-6 and energy_b <= 1e-6:
        return 1.0
    if energy_a <= 1e-6 or energy_b <= 1e-6:
        return 0.0
    denom = float((energy_a * energy_b) ** 0.5)
    if denom <= 1e-6:
        return None
    return float((a * b).sum() / denom)


def _extract_patch(gray, x: float, y: float, size: int):
    return extract_patch(gray, x, y, size)


def _gradient_magnitude(patch):
    if patch.shape[0] < 3 or patch.shape[1] < 3:
        return patch
    dx = patch[1:-1, 2:] - patch[1:-1, :-2]
    dy = patch[2:, 1:-1] - patch[:-2, 1:-1]
    return (dx * dx + dy * dy) ** 0.5
