from __future__ import annotations

from .compatibility import marker_co_to_pixel, pixel_to_marker_co
from .constants import TRACK_PREFIX
from .tracking_types import TrackCandidate


def target_tracks(clip):
    active_object = getattr(clip.tracking.objects, "active", None)
    if active_object is not None:
        return active_object.tracks
    return clip.tracking.tracks


def is_autotrack_track(track) -> bool:
    return bool(track.name.startswith(TRACK_PREFIX))


def track_name(index: int) -> str:
    return f"{TRACK_PREFIX}{index:04d}"


def next_track_name(used_names: set[str], start_index: int = 1) -> str:
    index = max(1, int(start_index))
    while True:
        name = track_name(index)
        if name not in used_names:
            used_names.add(name)
            return name
        index += 1


def existing_track_points(clip, frame: int, width: int, height: int, include_autotrack: bool = True) -> list[tuple[float, float]]:
    points = []
    for track in target_tracks(clip):
        if not include_autotrack and is_autotrack_track(track):
            continue
        marker = track.markers.find_frame(int(frame), exact=False)
        if marker is None or marker.mute:
            continue
        points.append(marker_co_to_pixel(tuple(marker.co), width, height))
    return points


def bake_candidates(
    clip,
    candidates: list[TrackCandidate],
    width: int,
    height: int,
    replace_autotrack: bool = False,
    bake_disabled: bool = True,
    pattern_size: int = 15,
    search_size: int = 30,
) -> tuple[int, int]:
    if replace_autotrack:
        raise RuntimeError("Existing CV Auto Track tracks must be deleted before baking replacement tracks.")
    tracks = target_tracks(clip)
    used_names = {str(track.name) for track in tracks}
    created = 0
    disabled = 0
    for candidate in candidates:
        if candidate.disabled and not bake_disabled:
            continue
        samples = candidate.valid_samples
        if not samples:
            continue
        created += 1
        if candidate.disabled:
            disabled += 1
        name = next_track_name(used_names)
        first = samples[0]
        track = tracks.new(name=name, frame=int(first.frame))
        track.use_custom_color = True
        track.color = (0.1, 0.65, 1.0)
        track.select = False
        track.select_anchor = False
        track.select_pattern = False
        track.select_search = False
        template_marker = track.markers.find_frame(int(first.frame), exact=True)
        marker_areas = _marker_area_template(template_marker, width, height, pattern_size, search_size)
        _bake_disabled_ranges(track, samples, candidate.disabled, width, height, int(getattr(clip, "frame_duration", 1)), marker_areas)
        for sample in samples:
            marker = track.markers.find_frame(int(sample.frame), exact=True)
            if marker is None:
                marker = track.markers.insert_frame(int(sample.frame))
            marker.co = pixel_to_marker_co(sample.x, sample.y, width, height)
            _apply_marker_areas(marker, marker_areas)
            marker.mute = bool(candidate.disabled or not sample.valid)
    return created, disabled


def _marker_area_template(marker, width: int, height: int, pattern_size: int | None = None, search_size: int | None = None) -> dict[str, tuple] | None:
    sized = _marker_area_from_sizes(width, height, pattern_size, search_size)
    if sized is not None:
        return sized
    if marker is None:
        return None
    return {
        "pattern_corners": tuple((float(corner[0]), float(corner[1])) for corner in marker.pattern_corners),
        "search_min": (float(marker.search_min[0]), float(marker.search_min[1])),
        "search_max": (float(marker.search_max[0]), float(marker.search_max[1])),
    }


def _marker_area_from_sizes(width: int, height: int, pattern_size: int | None, search_size: int | None) -> dict[str, tuple] | None:
    if not width or not height or pattern_size is None or search_size is None:
        return None
    pattern = max(1.0, float(pattern_size))
    search = max(pattern + 1.0, float(search_size))
    pattern_x = (pattern * 0.5) / float(width)
    pattern_y = (pattern * 0.5) / float(height)
    search_x = (search * 0.5) / float(width)
    search_y = (search * 0.5) / float(height)
    return {
        "pattern_corners": ((-pattern_x, -pattern_y), (pattern_x, -pattern_y), (pattern_x, pattern_y), (-pattern_x, pattern_y)),
        "search_min": (-search_x, -search_y),
        "search_max": (search_x, search_y),
    }


def _apply_marker_areas(marker, marker_areas: dict[str, tuple] | None) -> None:
    if marker_areas is None:
        return
    for index, corner in enumerate(marker_areas["pattern_corners"]):
        marker.pattern_corners[index] = corner
    marker.search_min = marker_areas["search_min"]
    marker.search_max = marker_areas["search_max"]


def _bake_disabled_ranges(track, samples, candidate_disabled: bool, width: int, height: int, clip_duration: int, marker_areas: dict[str, tuple] | None) -> None:
    if candidate_disabled or not samples:
        return
    ordered = sorted(samples, key=lambda item: int(item.frame))
    first = ordered[0]
    last = ordered[-1]
    clip_start = 1
    clip_end = max(clip_start, int(clip_duration))
    if int(first.frame) > clip_start:
        _insert_muted_span(track, clip_start, int(first.frame) - 1, (first.x, first.y), (first.x, first.y), width, height, marker_areas)
    previous = first
    for sample in ordered[1:]:
        if int(sample.frame) > int(previous.frame) + 1:
            _insert_muted_span(
                track,
                int(previous.frame) + 1,
                int(sample.frame) - 1,
                (previous.x, previous.y),
                (sample.x, sample.y),
                width,
                height,
                marker_areas,
            )
        previous = sample
    if int(last.frame) < clip_end:
        _insert_muted_span(track, int(last.frame) + 1, clip_end, (last.x, last.y), (last.x, last.y), width, height, marker_areas)


def _insert_muted_span(
    track,
    start_frame: int,
    end_frame: int,
    start_position: tuple[float, float],
    end_position: tuple[float, float],
    width: int,
    height: int,
    marker_areas: dict[str, tuple] | None,
) -> None:
    if int(end_frame) < int(start_frame):
        return
    _insert_muted_marker(track, int(start_frame), start_position[0], start_position[1], width, height, marker_areas)
    if int(end_frame) != int(start_frame):
        _insert_muted_marker(track, int(end_frame), end_position[0], end_position[1], width, height, marker_areas)


def _insert_muted_marker(track, frame: int, x: float, y: float, width: int, height: int, marker_areas: dict[str, tuple] | None) -> None:
    marker = track.markers.find_frame(int(frame), exact=True)
    if marker is None:
        marker = track.markers.insert_frame(int(frame))
    marker.co = pixel_to_marker_co(x, y, width, height)
    _apply_marker_areas(marker, marker_areas)
    marker.mute = True


def disable_tracks(tracks) -> int:
    count = 0
    for track in tracks:
        for marker in track.markers:
            marker.mute = True
        count += 1
    return count


def select_autotrack_tracks_for_deletion(clip) -> int:
    count = 0
    for track in target_tracks(clip):
        should_delete = is_autotrack_track(track)
        track.select = should_delete
        track.select_anchor = should_delete
        track.select_pattern = should_delete
        track.select_search = should_delete
        if should_delete:
            count += 1
    return count
