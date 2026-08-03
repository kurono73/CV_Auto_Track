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
