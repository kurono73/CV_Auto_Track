from __future__ import annotations

import copy
import time
from dataclasses import dataclass

from .tracking_types import TrackCandidate


_DENSITY_INDEPENDENT_EXCLUDE = {
    "track_density_percent",
    "auto_track_budget",
    "maximum_total_tracks",
    "auto_bake_track_limit",
    "maximum_baked_tracks",
    "track_replace_mode",
    "auto_solve_refine",
    "auto_solve_keyframes",
    "bake_pattern_size",
    "bake_search_size",
    "full_auto_refine_iterations",
    "maximum_refine_iterations",
    "target_solve_error",
    "minimum_solve_improvement",
    "delete_refined_tracks",
    "protect_selected_tracks",
    "protect_existing_tracks",
    "protected_name_prefix",
    "analyze_solve_action",
    "cache_size",
}

_KEY_PROPERTY_NAMES = (
    "preset",
    "frame_range_mode",
    "custom_start_frame",
    "custom_end_frame",
    "tracking_direction",
    "analysis_scale",
    "detector_type",
    "auto_scale_pixel_parameters",
    "minimum_analysis_width",
    "minimum_analysis_height",
    "maximum_features",
    "quality_level",
    "minimum_distance",
    "block_size",
    "use_harris_detector",
    "harris_k",
    "edge_margin",
    "window_size",
    "pyramid_levels",
    "termination_count",
    "termination_epsilon",
    "minimum_eigen_threshold",
    "maximum_lk_error",
    "maximum_motion",
    "enable_forward_backward",
    "maximum_fb_error",
    "enable_appearance_check",
    "appearance_patch_size",
    "minimum_appearance_correlation",
    "enable_edge_ambiguity_check",
    "edge_response_patch_size",
    "minimum_corner_ratio",
    "enable_silhouette_proximity_check",
    "silhouette_edge_radius",
    "silhouette_edge_percentile",
    "silhouette_minimum_corner_ratio",
    "enable_redetect",
    "adaptive_redetect",
    "redetect_interval",
    "minimum_active_tracks",
    "target_track_count",
    "auto_target_track_count",
    "lead_edge_redetect",
    "detect_only_when_needed",
    "enable_ransac",
    "ransac_model",
    "ransac_threshold",
    "ransac_confidence",
    "ransac_minimum_points",
    "filter_preset",
    "filter_preset_compact",
    "duplicate_distance",
    "minimum_track_length",
    "preferred_track_length",
    "minimum_valid_ratio",
    "enable_acceleration_filter",
    "acceleration_multiplier",
    "acceleration_minimum",
    "acceleration_minimum_ratio",
    "enable_local_motion_coherence",
    "local_motion_radius",
    "local_motion_multiplier",
    "local_motion_minimum_residual",
    "local_motion_minimum_tracks",
    "local_motion_minimum_ratio",
    "grid_columns",
    "grid_rows",
    "maximum_tracks_per_cell",
    "minimum_tracks_per_cell",
    "distribution_strength",
    "use_mask",
    "mask_source",
    "external_mask_channel",
    "mask_mode",
    "mask_margin",
    "preserve_existing_tracks",
    "use_existing_tracks_as_exclusion_points",
)


@dataclass(slots=True)
class CandidateCacheEntry:
    key: tuple
    candidates: list[TrackCandidate]
    analysis_width: int
    analysis_height: int
    frame_count: int
    created_at: float

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def clone_candidates(self) -> list[TrackCandidate]:
        return copy.deepcopy(self.candidates)


_CACHE: dict[tuple, CandidateCacheEntry] = {}
_CACHE_ORDER: list[tuple] = []
_MAX_CACHE_ENTRIES = 4


def make_cache_key(context, clip, props) -> tuple:
    scene = getattr(context, "scene", None)
    mode = str(getattr(props, "frame_range_mode", "") or "")
    direction = str(getattr(props, "tracking_direction", "") or "")
    scene_frame = int(getattr(scene, "frame_current", 0)) if mode in {"CURRENT_TO_END", "START_TO_CURRENT"} or direction == "CURRENT" else 0
    clip_key = (
        getattr(clip, "session_uid", None) or id(clip),
        str(getattr(clip, "filepath", "") or ""),
        int(getattr(clip, "frame_duration", 0) or 0),
        int(getattr(clip, "frame_start", 0) or 0),
        int(getattr(clip, "frame_offset", 0) or 0),
    )
    values = tuple(
        (name, _stable_value(getattr(props, name, None)))
        for name in _KEY_PROPERTY_NAMES
        if name not in _DENSITY_INDEPENDENT_EXCLUDE
    )
    mask = _stable_value(getattr(props, "tracking_mask", None))
    external_mask = _stable_value(getattr(props, "external_mask_clip", None))
    return (clip_key, scene_frame, mask, external_mask, values)


def store_candidate_cache(context, clip, props, candidates: list[TrackCandidate], width: int, height: int, frame_count: int) -> None:
    key = make_cache_key(context, clip, props)
    entry = CandidateCacheEntry(
        key=key,
        candidates=copy.deepcopy(candidates),
        analysis_width=int(width),
        analysis_height=int(height),
        frame_count=int(frame_count),
        created_at=time.time(),
    )
    _CACHE[key] = entry
    if key in _CACHE_ORDER:
        _CACHE_ORDER.remove(key)
    _CACHE_ORDER.append(key)
    while len(_CACHE_ORDER) > _MAX_CACHE_ENTRIES:
        old_key = _CACHE_ORDER.pop(0)
        _CACHE.pop(old_key, None)


def get_candidate_cache(context, clip, props) -> CandidateCacheEntry | None:
    key = make_cache_key(context, clip, props)
    entry = _CACHE.get(key)
    if entry is not None and key in _CACHE_ORDER:
        _CACHE_ORDER.remove(key)
        _CACHE_ORDER.append(key)
    return entry


def has_candidate_cache(context, clip, props) -> bool:
    entry = get_candidate_cache(context, clip, props)
    return bool(entry and entry.candidate_count > 0 and entry.analysis_width > 0 and entry.analysis_height > 0)


def clear_candidate_cache() -> None:
    _CACHE.clear()
    _CACHE_ORDER.clear()


def _stable_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return (
        getattr(value, "session_uid", None) or id(value),
        str(getattr(value, "name", "") or ""),
        str(getattr(value, "filepath", "") or ""),
        int(getattr(value, "frame_duration", 0) or 0),
        int(getattr(value, "frame_start", 0) or 0),
        int(getattr(value, "frame_offset", 0) or 0),
    )
