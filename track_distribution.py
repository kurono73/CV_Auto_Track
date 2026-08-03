from __future__ import annotations

from dataclasses import dataclass

from .tracking_types import TrackCandidate


@dataclass(frozen=True, slots=True)
class DistributionSettings:
    grid_columns: int = 8
    grid_rows: int = 5
    maximum_tracks_per_cell: int = 20
    minimum_tracks_per_cell: int = 3
    distribution_strength: float = 1.0


def cell_for_point(x: float, y: float, width: int, height: int, settings: DistributionSettings) -> tuple[int, int]:
    cols = max(1, int(settings.grid_columns))
    rows = max(1, int(settings.grid_rows))
    return (
        min(cols - 1, max(0, int((x / max(1, width)) * cols))),
        min(rows - 1, max(0, int((y / max(1, height)) * rows))),
    )


def enforce_distribution(
    tracks: list[TrackCandidate],
    width: int,
    height: int,
    settings: DistributionSettings,
    temporal_bucket_size: int = 0,
) -> None:
    buckets: dict[tuple, list[TrackCandidate]] = {}
    for track in tracks:
        if track.disabled or not track.valid_samples:
            continue
        sample = track.valid_samples[0]
        key = cell_for_point(sample.x, sample.y, width, height, settings)
        if temporal_bucket_size > 0:
            key = (key[0], key[1], int(track.detection_frame) // int(temporal_bucket_size))
        buckets.setdefault(key, []).append(track)
    max_per_cell = max(1, int(settings.maximum_tracks_per_cell))
    for bucket_tracks in buckets.values():
        if len(bucket_tracks) <= max_per_cell:
            continue
        bucket_tracks.sort(key=lambda item: (item.quality_score, item.length), reverse=True)
        for loser in bucket_tracks[max_per_cell:]:
            loser.disabled = True
            loser.termination_reason = "distribution_prune"


def limit_enabled_tracks(
    tracks: list[TrackCandidate],
    width: int,
    height: int,
    settings: DistributionSettings,
    maximum_tracks: int,
    temporal_bucket_size: int = 0,
) -> int:
    enabled = [track for track in tracks if not track.disabled and track.valid_samples]
    limit = max(1, int(maximum_tracks))
    if len(enabled) <= limit:
        return 0

    buckets: dict[tuple, list[TrackCandidate]] = {}
    for track in enabled:
        sample = _representative_sample(track)
        key = cell_for_point(sample.x, sample.y, width, height, settings)
        if temporal_bucket_size > 0:
            key = (int(track.detection_frame) // int(temporal_bucket_size), key[0], key[1])
        buckets.setdefault(key, []).append(track)

    for bucket_tracks in buckets.values():
        bucket_tracks.sort(key=_track_rank, reverse=True)

    bucket_keys = _spread_bucket_keys(buckets, settings)
    selected: set[int] = set()
    while len(selected) < limit:
        advanced = False
        for key in bucket_keys:
            bucket_tracks = buckets[key]
            while bucket_tracks and id(bucket_tracks[0]) in selected:
                bucket_tracks.pop(0)
            if not bucket_tracks:
                continue
            selected.add(id(bucket_tracks.pop(0)))
            advanced = True
            if len(selected) >= limit:
                break
        if not advanced:
            break

    pruned = 0
    for track in enabled:
        if id(track) in selected:
            continue
        track.disabled = True
        track.termination_reason = "bake_budget_prune"
        pruned += 1
    return pruned


def _representative_sample(track: TrackCandidate):
    samples = track.valid_samples
    if not samples:
        return track.samples[0]
    target_frame = int(track.detection_frame)
    return min(samples, key=lambda sample: abs(int(sample.frame) - target_frame))


def _track_rank(track: TrackCandidate) -> tuple[float, int, int]:
    return (float(track.quality_score), int(track.length), -int(track.id))


def _spread_bucket_keys(buckets: dict[tuple, list[TrackCandidate]], settings: DistributionSettings) -> list[tuple]:
    remaining = set(buckets)
    if not remaining:
        return []
    first = max(remaining, key=lambda key: _bucket_quality(buckets, key))
    ordered = [first]
    remaining.remove(first)
    while remaining:
        next_key = max(
            remaining,
            key=lambda key: (
                min(_bucket_distance(key, chosen, settings) for chosen in ordered),
                _bucket_quality(buckets, key),
                key,
            ),
        )
        ordered.append(next_key)
        remaining.remove(next_key)
    return ordered


def _bucket_quality(buckets: dict[tuple, list[TrackCandidate]], key: tuple) -> float:
    tracks = buckets.get(key) or []
    return float(tracks[0].quality_score) if tracks else 0.0


def _bucket_distance(key_a: tuple, key_b: tuple, settings: DistributionSettings) -> float:
    time_a, col_a, row_a = _bucket_components(key_a)
    time_b, col_b, row_b = _bucket_components(key_b)
    cols = max(1, int(settings.grid_columns) - 1)
    rows = max(1, int(settings.grid_rows) - 1)
    dx = (float(col_a) - float(col_b)) / float(cols)
    dy = (float(row_a) - float(row_b)) / float(rows)
    dt = 0.0 if time_a is None or time_b is None else min(1.0, abs(float(time_a) - float(time_b)) / 4.0)
    return (dx * dx) + (dy * dy) + (dt * dt * 0.35)


def _bucket_components(key: tuple) -> tuple[int | None, int, int]:
    if len(key) >= 3:
        return int(key[0]), int(key[1]), int(key[2])
    return None, int(key[0]), int(key[1])


def active_points_at_frame(tracks: list[TrackCandidate], frame: int) -> list[tuple[float, float]]:
    points = []
    for track in tracks:
        if track.disabled:
            continue
        for sample in track.valid_samples:
            if sample.frame == frame:
                points.append((sample.x, sample.y))
                break
    return points


def underfilled_cells(tracks: list[TrackCandidate], frame: int, width: int, height: int, settings: DistributionSettings):
    counts = {(col, row): 0 for row in range(settings.grid_rows) for col in range(settings.grid_columns)}
    for x, y in active_points_at_frame(tracks, frame):
        cell = cell_for_point(x, y, width, height, settings)
        counts[cell] = counts.get(cell, 0) + 1
    return [cell for cell, count in counts.items() if count < settings.minimum_tracks_per_cell]
