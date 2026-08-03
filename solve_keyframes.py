from __future__ import annotations

import math
import statistics

from .blender_tracks import target_tracks
from .tracking_types import TrackCandidate


def choose_keyframes_from_candidates(candidates: list[TrackCandidate], start_frame: int, end_frame: int) -> tuple[int, int] | None:
    sequences: list[dict[int, tuple[float, float]]] = []
    for candidate in candidates:
        if candidate.disabled:
            continue
        positions = {
            int(sample.frame): (float(sample.x), float(sample.y))
            for sample in candidate.valid_samples
            if int(start_frame) <= int(sample.frame) <= int(end_frame)
        }
        if len(positions) >= 2:
            sequences.append(positions)
    return _choose_pair_from_sequences(sequences, start_frame, end_frame)


def choose_keyframes_from_clip(clip, start_frame: int, end_frame: int) -> tuple[int, int] | None:
    sequences: list[dict[int, tuple[float, float]]] = []
    for track in target_tracks(clip):
        positions = {
            int(marker.frame): (float(marker.co[0]), float(marker.co[1]))
            for marker in track.markers
            if not marker.mute and int(start_frame) <= int(marker.frame) <= int(end_frame)
        }
        if len(positions) >= 2:
            sequences.append(positions)
    return _choose_pair_from_sequences(sequences, start_frame, end_frame)


def apply_keyframes(clip, keyframes: tuple[int, int] | None) -> bool:
    if keyframes is None:
        disable_keyframe_selection(clip)
        return False
    keyframe_a, keyframe_b = sorted((int(keyframes[0]), int(keyframes[1])))
    active_object = getattr(clip.tracking.objects, "active", None)
    if active_object is None:
        return False
    active_object.keyframe_a = keyframe_a
    active_object.keyframe_b = keyframe_b
    disable_keyframe_selection(clip)
    return True


def disable_keyframe_selection(clip) -> None:
    try:
        clip.tracking.settings.use_keyframe_selection = False
    except Exception:
        pass


def _choose_pair_from_runs(
    runs: list[tuple[int, int]],
    start_frame: int,
    end_frame: int,
    minimum_tracks: int = 8,
) -> tuple[int, int] | None:
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    if end_frame <= start_frame:
        return None
    clamped_runs = []
    for run_start, run_end in runs:
        run_start = max(start_frame, int(run_start))
        run_end = min(end_frame, int(run_end))
        if run_end <= run_start:
            continue
        clamped_runs.append((run_start, run_end))
    if len(clamped_runs) < minimum_tracks:
        return None

    best: tuple[int, int, int] | None = None
    start_candidates = sorted({start_frame} | {run_start for run_start, _run_end in clamped_runs})
    for frame_a in start_candidates:
        covering_ends = sorted(
            (run_end for run_start, run_end in clamped_runs if run_start <= frame_a < run_end),
            reverse=True,
        )
        if len(covering_ends) < minimum_tracks:
            continue
        frame_b = covering_ends[minimum_tracks - 1]
        if frame_b <= frame_a:
            continue
        coverage = sum(1 for run_end in covering_ends if run_end >= frame_b)
        candidate = (frame_a, frame_b, coverage)
        if best is None or _segment_score(candidate) > _segment_score(best):
            best = candidate
    if best is None:
        return None
    return _inner_pair(best[0], best[1], start_frame, end_frame)


def _choose_pair_from_sequences(
    sequences: list[dict[int, tuple[float, float]]],
    start_frame: int,
    end_frame: int,
    minimum_tracks: int = 8,
) -> tuple[int, int] | None:
    start_frame = int(start_frame)
    end_frame = int(end_frame)
    total_span = int(end_frame) - int(start_frame)
    if total_span <= 1 or len(sequences) < int(minimum_tracks):
        return None
    candidate_frames = _candidate_keyframes(sequences, start_frame, end_frame)
    bounds = _coordinate_bounds(sequences, start_frame, end_frame)
    best_pair: tuple[int, int] | None = None
    best_score: float | None = None
    for index, frame_a in enumerate(candidate_frames[:-1]):
        for frame_b in candidate_frames[index + 1 :]:
            score = _score_keyframe_pair(sequences, frame_a, frame_b, start_frame, end_frame, bounds, minimum_tracks)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_pair = (int(frame_a), int(frame_b))
    if best_pair is not None:
        return best_pair
    runs = []
    for positions in sequences:
        runs.extend(_contiguous_runs(positions, start_frame, end_frame))
    return _choose_pair_from_runs(runs, start_frame, end_frame, minimum_tracks=minimum_tracks)


def _candidate_keyframes(
    sequences: list[dict[int, tuple[float, float]]],
    start_frame: int,
    end_frame: int,
    maximum_samples: int = 56,
) -> list[int]:
    frames = {int(start_frame), int(end_frame)}
    total_span = max(1, int(end_frame) - int(start_frame))
    stride = max(1, int(math.ceil(total_span / max(1, int(maximum_samples)))))
    frames.update(range(int(start_frame), int(end_frame) + 1, stride))
    for positions in sequences:
        keys = sorted(frame for frame in positions if int(start_frame) <= int(frame) <= int(end_frame))
        if not keys:
            continue
        frames.add(keys[0])
        frames.add(keys[-1])
    return sorted(frame for frame in frames if int(start_frame) <= int(frame) <= int(end_frame))


def _score_keyframe_pair(
    sequences: list[dict[int, tuple[float, float]]],
    frame_a: int,
    frame_b: int,
    start_frame: int,
    end_frame: int,
    bounds: tuple[float, float, float, float],
    minimum_tracks: int,
) -> float | None:
    span = int(frame_b) - int(frame_a)
    total_span = max(1, int(end_frame) - int(start_frame))
    if span <= 1:
        return None
    if total_span >= 20 and span < max(4, int(round(total_span * 0.05))):
        return None
    if span > max(2, int(round(total_span * 0.55))):
        return None
    common: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for positions in sequences:
        point_a = positions.get(int(frame_a))
        point_b = positions.get(int(frame_b))
        if point_a is not None and point_b is not None:
            common.append((point_a, point_b))
    if len(common) < int(minimum_tracks):
        return None

    diag = _bounds_diagonal(bounds)
    displacements = [math.hypot(point_b[0] - point_a[0], point_b[1] - point_a[1]) / diag for point_a, point_b in common]
    median_motion = min(1.0, float(statistics.median(displacements)))
    median_dx = statistics.median(point_b[0] - point_a[0] for point_a, point_b in common)
    median_dy = statistics.median(point_b[1] - point_a[1] for point_a, point_b in common)
    motion_spread = min(
        1.0,
        float(
            statistics.median(
                math.hypot((point_b[0] - point_a[0]) - median_dx, (point_b[1] - point_a[1]) - median_dy)
                for point_a, point_b in common
            )
        )
        / diag,
    )
    coverage_score = min(1.0, len(common) / 80.0)
    distribution_score = _distribution_score([point_a for point_a, _point_b in common], bounds)
    span_ratio = span / float(total_span)
    span_score = max(0.0, 1.0 - (abs(span_ratio - 0.28) / 0.28))
    return (
        (median_motion * 3.0)
        + (motion_spread * 2.0)
        + (distribution_score * 1.25)
        + coverage_score
        + (span_score * 0.75)
    )


def _coordinate_bounds(
    sequences: list[dict[int, tuple[float, float]]],
    start_frame: int,
    end_frame: int,
) -> tuple[float, float, float, float]:
    points = [
        point
        for positions in sequences
        for frame, point in positions.items()
        if int(start_frame) <= int(frame) <= int(end_frame)
    ]
    if not points:
        return (0.0, 0.0, 1.0, 1.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _bounds_diagonal(bounds: tuple[float, float, float, float]) -> float:
    min_x, min_y, max_x, max_y = bounds
    return max(1.0e-6, math.hypot(float(max_x) - float(min_x), float(max_y) - float(min_y)))


def _distribution_score(points: list[tuple[float, float]], bounds: tuple[float, float, float, float]) -> float:
    if not points:
        return 0.0
    min_x, min_y, max_x, max_y = bounds
    width = max(1.0e-6, float(max_x) - float(min_x))
    height = max(1.0e-6, float(max_y) - float(min_y))
    occupied = set()
    for x, y in points:
        cell_x = min(3, max(0, int(((float(x) - min_x) / width) * 4.0)))
        cell_y = min(2, max(0, int(((float(y) - min_y) / height) * 3.0)))
        occupied.add((cell_x, cell_y))
    return min(1.0, len(occupied) / 12.0)


def _contiguous_runs(frames, start_frame: int, end_frame: int) -> list[tuple[int, int]]:
    sorted_frames = sorted({int(frame) for frame in frames if int(start_frame) <= int(frame) <= int(end_frame)})
    if len(sorted_frames) < 2:
        return []
    runs = []
    run_start = sorted_frames[0]
    previous = run_start
    for frame in sorted_frames[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        if previous > run_start:
            runs.append((run_start, previous))
        run_start = previous = frame
    if previous > run_start:
        runs.append((run_start, previous))
    return runs


def _segment_score(segment: tuple[int, int, int]) -> tuple[int, int, int]:
    start, end, count = segment
    return (end - start, count, -start)


def _inner_pair(segment_start: int, segment_end: int, start_frame: int, end_frame: int) -> tuple[int, int] | None:
    segment_span = int(segment_end) - int(segment_start)
    total_span = int(end_frame) - int(start_frame)
    if segment_span <= 1:
        return None
    target_span = max(2, min(int(total_span * 0.45), int(segment_span * 0.65)))
    if target_span >= segment_span:
        return (int(segment_start), int(segment_end))
    center = (int(segment_start) + int(segment_end)) // 2
    frame_a = center - (target_span // 2)
    frame_b = frame_a + target_span
    if frame_a < segment_start:
        frame_a = int(segment_start)
        frame_b = frame_a + target_span
    if frame_b > segment_end:
        frame_b = int(segment_end)
        frame_a = frame_b - target_span
    return (int(frame_a), int(frame_b))
