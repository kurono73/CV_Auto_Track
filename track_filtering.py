from __future__ import annotations

from dataclasses import dataclass

from .dependencies import ensure_numpy_cv2
from .tracking_types import TrackCandidate
from .utils import mad, median


@dataclass(frozen=True, slots=True)
class FilteringSettings:
    minimum_track_length: int = 8
    preferred_track_length: int = 20
    minimum_valid_ratio: float = 0.8
    enable_ransac: bool = True
    ransac_model: str = "FUNDAMENTAL"
    ransac_threshold: float = 2.0
    ransac_confidence: float = 0.99
    ransac_minimum_points: int = 12
    duplicate_distance: float = 6.0
    enable_acceleration_filter: bool = True
    acceleration_multiplier: float = 4.0
    acceleration_minimum: float = 18.0
    acceleration_minimum_ratio: float = 0.30
    enable_local_motion_coherence: bool = True
    local_motion_radius: float = 160.0
    local_motion_multiplier: float = 4.0
    local_motion_minimum_residual: float = 16.0
    local_motion_minimum_tracks: int = 6
    local_motion_minimum_ratio: float = 0.35


def score_track(track: TrackCandidate, settings: FilteringSettings) -> float:
    samples = track.valid_samples
    if not samples:
        return 0.0
    length_score = min(1.0, len(samples) / max(1.0, float(settings.preferred_track_length)))
    fb_values = [s.fb_error for s in samples if s.fb_error is not None]
    lk_values = [s.lk_error for s in samples if s.lk_error is not None]
    fb_score = 1.0 / (1.0 + median(fb_values, 0.0))
    lk_score = 1.0 / (1.0 + median(lk_values, 0.0) / 20.0)
    smoothness = motion_smoothness_score(samples)
    return max(0.0, min(1.0, (0.35 * length_score) + (0.25 * fb_score) + (0.2 * lk_score) + (0.2 * smoothness)))


def motion_smoothness_score(samples) -> float:
    if len(samples) < 4:
        return 0.75
    velocities = []
    for a, b in zip(samples, samples[1:]):
        velocities.append(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5)
    center = median(velocities, 0.0)
    spread = mad(velocities, center, 0.0)
    if center <= 0.0001:
        return 0.7
    return max(0.0, min(1.0, 1.0 - (spread / (center + spread + 1e-6))))


def filter_tracks(tracks: list[TrackCandidate], settings: FilteringSettings) -> list[TrackCandidate]:
    for track in tracks:
        track.quality_score = score_track(track, settings)
        if track.length < settings.minimum_track_length:
            track.disabled = True
            track.termination_reason = track.termination_reason or "short_track"
        elif _valid_ratio(track) < float(settings.minimum_valid_ratio):
            track.disabled = True
            track.termination_reason = track.termination_reason or "low_valid_ratio"
    if bool(settings.enable_acceleration_filter):
        _filter_acceleration_jitter(tracks, settings)
    if bool(settings.enable_local_motion_coherence):
        _filter_local_motion_coherence(tracks, settings)
    remove_duplicates(tracks, settings.duplicate_distance)
    return tracks


def _valid_ratio(track: TrackCandidate) -> float:
    samples = track.valid_samples
    if not samples:
        return 0.0
    start = min(int(sample.frame) for sample in samples)
    end = max(int(sample.frame) for sample in samples)
    span = max(1, end - start + 1)
    return len(samples) / float(span)


def remove_duplicates(tracks: list[TrackCandidate], distance: float) -> None:
    by_frame: dict[int, list[tuple[TrackCandidate, object]]] = {}
    for track in tracks:
        if track.disabled or not track.valid_samples:
            continue
        sample = track.valid_samples[0]
        by_frame.setdefault(sample.frame, []).append((track, sample))
    squared = float(distance) ** 2
    for items in by_frame.values():
        for index, (track_a, sample_a) in enumerate(items):
            if track_a.disabled:
                continue
            for track_b, sample_b in items[index + 1 :]:
                if track_b.disabled:
                    continue
                dist2 = (sample_a.x - sample_b.x) ** 2 + (sample_a.y - sample_b.y) ** 2
                if dist2 <= squared:
                    loser = track_a if track_a.quality_score < track_b.quality_score else track_b
                    loser.disabled = True
                    loser.termination_reason = "duplicate_track"


def _filter_acceleration_jitter(tracks: list[TrackCandidate], settings: FilteringSettings) -> None:
    for track in tracks:
        if track.disabled:
            continue
        samples = track.valid_samples
        if len(samples) < 5:
            continue
        velocities = []
        for a, b in zip(samples, samples[1:]):
            frame_delta = max(1, int(b.frame) - int(a.frame))
            velocities.append(((b.x - a.x) / frame_delta, (b.y - a.y) / frame_delta))
        accelerations = [
            ((vx_b - vx_a) ** 2 + (vy_b - vy_a) ** 2) ** 0.5
            for (vx_a, vy_a), (vx_b, vy_b) in zip(velocities, velocities[1:])
        ]
        if len(accelerations) < 3:
            continue
        center = median(accelerations, 0.0)
        spread = mad(accelerations, center, 0.0)
        threshold = max(float(settings.acceleration_minimum), center + (float(settings.acceleration_multiplier) * spread))
        bad = sum(1 for value in accelerations if value > threshold)
        if bad >= 2 and (bad / float(len(accelerations))) >= float(settings.acceleration_minimum_ratio):
            track.disabled = True
            track.termination_reason = track.termination_reason or "acceleration_jitter"


def _filter_local_motion_coherence(tracks: list[TrackCandidate], settings: FilteringSettings) -> None:
    transitions: dict[tuple[int, int], list[tuple[TrackCandidate, float, float, float, float]]] = {}
    for track in tracks:
        if track.disabled:
            continue
        samples = track.valid_samples
        for a, b in zip(samples, samples[1:]):
            frame_a = int(a.frame)
            frame_b = int(b.frame)
            frame_delta = frame_b - frame_a
            if frame_delta <= 0:
                continue
            transitions.setdefault((frame_a, frame_b), []).append(
                (track, float(a.x), float(a.y), (b.x - a.x) / frame_delta, (b.y - a.y) / frame_delta)
            )

    bad_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    tracks_by_name: dict[str, TrackCandidate] = {}
    radius2 = max(1.0, float(settings.local_motion_radius)) ** 2
    minimum_tracks = max(3, int(settings.local_motion_minimum_tracks))
    minimum_residual = float(settings.local_motion_minimum_residual)
    multiplier = float(settings.local_motion_multiplier)
    for items in transitions.values():
        if len(items) < minimum_tracks:
            continue
        for track, x, y, vx, vy in items:
            key = str(track.id)
            tracks_by_name[key] = track
            total_counts[key] = total_counts.get(key, 0) + 1
            neighbors = [
                (other_vx, other_vy)
                for _other, other_x, other_y, other_vx, other_vy in items
                if (other_x - x) ** 2 + (other_y - y) ** 2 <= radius2
            ]
            if len(neighbors) < minimum_tracks:
                continue
            median_vx = median([item[0] for item in neighbors], 0.0)
            median_vy = median([item[1] for item in neighbors], 0.0)
            residuals = [((item_vx - median_vx) ** 2 + (item_vy - median_vy) ** 2) ** 0.5 for item_vx, item_vy in neighbors]
            center = median(residuals, 0.0)
            spread = mad(residuals, center, 0.0)
            threshold = max(minimum_residual, center + (multiplier * spread))
            residual = ((vx - median_vx) ** 2 + (vy - median_vy) ** 2) ** 0.5
            if residual > threshold:
                bad_counts[key] = bad_counts.get(key, 0) + 1

    minimum_ratio = float(settings.local_motion_minimum_ratio)
    for key, bad in bad_counts.items():
        total = max(1, total_counts.get(key, 0))
        if bad >= 2 and (bad / float(total)) >= minimum_ratio:
            track = tracks_by_name.get(key)
            if track is not None:
                track.disabled = True
                track.termination_reason = track.termination_reason or "local_motion"


def ransac_inlier_rate_for_pair(points_a, points_b, settings: FilteringSettings) -> float:
    if not settings.enable_ransac or len(points_a) < settings.ransac_minimum_points:
        return 1.0
    np, cv2 = ensure_numpy_cv2()
    a = np.asarray(points_a, dtype=np.float32)
    b = np.asarray(points_b, dtype=np.float32)
    mask = None
    if settings.ransac_model in {"FUNDAMENTAL", "AUTO"} and len(points_a) >= 8:
        _, mask = cv2.findFundamentalMat(a, b, cv2.FM_RANSAC, settings.ransac_threshold, settings.ransac_confidence)
    if mask is None and settings.ransac_model in {"HOMOGRAPHY", "AUTO"} and len(points_a) >= 4:
        _, mask = cv2.findHomography(a, b, cv2.RANSAC, settings.ransac_threshold)
    if mask is None:
        return 1.0
    return float(mask.sum()) / float(len(mask))
