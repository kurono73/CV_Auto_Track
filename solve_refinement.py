from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .blender_tracks import disable_tracks, is_autotrack_track, target_tracks
from .compatibility import marker_co_to_pixel
from .constants import PROTECTED_PREFIX
from .constants import REFERENCE_ANALYSIS_WIDTH
from .track_filtering import FilteringSettings, filter_multibaseline_geometry
from .tracking_types import TrackCandidate, TrackSample
from .utils import mad


@dataclass(slots=True)
class RefineResult:
    solve_error_before: float = -1.0
    solve_error_after: float = -1.0
    iterations: int = 0
    disabled_tracks: int = 0
    disabled_track_names: list[str] = field(default_factory=list)
    message: str = ""


def get_solve_error(clip) -> float:
    reconstruction = clip.tracking.reconstruction
    value = getattr(reconstruction, "average_error", -1.0)
    try:
        return float(value)
    except Exception:
        return -1.0


def solve_camera(context, clip, reset_radial: bool = False) -> tuple[bool, str]:
    import bpy

    if reset_radial:
        _reset_radial_distortion_if_refining(clip)
    area = next((area for area in context.window.screen.areas if area.type == "CLIP_EDITOR"), None)
    if area is None:
        return False, "Movie Clip Editor area is required to run Blender's camera solver."
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    active_space = getattr(area.spaces, "active", None)
    space = active_space if active_space and active_space.type == "CLIP_EDITOR" else None
    if space is None:
        space = next((space for space in area.spaces if space.type == "CLIP_EDITOR"), None)
    if region is None or space is None:
        return False, "Movie Clip Editor context is incomplete."
    previous_clip = space.clip
    space.clip = clip
    if getattr(space, "mode", "TRACKING") != "TRACKING":
        space.mode = "TRACKING"
    override = {
        "window": context.window,
        "screen": context.window.screen,
        "area": area,
        "region": region,
        "space_data": space,
        "edit_movieclip": clip,
    }
    try:
        with context.temp_override(**override):
            result = bpy.ops.clip.solve_camera()
    except Exception as exc:
        return False, f"Camera solve failed: {type(exc).__name__}: {exc}"
    finally:
        space.clip = previous_clip
    if "FINISHED" not in result:
        return False, f"Camera solve returned {result}."
    return True, "Camera solve finished."


def _reset_radial_distortion_if_refining(clip) -> None:
    try:
        if not bool(clip.tracking.settings.refine_intrinsics_radial_distortion):
            return
    except Exception:
        return
    camera = clip.tracking.camera
    for attr in (
        "k1",
        "k2",
        "k3",
        "division_k1",
        "division_k2",
        "nuke_k1",
        "nuke_k2",
        "brown_k1",
        "brown_k2",
        "brown_k3",
        "brown_k4",
    ):
        if hasattr(camera, attr):
            try:
                setattr(camera, attr, 0.0)
            except Exception:
                pass


def collect_error_tracks(clip, props):
    tracks = []
    for track in target_tracks(clip):
        if not _can_refine_track(track, props):
            continue
        error = _track_error(track)
        if error is None:
            continue
        tracks.append((track, error))
    return tracks


def choose_outliers(clip, props):
    outliers, _forced_filter_names = _choose_outliers_with_forced_names(clip, props)
    return outliers


def _choose_outliers_with_forced_names(clip, props):
    error_tracks = collect_error_tracks(clip, props)
    if not error_tracks:
        return [], set()
    hard_threshold, soft_threshold = _solve_error_thresholds(error_tracks, props)
    motion_candidates = _motion_outliers(clip, props)
    geometry_candidates = _geometry_outliers(clip, props)
    motion_names = {track.name for track, _score in motion_candidates}
    geometry_names = {track.name for track, _score in geometry_candidates}
    forced_filter_names = set()
    candidates = []
    for track, error in error_tracks:
        motion = track.name in motion_names
        geometry = track.name in geometry_names
        soft_error = float(error) > soft_threshold
        hard_error = float(error) > hard_threshold
        # Motion alone is ambiguous in scenes with parallax. Prioritize tracks
        # where independent solve and motion/geometry evidence agree.
        if soft_error and motion and geometry:
            priority = 5.0e6
            forced_filter_names.add(track.name)
        elif soft_error and motion:
            priority = 4.0e6
            forced_filter_names.add(track.name)
        elif soft_error and geometry:
            priority = 3.0e6
            forced_filter_names.add(track.name)
        elif hard_error:
            priority = 2.0e6
        else:
            continue
        candidates.append((track, priority + float(error)))
    candidates.sort(key=lambda item: item[1], reverse=True)

    enabled_count = _enabled_track_count(clip)
    remaining_budget = max(0, enabled_count - int(props.minimum_remaining_tracks))
    per_iteration = max(1, int(props.maximum_disabled_per_iteration))
    percentage_limit = max(1, int(enabled_count * (float(props.outlier_percentage_per_iteration) / 100.0)))
    limit = min(remaining_budget, per_iteration, percentage_limit)
    return [track for track, _ in candidates[:limit]], forced_filter_names


def _solve_error_outliers(clip, props):
    error_tracks = collect_error_tracks(clip, props)
    if not error_tracks:
        return []
    hard_threshold, _soft_threshold = _solve_error_thresholds(error_tracks, props)
    return [
        (track, error)
        for track, error in error_tracks
        if error > hard_threshold
    ]


def _solve_error_thresholds(error_tracks, props) -> tuple[float, float]:
    errors = [float(error) for _track, error in error_tracks]
    center = statistics.median(errors)
    spread = mad(errors, center, 0.0)
    absolute_threshold = max(0.001, float(props.maximum_track_error))
    multiplier = max(0.0, float(props.mad_multiplier))
    # A zero MAD is common when Blender reports many similar bundle errors.
    # Keep a small absolute margin so any tiny deviation is not treated as an
    # outlier merely because the robust spread collapsed to zero.
    minimum_margin = max(0.1, absolute_threshold * 0.125)
    relative_threshold = center + max(minimum_margin, multiplier * spread)
    hard_threshold = min(absolute_threshold, relative_threshold)
    soft_relative = center + max(minimum_margin * 0.5, multiplier * spread * 0.5)
    soft_threshold = min(absolute_threshold * 0.75, soft_relative)
    return hard_threshold, soft_threshold


def _motion_outliers(clip, props):
    if not bool(getattr(props, "refine_motion_outliers", True)):
        return []
    width, height = _clip_dimensions(clip)
    if width <= 0 or height <= 0:
        return []
    transitions: dict[tuple[int, int], list[tuple[object, float, float, float, float]]] = {}
    for track in target_tracks(clip):
        if not _can_refine_track(track, props):
            continue
        for frame_a, frame_b, x, y, vx, vy in _track_motion_steps(track, width, height):
            transitions.setdefault((frame_a, frame_b), []).append((track, x, y, vx, vy))

    bad_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    scores: dict[str, float] = {}
    tracks_by_name = {}
    minimum_tracks = max(8, int(getattr(props, "minimum_remaining_tracks", 20) * 0.25))
    multiplier = float(getattr(props, "motion_outlier_multiplier", 4.0))
    pixel_scale = 1.0
    if bool(getattr(props, "auto_scale_pixel_parameters", True)):
        pixel_scale = max(0.05, float(width) / float(REFERENCE_ANALYSIS_WIDTH))
    minimum_residual = float(getattr(props, "motion_outlier_min_residual", 20.0)) * pixel_scale
    local_radius = float(getattr(props, "motion_outlier_local_radius", 180.0)) * pixel_scale
    local_minimum = max(3, int(getattr(props, "motion_outlier_local_min_tracks", 6)))
    for items in transitions.values():
        if len(items) < minimum_tracks:
            continue
        median_vx = statistics.median(vx for _, _x, _y, vx, _vy in items)
        median_vy = statistics.median(vy for _, _x, _y, _vx, vy in items)
        residuals = [((vx - median_vx) ** 2 + (vy - median_vy) ** 2) ** 0.5 for _, _x, _y, vx, vy in items]
        center = statistics.median(residuals)
        spread = mad(residuals, center, 0.0)
        threshold = max(minimum_residual, center + (multiplier * spread))
        local_residuals, local_thresholds = _local_motion_residuals(items, local_radius, local_minimum, minimum_residual, multiplier)
        for (track, _x, _y, _vx, _vy), residual, local_residual, local_threshold in zip(items, residuals, local_residuals, local_thresholds):
            tracks_by_name[track.name] = track
            total_counts[track.name] = total_counts.get(track.name, 0) + 1
            effective_residual = max(residual, local_residual)
            local_available = local_threshold < float("inf")
            if local_available:
                motion_outlier = residual > threshold and local_residual > local_threshold
            else:
                motion_outlier = residual > threshold
            if not motion_outlier:
                continue
            bad_counts[track.name] = bad_counts.get(track.name, 0) + 1
            scores[track.name] = max(scores.get(track.name, 0.0), effective_residual)

    minimum_ratio = float(getattr(props, "motion_outlier_min_ratio", 0.35))
    result = []
    for name, bad_count in bad_counts.items():
        total = max(1, total_counts.get(name, 0))
        if bad_count < 2 or (bad_count / float(total)) < minimum_ratio:
            continue
        result.append((tracks_by_name[name], scores.get(name, 0.0)))
    return result


def _local_motion_residuals(items, radius: float, minimum_tracks: int, minimum_residual: float, multiplier: float):
    radius2 = max(1.0, float(radius)) ** 2
    residuals = []
    thresholds = []
    for _track, x, y, vx, vy in items:
        neighbors = [
            (other_vx, other_vy)
            for _other, other_x, other_y, other_vx, other_vy in items
            if (other_x - x) ** 2 + (other_y - y) ** 2 <= radius2
        ]
        if len(neighbors) < minimum_tracks:
            residuals.append(0.0)
            thresholds.append(float("inf"))
            continue
        median_vx = statistics.median(item[0] for item in neighbors)
        median_vy = statistics.median(item[1] for item in neighbors)
        neighbor_residuals = [((item_vx - median_vx) ** 2 + (item_vy - median_vy) ** 2) ** 0.5 for item_vx, item_vy in neighbors]
        center = statistics.median(neighbor_residuals)
        spread = mad(neighbor_residuals, center, 0.0)
        residuals.append(((vx - median_vx) ** 2 + (vy - median_vy) ** 2) ** 0.5)
        thresholds.append(max(float(minimum_residual), center + (float(multiplier) * spread)))
    return residuals, thresholds


def _track_motion_steps(track, width: int, height: int):
    markers = [marker for marker in track.markers if not marker.mute]
    markers.sort(key=lambda marker: int(marker.frame))
    for marker_a, marker_b in zip(markers, markers[1:]):
        frame_a = int(marker_a.frame)
        frame_b = int(marker_b.frame)
        frame_delta = frame_b - frame_a
        if frame_delta != 1:
            continue
        ax, ay = marker_co_to_pixel(tuple(marker_a.co), width, height)
        bx, by = marker_co_to_pixel(tuple(marker_b.co), width, height)
        yield frame_a, frame_b, ax, ay, (bx - ax) / frame_delta, (by - ay) / frame_delta


def _clip_dimensions(clip) -> tuple[int, int]:
    try:
        size = clip.size
        return int(size[0]), int(size[1])
    except Exception:
        return 0, 0


def refine_solve(context, clip, props, cancel_cb=None, progress_cb=None, max_iterations: int | None = None) -> RefineResult:
    result = RefineResult(solve_error_before=get_solve_error(clip))
    _reset_radial_distortion_if_refining(clip)
    best_error = result.solve_error_before if result.solve_error_before >= 0 else float("inf")
    best_state = _snapshot_mutes(clip)
    best_state_is_current = True
    previous_error = best_error
    iteration_limit = max(1, int(max_iterations if max_iterations is not None else props.maximum_refine_iterations))
    progress_index = 0
    progress_total = (iteration_limit * 2) + 1

    def update_progress(message: str) -> None:
        nonlocal progress_index
        progress_index += 1
        if progress_cb:
            progress_cb(progress_index, progress_total, message)

    for iteration in range(iteration_limit):
        if cancel_cb and cancel_cb():
            result.message = "Cancelled"
            break
        update_progress(f"Solving {iteration + 1}/{iteration_limit}")
        ok, message = solve_camera(context, clip)
        if not ok:
            result.message = message
            break
        current_error = get_solve_error(clip)
        if iteration == 0 and result.solve_error_before < 0:
            result.solve_error_before = current_error
        if current_error >= 0 and current_error < best_error:
            best_error = current_error
            best_state = _snapshot_mutes(clip)
            best_state_is_current = True
        outliers, forced_filter_names = _choose_outliers_with_forced_names(clip, props)
        if not outliers:
            if 0 <= current_error <= float(props.target_solve_error):
                result.message = "Target solve error reached."
                break
            result.message = "No solve outliers to disable."
            break
        state_before_disable = _snapshot_mutes(clip)
        outlier_names = [track.name for track in outliers]
        disabled = disable_tracks(outliers)
        best_state_is_current = False
        result.disabled_tracks += disabled
        result.disabled_track_names.extend(outlier_names[:disabled])
        result.iterations += 1
        update_progress(f"Refining {iteration + 1}/{iteration_limit}")
        ok, message = solve_camera(context, clip)
        if not ok:
            result.message = message
            break
        next_error = get_solve_error(clip)
        if next_error >= 0 and previous_error < float("inf"):
            improvement = previous_error - next_error
            if next_error > previous_error:
                if bool(getattr(props, "delete_refined_tracks", True)):
                    best_state = _snapshot_mutes(clip)
                    best_state_is_current = True
                    best_error = next_error
                    result.message = "Solve error worsened; kept rejected tracks for deletion."
                    break
                _restore_mutes(clip, state_before_disable)
                forced_outliers = [track for track in outliers if track.name in forced_filter_names]
                forced_disabled = disable_tracks(forced_outliers)
                if forced_disabled:
                    solve_camera(context, clip)
                    best_state = _snapshot_mutes(clip)
                    best_state_is_current = True
                    best_error = get_solve_error(clip)
                    result.disabled_tracks -= disabled - forced_disabled
                    keep_names = {track.name for track in forced_outliers}
                    result.disabled_track_names = [name for name in result.disabled_track_names if name in keep_names]
                    result.message = "Solve error worsened; kept filter-selected outliers."
                    break
                solve_camera(context, clip)
                best_state_is_current = False
                result.disabled_tracks -= disabled
                disabled_set = set(outlier_names)
                result.disabled_track_names = [name for name in result.disabled_track_names if name not in disabled_set]
                result.message = "Solve error worsened after refinement."
                break
            if next_error < best_error:
                best_error = next_error
                best_state = _snapshot_mutes(clip)
                best_state_is_current = True
            if improvement < float(props.minimum_solve_improvement):
                result.message = "Minimum improvement reached."
                break
        previous_error = next_error if next_error >= 0 else previous_error

    if best_state and best_error < float("inf") and not best_state_is_current:
        _restore_mutes(clip, best_state)
        update_progress("Final solve")
        solve_camera(context, clip)
    final_error = get_solve_error(clip)
    result.solve_error_after = final_error
    if not result.message:
        result.message = "Refine finished."
    return result


def analyze_solve(clip, props):
    return collect_error_tracks(clip, props)


def _geometry_outliers(clip, props):
    if not bool(getattr(props, "enable_ransac", True)):
        return []
    width, height = _clip_dimensions(clip)
    if width <= 0 or height <= 0:
        return []
    candidates = []
    tracks_by_id = {}
    for track_id, track in enumerate(target_tracks(clip), start=1):
        if not _can_refine_track(track, props):
            continue
        samples = []
        for marker in track.markers:
            if marker.mute:
                continue
            x, y = marker_co_to_pixel(tuple(marker.co), width, height)
            samples.append(TrackSample(int(marker.frame), float(x), float(y)))
        if len(samples) < 2:
            continue
        candidates.append(TrackCandidate(track_id, int(samples[0].frame), samples=samples))
        tracks_by_id[track_id] = track
    if not candidates:
        return []
    scale = 1.0
    if bool(getattr(props, "auto_scale_pixel_parameters", True)):
        scale = max(0.05, float(width) / float(REFERENCE_ANALYSIS_WIDTH))
    settings = FilteringSettings(
        enable_ransac=True,
        ransac_model=str(getattr(props, "ransac_model", "FUNDAMENTAL")),
        ransac_threshold=float(getattr(props, "ransac_threshold", 2.0)) * scale,
        ransac_confidence=float(getattr(props, "ransac_confidence", 0.99)),
        ransac_minimum_points=int(getattr(props, "ransac_minimum_points", 12)),
    )
    result = filter_multibaseline_geometry(candidates, settings)
    return [
        (tracks_by_id[track_id], 1.0e6)
        for track_id in result.rejected_track_ids
        if track_id in tracks_by_id
    ]


def _track_error(track) -> float | None:
    for attr in ("average_error", "bundle"):
        try:
            value = float(getattr(track, attr))
        except Exception:
            continue
        if value >= 0:
            return value
    return None


def _can_refine_track(track, props) -> bool:
    markers = list(track.markers)
    if not markers or all(marker.mute for marker in markers):
        return False
    if bool(props.protect_selected_tracks) and bool(track.select):
        return False
    if bool(props.protect_existing_tracks) and not is_autotrack_track(track):
        return False
    if track.name.startswith(str(props.protected_name_prefix or PROTECTED_PREFIX)):
        return False
    if bool(track.lock):
        return False
    return True


def _enabled_track_count(clip) -> int:
    count = 0
    for track in target_tracks(clip):
        markers = list(track.markers)
        if markers and not all(marker.mute for marker in markers):
            count += 1
    return count


def _snapshot_mutes(clip):
    state = {}
    for track in target_tracks(clip):
        state[track.name] = {int(marker.frame): bool(marker.mute) for marker in track.markers}
    return state


def _restore_mutes(clip, state) -> None:
    for track in target_tracks(clip):
        marker_state = state.get(track.name)
        if marker_state is None:
            continue
        for marker in track.markers:
            if int(marker.frame) in marker_state:
                marker.mute = marker_state[int(marker.frame)]
