from __future__ import annotations

import time

import bpy

from .blender_tracks import (
    bake_candidates,
    existing_track_points,
    is_autotrack_track,
    mute_autotrack_tracks,
    target_tracks,
)
from .candidate_cache import get_candidate_cache, store_candidate_cache
from .constants import REFERENCE_ANALYSIS_WIDTH
from .feature_detection import DetectionSettings, detect_shi_tomasi
from .frame_provider import FrameProvider, range_from_props
from .masks import build_detection_mask, clear_detection_mask_cache
from .optical_flow import LKSettings, track_points_batch, track_points_step
from .track_distribution import DistributionSettings, cell_for_point, enforce_distribution, limit_enabled_tracks
from .track_filtering import FilteringSettings, filter_tracks, ransac_inlier_rate_for_pair
from .tracking_types import TrackCandidate, TrackingStats, TrackSample


class DetectTrackSession:
    def __init__(self, context, clip, props):
        self.context = context
        self.clip = clip
        self.props = props
        self.started = time.perf_counter()
        self.stats = TrackingStats()
        frame_range = range_from_props(context, clip, props)
        if frame_range.start > frame_range.end:
            raise ValueError("Start Frame must be less than or equal to End Frame.")
        self.frames = list(range(frame_range.start, frame_range.end + 1))
        if not self.frames:
            raise ValueError("No analysis frames are available.")
        self.provider = FrameProvider(
            clip,
            analysis_scale=float(props.analysis_scale) / 100.0,
            cache_size=int(props.cache_size),
            blend_filepath=context.blend_data.filepath,
            minimum_analysis_width=int(props.minimum_analysis_width),
            minimum_analysis_height=int(props.minimum_analysis_height),
        )
        self.direction = str(getattr(props, "tracking_direction", "FORWARD"))
        if self.direction not in {"FORWARD", "AUTO"}:
            raise ValueError("Modal tracking currently supports Forward and Auto directions.")
        self.passes = [
            _StreamPassSession(
                context,
                clip,
                props,
                self.provider,
                self.frames,
                progress_label="Forward",
            )
        ]
        self.pass_index = 0
        self.done = False
        clear_detection_mask_cache()

    @property
    def progress(self) -> float:
        if self.done:
            return 1.0
        if self.direction != "AUTO":
            return self.passes[0].progress
        return min(1.0, (float(self.pass_index) + self.passes[self.pass_index].progress) / 2.0)

    @property
    def candidates(self) -> list[TrackCandidate]:
        return [candidate for session in self.passes for candidate in session.candidates]

    def step(self) -> str:
        if self.done:
            return "Done"
        current_pass = self.passes[self.pass_index]
        message = current_pass.step()
        if current_pass.done:
            if self.direction == "AUTO" and self.pass_index == 0:
                forward_candidates = current_pass.candidates
                self.passes.append(
                    _StreamPassSession(
                        self.context,
                        self.clip,
                        self.props,
                        self.provider,
                        list(reversed(self.frames)),
                        existing_candidates=forward_candidates,
                        first_id=len(forward_candidates) + 1,
                        source_batch_offset=len(forward_candidates),
                        progress_label="Backward",
                    )
                )
                self.pass_index = 1
                return "Starting backward pass"
            self.done = True
        return message

    def cancel(self) -> TrackingStats:
        self.done = True
        self.provider.close()
        clear_detection_mask_cache()
        self.stats.processing_time = time.perf_counter() - self.started
        self.stats.cancellation_state = "Cancelled before bake"
        return self.stats

    def finish(self) -> tuple[list[TrackCandidate], TrackingStats]:
        try:
            candidates = self.candidates
            _finalize_detect_track(
                self.context,
                self.clip,
                self.props,
                self.provider,
                self.frames,
                candidates,
                self.stats,
            )
            self.stats.cancellation_state = "Completed"
            return candidates, self.stats
        finally:
            self.stats.processing_time = time.perf_counter() - self.started
            self.provider.close()
            clear_detection_mask_cache()


class _StreamPassSession:
    def __init__(
        self,
        context,
        clip,
        props,
        provider,
        frames,
        existing_candidates=None,
        first_id=1,
        source_batch_offset=0,
        progress_label="Tracking",
    ):
        self.context = context
        self.clip = clip
        self.props = props
        self.provider = provider
        self.frames = list(frames)
        self.existing_candidates = existing_candidates or []
        self.source_batch_offset = int(source_batch_offset)
        self.progress_label = str(progress_label)
        self.detection_settings = _detection_settings(props, self.provider)
        self.lk_settings = _lk_settings(props, self.provider)
        self.target_track_count = _effective_target_track_count(props, self.provider, len(self.frames))
        self.minimum_active_tracks = _effective_minimum_active_tracks(props, self.target_track_count)
        self.track_budget = _track_budget(props, len(self.frames), self.provider)
        self.candidates: list[TrackCandidate] = []
        self.active: list[tuple[TrackCandidate, float, float]] = []
        self.previous_gray = None
        self.frame_index = 0
        self.next_id = int(first_id)
        self.done = False
        self.last_detection_frame = None
        self.last_motion_vector = (0.0, 0.0)

    @property
    def progress(self) -> float:
        return min(1.0, self.frame_index / max(1, len(self.frames)))

    def step(self) -> str:
        if self.done:
            return "Done"
        frame = self.frames[self.frame_index]
        gray = self.provider.read_gray(frame)
        height, width = gray.shape[:2]
        if self.previous_gray is not None and self.active:
            points = [(x, y) for _, x, y in self.active]
            step_results = track_points_step(self.previous_gray, gray, points, self.lk_settings)
            next_active = []
            motion_steps = []
            mask_cache = {}
            for (candidate, _old_x, _old_y), (x, y, lk_error, fb_error, reason) in zip(self.active, step_results):
                if reason is None:
                    if _outside_mask_boundary(
                        self.context,
                        self.clip,
                        self.props,
                        self.provider,
                        frame,
                        x,
                        y,
                        mask_cache,
                        previous=(_old_x, _old_y),
                    ):
                        candidate.termination_reason = candidate.termination_reason or "Mask boundary"
                    else:
                        candidate.samples.append(_sample(frame, x, y, lk_error, fb_error))
                        next_active.append((candidate, x, y))
                        motion_steps.append((float(x) - float(_old_x), float(y) - float(_old_y)))
                else:
                    candidate.termination_reason = candidate.termination_reason or reason
            self.active = next_active
            self.last_motion_vector = _median_motion_vector(motion_steps)
        self._detect_if_needed(frame, gray, width, height)
        self.previous_gray = gray
        self.frame_index += 1
        if self.frame_index >= len(self.frames):
            for candidate in self.candidates:
                candidate.samples.sort(key=lambda sample: sample.frame)
            self.done = True
        return f"{self.progress_label} {self.frame_index}/{len(self.frames)}, active {len(self.active)}"

    def _detect_if_needed(self, frame, gray, width, height) -> None:
        active_points = [(x, y) for _, x, y in self.active]
        existing_candidate_points = _active_points(self.existing_candidates, frame)
        combined_points = active_points + existing_candidate_points
        underfilled_cells = _underfilled_cells_for_points(combined_points, width, height, self.props)
        underfilled_cells = _add_lead_edge_cells(underfilled_cells, self.last_motion_vector, width, height, self.props)
        should_detect = self._should_detect(frame, bool(underfilled_cells))
        if (
            self.props.detect_only_when_needed
            and len(active_points) + len(existing_candidate_points) >= int(self.minimum_active_tracks)
            and not underfilled_cells
        ):
            should_detect = False
        if not should_detect or len(self.candidates) >= int(self.track_budget):
            return
        existing_points = []
        if self.props.use_existing_tracks_as_exclusion_points:
            existing_points.extend(
                existing_track_points(
                    self.clip,
                    frame,
                    width,
                    height,
                    include_autotrack=not _replaces_autotrack(self.props),
                )
            )
        existing_points.extend(combined_points)
        distribution_deficit = _cell_deficit(underfilled_cells, combined_points, width, height, self.props)
        total_active = len(active_points) + len(existing_candidate_points)
        capacity = max(
            0,
            min(
                max(int(self.target_track_count) - total_active, distribution_deficit),
                int(self.track_budget) - len(self.candidates) - len(self.existing_candidates),
            ),
        )
        if capacity <= 0:
            return
        mask = build_detection_mask(self.context, self.clip, self.props, width, height, self.provider.np, frame=frame)
        mask = _limit_mask_to_cells(mask, underfilled_cells, width, height, self.props, self.provider.np)
        self.last_detection_frame = int(frame)
        points = detect_shi_tomasi(gray, self.detection_settings, existing_points, mask)[:capacity]
        for point in points:
            candidate = TrackCandidate(self.next_id, int(frame), source_batch_id=self.source_batch_offset + len(self.candidates) + 1)
            candidate.samples.append(_sample(frame, point[0], point[1]))
            self.candidates.append(candidate)
            self.active.append((candidate, float(point[0]), float(point[1])))
            self.next_id += 1

    def _should_detect(self, frame, distribution_underfilled: bool = False) -> bool:
        if not self.props.enable_redetect:
            return frame == self.frames[0]
        if frame == self.frames[0]:
            return True
        if not bool(self.props.adaptive_redetect):
            return _is_periodic_detection_frame(frame, self.frames[0], self.props)
        if len(self.active) >= int(self.minimum_active_tracks) and not distribution_underfilled:
            return False
        if self.last_detection_frame is None:
            return True
        return abs(int(frame) - int(self.last_detection_frame)) >= int(self.props.redetect_interval)


def run_detect_track(context, clip, props, cancel_cb=None, progress_cb=None) -> tuple[list[TrackCandidate], TrackingStats]:
    started = time.perf_counter()
    stats = TrackingStats()
    frame_range = range_from_props(context, clip, props)
    if frame_range.start > frame_range.end:
        raise ValueError("Start Frame must be less than or equal to End Frame.")

    scale = float(props.analysis_scale) / 100.0
    provider = FrameProvider(
        clip,
        analysis_scale=scale,
        cache_size=int(props.cache_size),
        blend_filepath=context.blend_data.filepath,
        minimum_analysis_width=int(props.minimum_analysis_width),
        minimum_analysis_height=int(props.minimum_analysis_height),
    )
    clear_detection_mask_cache()
    try:
        frames = list(range(frame_range.start, frame_range.end + 1))
        if not frames:
            raise ValueError("No analysis frames are available.")
        if props.tracking_direction == "FORWARD":
            candidates = _run_forward_stream(
                context,
                clip,
                props,
                provider,
                frames,
                cancel_cb=cancel_cb,
                progress_cb=progress_cb,
            )
            stats.cancellation_state = "Cancelled before bake" if cancel_cb and cancel_cb() else "Completed"
        elif props.tracking_direction == "AUTO":
            candidates = _run_auto_bidirectional_stream(
                context,
                clip,
                props,
                provider,
                frames,
                cancel_cb=cancel_cb,
                progress_cb=progress_cb,
            )
            stats.cancellation_state = "Cancelled before bake" if cancel_cb and cancel_cb() else "Completed"
        else:
            candidates = _run_detection_batches(context, clip, props, provider, frames, stats, cancel_cb, progress_cb)

        if stats.cancellation_state.startswith("Cancelled"):
            return [], stats
        _finalize_detect_track(context, clip, props, provider, frames, candidates, stats)
        return candidates, stats
    finally:
        stats.processing_time = time.perf_counter() - started
        provider.close()
        clear_detection_mask_cache()


def _finalize_detect_track(context, clip, props, provider, frames: list[int], candidates: list[TrackCandidate], stats: TrackingStats) -> None:
    filtering_settings = _filtering_settings(props, provider)
    filter_tracks(candidates, filtering_settings)
    if provider.analysis_width and provider.analysis_height:
        distribution_settings = _distribution_settings(props)
        enforce_distribution(
            candidates,
            provider.analysis_width,
            provider.analysis_height,
            distribution_settings,
            temporal_bucket_size=_distribution_temporal_bucket(props),
        )
        store_candidate_cache(
            context,
            clip,
            props,
            [candidate for candidate in candidates if candidate.valid_samples],
            provider.analysis_width,
            provider.analysis_height,
            len(frames),
        )
        _limit_candidates_for_bake(candidates, provider, props, len(frames))
    _sort_candidates_for_bake(candidates)
    stats.generated_tracks = len(candidates)
    stats.valid_tracks = sum(1 for item in candidates if not item.disabled)
    stats.disabled_tracks = sum(1 for item in candidates if item.disabled)
    _fill_length_stats(stats, candidates)
    stats.ransac_inlier_rate = _quick_ransac_rate(candidates, filtering_settings)
    if _replaces_autotrack(props):
        _delete_autotrack_tracks(context, clip)
    created, disabled = bake_candidates(
        clip,
        candidates,
        provider.analysis_width,
        provider.analysis_height,
        replace_autotrack=False,
        bake_disabled=False,
        pattern_size=int(getattr(props, "bake_pattern_size", 15)),
        search_size=int(getattr(props, "bake_search_size", 30)),
    )
    stats.generated_tracks = created
    stats.disabled_tracks = disabled
    stats.valid_tracks = created - disabled


def add_cached_candidates(context, clip, props) -> TrackingStats:
    started = time.perf_counter()
    cache = get_candidate_cache(context, clip, props)
    if cache is None or cache.analysis_width <= 0 or cache.analysis_height <= 0:
        return TrackingStats(processing_time=time.perf_counter() - started, cancellation_state="No cached OpenCV tracks are available.")
    candidates = [
        candidate
        for candidate in cache.clone_candidates()
        if not candidate.disabled and candidate.valid_samples
    ]
    removed_overlaps = _disable_candidates_near_existing_tracks(
        clip,
        candidates,
        int(cache.analysis_width),
        int(cache.analysis_height),
        _cached_add_exclusion_distance(props, _CachedProvider(cache.analysis_width, cache.analysis_height)),
    )
    add_limit = _cached_add_track_limit(props, clip, cache)
    if add_limit <= 0:
        return TrackingStats(processing_time=time.perf_counter() - started, cancellation_state="No additional cached tracks are available.")
    limit_enabled_tracks(
        candidates,
        int(cache.analysis_width),
        int(cache.analysis_height),
        _distribution_settings(props),
        add_limit,
        temporal_bucket_size=_distribution_temporal_bucket(props),
    )
    _sort_candidates_for_bake(candidates)
    created, disabled = bake_candidates(
        clip,
        candidates,
        int(cache.analysis_width),
        int(cache.analysis_height),
        replace_autotrack=False,
        bake_disabled=False,
        pattern_size=int(getattr(props, "bake_pattern_size", 15)),
        search_size=int(getattr(props, "bake_search_size", 30)),
    )
    message = "Added cached tracks" if created else "No additional cached tracks are available."
    stats = TrackingStats(
        generated_tracks=created,
        valid_tracks=created - disabled,
        disabled_tracks=disabled,
        processing_time=time.perf_counter() - started,
        cancellation_state=message if not created else "Completed",
        warning_count=1 if removed_overlaps and not created else 0,
    )
    return stats


def _run_detection_batches(context, clip, props, provider, frames, stats, cancel_cb=None, progress_cb=None):
    detection_frames = _detection_frames(frames, props, _current_frame_in_range(context, clip, frames))
    track_budget = _track_budget(props, len(frames), provider)
    target_track_count = _effective_target_track_count(props, provider, len(frames))
    minimum_active_tracks = _effective_minimum_active_tracks(props, target_track_count)
    candidates: list[TrackCandidate] = []
    next_id = 1
    for batch_id, detection_frame in enumerate(detection_frames, start=1):
        if cancel_cb and cancel_cb():
            stats.cancellation_state = "Cancelled before bake"
            break
        gray = provider.read_gray(detection_frame)
        height, width = gray.shape[:2]
        detection_settings = _detection_settings(props, provider)
        existing_points = []
        if props.use_existing_tracks_as_exclusion_points:
            existing_points.extend(
                existing_track_points(
                    clip,
                    detection_frame,
                    width,
                    height,
                    include_autotrack=not _replaces_autotrack(props),
                )
            )
        active = _active_points(candidates, detection_frame)
        existing_points.extend(active)
        mask = build_detection_mask(context, clip, props, width, height, provider.np, frame=detection_frame)
        underfilled_cells = _underfilled_cells_for_points(active, width, height, props)
        mask = _limit_mask_to_cells(mask, underfilled_cells, width, height, props, provider.np)
        points = detect_shi_tomasi(gray, detection_settings, existing_points, mask)
        if props.detect_only_when_needed and len(active) >= int(minimum_active_tracks) and not underfilled_cells:
            points = []
        distribution_deficit = _cell_deficit(underfilled_cells, active, width, height, props)
        points = points[: max(0, max(int(target_track_count) - len(active), distribution_deficit))]
        if progress_cb:
            progress_cb(batch_id - 1, len(detection_frames), f"Detected {len(points)} points at frame {detection_frame}")
        def batch_progress(current, total, active_count):
            if not progress_cb:
                return
            fraction = 0.0 if total <= 0 else max(0.0, min(1.0, current / max(1, total)))
            progress_cb(
                (batch_id - 1) + fraction,
                len(detection_frames),
                f"Tracking frame {detection_frame}: {current}/{total}, active {active_count}",
            )

        new_tracks = _tracks_from_points(
            provider,
            context,
            clip,
            _batch_tracking_frames(frames, detection_frames, batch_id - 1, props),
            detection_frame,
            points,
            next_id,
            batch_id,
            props,
            cancel_cb=cancel_cb,
            progress_cb=batch_progress if progress_cb else None,
        )
        next_id += len(new_tracks)
        candidates.extend(new_tracks)
        if len(candidates) >= int(track_budget):
            candidates = candidates[: int(track_budget)]
            break
    return candidates


def _run_forward_stream(context, clip, props, provider, frames, cancel_cb=None, progress_cb=None):
    return _run_stream_pass(
        context,
        clip,
        props,
        provider,
        frames,
        existing_candidates=[],
        first_id=1,
        source_batch_offset=0,
        cancel_cb=cancel_cb,
        progress_cb=progress_cb,
        progress_label="Forward",
    )


def _run_auto_bidirectional_stream(context, clip, props, provider, frames, cancel_cb=None, progress_cb=None):
    def forward_progress(index, total, message):
        if progress_cb:
            progress_cb(index, max(1, total * 2), message)

    forward_candidates = _run_stream_pass(
        context,
        clip,
        props,
        provider,
        frames,
        existing_candidates=[],
        first_id=1,
        source_batch_offset=0,
        cancel_cb=cancel_cb,
        progress_cb=forward_progress,
        progress_label="Forward",
    )
    if cancel_cb and cancel_cb():
        return forward_candidates

    def backward_progress(index, total, message):
        if progress_cb:
            progress_cb(total + index, max(1, total * 2), message)

    backward_candidates = _run_stream_pass(
        context,
        clip,
        props,
        provider,
        list(reversed(frames)),
        existing_candidates=forward_candidates,
        first_id=len(forward_candidates) + 1,
        source_batch_offset=len(forward_candidates),
        cancel_cb=cancel_cb,
        progress_cb=backward_progress,
        progress_label="Backward",
    )
    return forward_candidates + backward_candidates


def _run_stream_pass(
    context,
    clip,
    props,
    provider,
    frames,
    existing_candidates=None,
    first_id=1,
    source_batch_offset=0,
    cancel_cb=None,
    progress_cb=None,
    progress_label="Tracking",
):
    existing_candidates = existing_candidates or []
    track_budget = _track_budget(props, len(frames), provider)
    target_track_count = _effective_target_track_count(props, provider, len(frames))
    minimum_active_tracks = _effective_minimum_active_tracks(props, target_track_count)
    detection_settings = _detection_settings(props, provider)
    lk_settings = _lk_settings(props, provider)
    candidates: list[TrackCandidate] = []
    active: list[tuple[TrackCandidate, float, float]] = []
    next_id = int(first_id)
    previous_gray = None
    last_detection_frame = None
    last_motion_vector = (0.0, 0.0)

    for frame_index, frame in enumerate(frames):
        if cancel_cb and cancel_cb():
            break
        gray = provider.read_gray(frame)
        height, width = gray.shape[:2]
        if previous_gray is not None and active:
            points = [(x, y) for _, x, y in active]
            step_results = track_points_step(previous_gray, gray, points, lk_settings)
            next_active = []
            motion_steps = []
            mask_cache = {}
            for (candidate, _old_x, _old_y), (x, y, lk_error, fb_error, reason) in zip(active, step_results):
                if reason is None:
                    if _outside_mask_boundary(
                        context,
                        clip,
                        props,
                        provider,
                        frame,
                        x,
                        y,
                        mask_cache,
                        previous=(_old_x, _old_y),
                    ):
                        candidate.termination_reason = candidate.termination_reason or "Mask boundary"
                    else:
                        candidate.samples.append(_sample(frame, x, y, lk_error, fb_error))
                        next_active.append((candidate, x, y))
                        motion_steps.append((float(x) - float(_old_x), float(y) - float(_old_y)))
                else:
                    candidate.termination_reason = candidate.termination_reason or reason
            active = next_active
            last_motion_vector = _median_motion_vector(motion_steps)
        active_points = [(x, y) for _, x, y in active]
        existing_candidate_points = _active_points(existing_candidates, frame)
        combined_points = active_points + existing_candidate_points
        underfilled_cells = _underfilled_cells_for_points(combined_points, width, height, props)
        underfilled_cells = _add_lead_edge_cells(underfilled_cells, last_motion_vector, width, height, props)
        if bool(props.adaptive_redetect) and props.enable_redetect:
            should_detect = (
                frame == frames[0]
                or (
                    (len(active) < int(minimum_active_tracks) or bool(underfilled_cells))
                    and (
                        last_detection_frame is None
                        or abs(int(frame) - int(last_detection_frame)) >= int(props.redetect_interval)
                    )
                )
            )
        else:
            should_detect = frame == frames[0]
            if props.enable_redetect and not bool(props.adaptive_redetect):
                should_detect = _is_periodic_detection_frame(frame, frames[0], props)
            if props.detect_only_when_needed and len(active) >= int(minimum_active_tracks) and not underfilled_cells:
                should_detect = False
        if should_detect and len(candidates) < int(track_budget):
            existing_points = []
            if props.use_existing_tracks_as_exclusion_points:
                existing_points.extend(
                    existing_track_points(
                        clip,
                        frame,
                        width,
                        height,
                        include_autotrack=not _replaces_autotrack(props),
                    )
                )
            existing_points.extend(combined_points)
            mask = build_detection_mask(context, clip, props, width, height, provider.np, frame=frame)
            mask = _limit_mask_to_cells(mask, underfilled_cells, width, height, props, provider.np)
            distribution_deficit = _cell_deficit(underfilled_cells, combined_points, width, height, props)
            total_active = len(active_points) + len(existing_candidate_points)
            capacity = max(
                0,
                min(
                    max(int(target_track_count) - total_active, distribution_deficit),
                    int(track_budget) - len(candidates) - len(existing_candidates),
                ),
            )
            if capacity <= 0:
                previous_gray = gray
                if progress_cb:
                    progress_cb(frame_index + 1, len(frames), f"{progress_label} {frame_index + 1}/{len(frames)}, active {len(active)}")
                continue
            last_detection_frame = int(frame)
            points = detect_shi_tomasi(gray, detection_settings, existing_points, mask)[:capacity]
            for point in points:
                candidate = TrackCandidate(next_id, int(frame), source_batch_id=source_batch_offset + len(candidates) + 1)
                candidate.samples.append(_sample(frame, point[0], point[1]))
                candidates.append(candidate)
                active.append((candidate, float(point[0]), float(point[1])))
                next_id += 1
        previous_gray = gray
        if progress_cb:
            progress_cb(frame_index + 1, len(frames), f"{progress_label} {frame_index + 1}/{len(frames)}, active {len(active)}")
    for candidate in candidates:
        candidate.samples.sort(key=lambda sample: sample.frame)
    return candidates


def _tracks_from_points(
    provider,
    context,
    clip,
    all_frames,
    detection_frame,
    points,
    first_track_id,
    batch_id,
    props,
    cancel_cb=None,
    progress_cb=None,
) -> list[TrackCandidate]:
    direction = props.tracking_direction
    lk_settings = _lk_settings(props, provider)
    merged_samples: list[list] = [[] for _ in points]
    terminations: list[str | None] = [None for _ in points]
    if direction in {"BACKWARD", "BOTH", "CURRENT"}:
        backward_frames = [frame for frame in all_frames if frame <= detection_frame]
        backward_frames.sort(reverse=True)
        frames = provider.read_frames(backward_frames)
        back_samples, back_terms = track_points_batch(frames, points, lk_settings, progress_cb=progress_cb)
        for index, samples in enumerate(back_samples):
            boundary_samples, boundary_term = _trim_samples_to_mask(context, clip, props, provider, samples)
            merged_samples[index].extend(reversed(boundary_samples))
            terminations[index] = terminations[index] or boundary_term
            terminations[index] = terminations[index] or back_terms[index]
        if cancel_cb and cancel_cb():
            return []
    if direction in {"FORWARD", "BOTH", "CURRENT"}:
        forward_frames = [frame for frame in all_frames if frame >= detection_frame]
        frames = provider.read_frames(forward_frames)
        forward_samples, forward_terms = track_points_batch(frames, points, lk_settings, progress_cb=progress_cb)
        for index, samples in enumerate(forward_samples):
            samples, boundary_term = _trim_samples_to_mask(context, clip, props, provider, samples)
            if merged_samples[index] and samples:
                merged_samples[index].extend(samples[1:])
            else:
                merged_samples[index].extend(samples)
            terminations[index] = terminations[index] or boundary_term
            terminations[index] = terminations[index] or forward_terms[index]

    candidates = []
    for index, samples in enumerate(merged_samples):
        candidate = TrackCandidate(
            id=first_track_id + index,
            detection_frame=int(detection_frame),
            samples=sorted(samples, key=lambda item: item.frame),
            termination_reason=terminations[index],
            source_batch_id=batch_id,
        )
        if candidate.length:
            candidates.append(candidate)
    return candidates


def _batch_tracking_frames(all_frames: list[int], detection_frames: list[int], detection_index: int, props) -> list[int]:
    detection_frame = int(detection_frames[detection_index])
    direction = str(props.tracking_direction)
    if direction == "BACKWARD" or len(detection_frames) <= 1:
        return all_frames
    if direction in {"BOTH", "CURRENT"} and detection_index == 0:
        return all_frames
    previous_detection = _previous_detection(detection_frames, detection_index)
    next_detection = _next_detection(detection_frames, detection_index)
    if direction == "FORWARD":
        start = detection_frame
        end = _neighbor_midpoint(detection_frame, next_detection, all_frames[-1])
    elif direction == "BACKWARD":
        start = _neighbor_midpoint(previous_detection, detection_frame, all_frames[0])
        end = detection_frame
    else:
        start = _neighbor_midpoint(previous_detection, detection_frame, all_frames[0])
        end = _neighbor_midpoint(detection_frame, next_detection, all_frames[-1])
    start = max(min(all_frames), min(max(all_frames), int(start)))
    end = max(min(all_frames), min(max(all_frames), int(end)))
    return [frame for frame in all_frames if start <= frame <= end]


def _previous_detection(detection_frames: list[int], detection_index: int) -> int | None:
    current = int(detection_frames[detection_index])
    previous_values = [int(frame) for frame in detection_frames if int(frame) < current]
    return max(previous_values) if previous_values else None


def _next_detection(detection_frames: list[int], detection_index: int) -> int | None:
    current = int(detection_frames[detection_index])
    next_values = [int(frame) for frame in detection_frames if int(frame) > current]
    return min(next_values) if next_values else None


def _neighbor_midpoint(frame_a: int | None, frame_b: int, fallback: int) -> int:
    if frame_a is None or frame_b is None:
        return int(fallback)
    return int(round((int(frame_a) + int(frame_b)) * 0.5))


def _sample(frame, x, y, lk_error=None, fb_error=None):
    return TrackSample(int(frame), float(x), float(y), lk_error=lk_error, fb_error=fb_error, valid=True)


def _trim_samples_to_mask(context, clip, props, provider, samples):
    if not _uses_tracking_mask_boundary(props):
        return list(samples), None
    kept = []
    mask_cache = {}
    previous = None
    for sample in samples:
        if _outside_mask_boundary(
            context,
            clip,
            props,
            provider,
            sample.frame,
            sample.x,
            sample.y,
            mask_cache,
            previous=previous,
        ):
            return kept, "Mask boundary"
        kept.append(sample)
        previous = (sample.x, sample.y)
    return kept, None


def _outside_mask_boundary(context, clip, props, provider, frame, x, y, mask_cache, previous=None) -> bool:
    if not _uses_tracking_mask_boundary(props):
        return False
    width = int(provider.analysis_width)
    height = int(provider.analysis_height)
    if width <= 0 or height <= 0:
        return True
    key = (int(frame), width, height)
    mask = mask_cache.get(key)
    if mask is None:
        mask = build_detection_mask(context, clip, props, width, height, provider.np, frame=int(frame))
        mask_cache[key] = mask
    xi = int(round(float(x)))
    yi = int(round(float(y)))
    if xi < 0 or yi < 0 or xi >= width or yi >= height:
        return True
    if int(mask[yi, xi]) == 0:
        return True
    if _tracking_window_hits_mask(mask, xi, yi, props, provider):
        return True
    if previous is not None and _segment_hits_mask(mask, previous[0], previous[1], float(x), float(y)):
        return True
    return False


def _tracking_window_hits_mask(mask, x: int, y: int, props, provider=None) -> bool:
    scale = _pixel_parameter_scale(props, provider)
    radius = max(0, _scale_odd_int(getattr(props, "window_size", 0), scale, minimum=3) // 2)
    if radius <= 0:
        return False
    height, width = mask.shape[:2]
    x0 = max(0, int(x) - radius)
    y0 = max(0, int(y) - radius)
    x1 = min(width, int(x) + radius + 1)
    y1 = min(height, int(y) + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return True
    return bool((mask[y0:y1, x0:x1] == 0).any())


def _segment_hits_mask(mask, x0: float, y0: float, x1: float, y1: float) -> bool:
    import math

    height, width = mask.shape[:2]
    distance = ((float(x1) - float(x0)) ** 2 + (float(y1) - float(y0)) ** 2) ** 0.5
    steps = max(1, int(math.ceil(distance)))
    for index in range(1, steps + 1):
        t = index / float(steps)
        x = int(round((float(x0) * (1.0 - t)) + (float(x1) * t)))
        y = int(round((float(y0) * (1.0 - t)) + (float(y1) * t)))
        if x < 0 or y < 0 or x >= width or y >= height:
            return True
        if int(mask[y, x]) == 0:
            return True
    return False


def _uses_tracking_mask_boundary(props) -> bool:
    return bool(getattr(props, "use_mask", False))


def _replaces_autotrack(props) -> bool:
    return str(getattr(props, "track_replace_mode", "AUTO_REUSE")) in {"AUTO_REUSE", "REPLACE_AUTOTRACK"}


def _track_budget(props, frame_count: int, provider=None) -> int:
    manual_budget = max(1, int(getattr(props, "maximum_total_tracks", 1500)))
    if not bool(getattr(props, "auto_track_budget", True)):
        return manual_budget
    import math

    target = _effective_target_track_count(props, provider, frame_count)
    interval = max(1, int(getattr(props, "redetect_interval", 15)))
    batches = 1 + int(math.ceil(max(0, int(frame_count) - 1) / float(interval)))
    pass_multiplier = 2 if str(getattr(props, "tracking_direction", "AUTO")) == "AUTO" else 1
    budget = int(target * batches * pass_multiplier)
    bake_limit = _effective_bake_track_limit(props, provider, frame_count)
    auto_cap = max(target * 2, int(round(bake_limit * 3.0)))
    return max(target, min(20000, auto_cap, budget))


def _effective_target_track_count(props, provider=None, frame_count: int = 0) -> int:
    manual = max(1, int(getattr(props, "target_track_count", 200)))
    if not bool(getattr(props, "auto_target_track_count", True)):
        return max(8, int(round(manual * _track_density_multiplier(props))))
    preset = str(getattr(props, "preset", "FAST"))
    base_by_preset = {
        "FAST": 190,
        "DYNAMIC": 250,
        "HIGH_MOTION": 300,
        "BALANCED": 320,
        "SENSITIVE": 300,
        "DETAILED": 380,
    }
    cap_by_preset = {
        "FAST": 260,
        "DYNAMIC": 380,
        "HIGH_MOTION": 460,
        "BALANCED": 460,
        "SENSITIVE": 430,
        "DETAILED": 620,
    }
    base = base_by_preset.get(preset, manual)
    scale = _analysis_density_scale(provider)
    density = _track_density_multiplier(props)
    grid_floor = _grid_track_floor(props)
    target = int(round(base * scale * density))
    cap = int(round(cap_by_preset.get(preset, max(manual, target)) * max(0.05, density)))
    return max(8, grid_floor, min(max(8, cap), target))


def _effective_minimum_active_tracks(props, target_track_count: int) -> int:
    if not bool(getattr(props, "auto_target_track_count", True)):
        return max(0, int(getattr(props, "minimum_active_tracks", 150)))
    target = max(1, int(target_track_count))
    grid_floor = _grid_track_floor(props)
    return min(target, max(grid_floor, int(round(target * 0.62))))


def _effective_bake_track_limit(props, provider=None, frame_count: int = 0) -> int:
    manual = max(8, int(getattr(props, "maximum_baked_tracks", 800)))
    if not bool(getattr(props, "auto_bake_track_limit", True)):
        return manual
    import math

    target = _effective_target_track_count(props, provider, frame_count)
    preset = str(getattr(props, "preset", "FAST"))
    multiplier_by_preset = {
        "FAST": 1.9,
        "DYNAMIC": 2.4,
        "HIGH_MOTION": 2.5,
        "BALANCED": 2.2,
        "SENSITIVE": 2.2,
        "DETAILED": 1.9,
    }
    length_scale = max(1.0, min(1.65, math.sqrt(max(1, int(frame_count)) / 200.0)))
    floor = max(8, _grid_track_floor(props) * 2, int(target * 1.25))
    automatic = int(round(target * multiplier_by_preset.get(preset, 2.0) * length_scale))
    return max(floor, min(manual, automatic))


def _limit_candidates_for_bake(candidates: list[TrackCandidate], provider, props, frame_count: int) -> int:
    width = int(getattr(provider, "analysis_width", 0))
    height = int(getattr(provider, "analysis_height", 0))
    if width <= 0 or height <= 0:
        return 0
    limit = _effective_bake_track_limit(props, provider, frame_count)
    return limit_enabled_tracks(
        candidates,
        width,
        height,
        _distribution_settings(props),
        limit,
        temporal_bucket_size=_distribution_temporal_bucket(props),
    )


class _CachedProvider:
    def __init__(self, width: int, height: int):
        self.analysis_width = int(width)
        self.analysis_height = int(height)


def _cached_add_track_limit(props, clip, cache) -> int:
    provider = _CachedProvider(cache.analysis_width, cache.analysis_height)
    existing = sum(1 for track in target_tracks(clip) if is_autotrack_track(track))
    base = max(existing, _effective_target_track_count(props, provider, int(cache.frame_count)))
    return max(8, min(160, int(round(base * 0.25))))


def _cached_add_exclusion_distance(props, provider) -> float:
    scale = _pixel_parameter_scale(props, provider)
    return max(4.0, _scale_float(max(float(getattr(props, "minimum_distance", 12.0)), float(getattr(props, "duplicate_distance", 6.0))), scale))


def _disable_candidates_near_existing_tracks(clip, candidates: list[TrackCandidate], width: int, height: int, distance: float) -> int:
    by_frame: dict[int, list[tuple[float, float]]] = {}
    removed = 0
    distance_sq = float(distance) * float(distance)
    for candidate in candidates:
        sample = _representative_candidate_sample(candidate)
        if sample is None:
            candidate.disabled = True
            removed += 1
            continue
        frame = int(sample.frame)
        if frame not in by_frame:
            by_frame[frame] = existing_track_points(clip, frame, width, height, include_autotrack=True)
        if _point_near_any((sample.x, sample.y), by_frame[frame], distance_sq):
            candidate.disabled = True
            candidate.termination_reason = "cached_overlap_prune"
            removed += 1
    return removed


def _representative_candidate_sample(candidate: TrackCandidate):
    samples = candidate.valid_samples
    if not samples:
        return None
    target_frame = int(candidate.detection_frame)
    return min(samples, key=lambda sample: abs(int(sample.frame) - target_frame))


def _point_near_any(point: tuple[float, float], points: list[tuple[float, float]], distance_sq: float) -> bool:
    px, py = point
    for x, y in points:
        dx = float(px) - float(x)
        dy = float(py) - float(y)
        if (dx * dx) + (dy * dy) <= distance_sq:
            return True
    return False


def _grid_track_floor(props) -> int:
    return max(8, int(round(
        max(1, int(getattr(props, "grid_columns", 6)))
        * max(1, int(getattr(props, "grid_rows", 4)))
        * max(1, int(getattr(props, "minimum_tracks_per_cell", 3)))
        * _track_density_multiplier(props)
    )))


def _effective_minimum_tracks_per_cell(props) -> int:
    base = max(0, int(getattr(props, "minimum_tracks_per_cell", 3)))
    if base <= 0:
        return 0
    return max(1, int(round(base * _track_density_multiplier(props))))


def _track_density_multiplier(props) -> float:
    return max(0.1, float(getattr(props, "track_density_percent", 100.0)) / 100.0)


def _analysis_density_scale(provider=None) -> float:
    width = int(getattr(provider, "analysis_width", 0) or 0)
    height = int(getattr(provider, "analysis_height", 0) or 0)
    if width <= 0 or height <= 0:
        return 1.0
    reference_pixels = 1920.0 * 1080.0
    pixel_ratio = max(0.1, (float(width) * float(height)) / reference_pixels)
    return max(0.75, min(1.45, pixel_ratio ** 0.25))


def _underfilled_cells_for_points(points: list[tuple[float, float]], width: int, height: int, props) -> list[tuple[int, int]]:
    settings = _distribution_settings(props)
    minimum = _effective_minimum_tracks_per_cell(props)
    if minimum <= 0 or float(settings.distribution_strength) <= 0.0:
        return []
    counts = {
        (col, row): 0
        for row in range(max(1, int(settings.grid_rows)))
        for col in range(max(1, int(settings.grid_columns)))
    }
    for x, y in points:
        cell = cell_for_point(x, y, width, height, settings)
        counts[cell] = counts.get(cell, 0) + 1
    return [cell for cell, count in counts.items() if count < minimum]


def _cell_deficit(
    underfilled_cells: list[tuple[int, int]],
    points: list[tuple[float, float]],
    width: int,
    height: int,
    props,
) -> int:
    if not underfilled_cells:
        return 0
    settings = _distribution_settings(props)
    minimum = _effective_minimum_tracks_per_cell(props)
    counts = {cell: 0 for cell in underfilled_cells}
    for x, y in points:
        cell = cell_for_point(x, y, width, height, settings)
        if cell in counts:
            counts[cell] += 1
    return sum(max(0, minimum - count) for count in counts.values())


def _add_lead_edge_cells(
    cells: list[tuple[int, int]],
    motion_vector: tuple[float, float],
    width: int,
    height: int,
    props,
) -> list[tuple[int, int]]:
    if not bool(getattr(props, "lead_edge_redetect", True)):
        return cells
    settings = _distribution_settings(props)
    if float(settings.distribution_strength) <= 0.0:
        return cells
    vx, vy = motion_vector
    threshold = max(1.25, min(max(1, int(width)), max(1, int(height))) * 0.002)
    if abs(float(vx)) < threshold and abs(float(vy)) < threshold:
        return cells
    cols = max(1, int(settings.grid_columns))
    rows = max(1, int(settings.grid_rows))
    added = list(cells)
    seen = set(added)

    def add_cell(cell):
        if cell not in seen:
            seen.add(cell)
            added.append(cell)

    if abs(float(vx)) >= threshold:
        col = cols - 1 if float(vx) < 0.0 else 0
        for row in range(rows):
            add_cell((col, row))
    if abs(float(vy)) >= threshold:
        row = rows - 1 if float(vy) < 0.0 else 0
        for col in range(cols):
            add_cell((col, row))
    return added


def _median_motion_vector(steps: list[tuple[float, float]]) -> tuple[float, float]:
    if not steps:
        return 0.0, 0.0
    import statistics

    return (
        float(statistics.median(item[0] for item in steps)),
        float(statistics.median(item[1] for item in steps)),
    )


def _limit_mask_to_cells(mask, cells: list[tuple[int, int]], width: int, height: int, props, np):
    if not cells:
        return mask
    settings = _distribution_settings(props)
    cols = max(1, int(settings.grid_columns))
    rows = max(1, int(settings.grid_rows))
    limited = np.zeros_like(mask)
    for col, row in cells:
        x0 = int((max(0, min(cols - 1, col)) / cols) * width)
        x1 = int(((max(0, min(cols - 1, col)) + 1) / cols) * width)
        y0 = int((max(0, min(rows - 1, row)) / rows) * height)
        y1 = int(((max(0, min(rows - 1, row)) + 1) / rows) * height)
        limited[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    return limited


def _detection_frames(frames: list[int], props, current_frame: int | None = None) -> list[int]:
    if not frames:
        return []
    start = frames[0]
    end = frames[-1]
    current = _clamp_frame(current_frame, start, end) if current_frame is not None else frames[len(frames) // 2]
    direction = props.tracking_direction
    if direction == "BACKWARD":
        first = end
    elif direction == "BOTH":
        first = frames[len(frames) // 2]
    elif direction == "CURRENT":
        first = current
    else:
        first = start
    if direction == "BACKWARD" or not props.enable_redetect:
        return [first]
    if direction in {"BOTH", "CURRENT"}:
        return _unique_frames(
            [first] + _forward_supplemental_anchors(first, end, max(1, int(props.redetect_interval))),
            start,
            end,
        )
    interval = max(1, int(props.redetect_interval))
    values = [first]
    if direction == "BACKWARD":
        value = first
        while value >= start:
            if value not in values:
                values.append(value)
            value -= interval
        return values
    if direction in {"BOTH", "CURRENT"}:
        offset = interval
        while first - offset >= start or first + offset <= end:
            if first - offset >= start:
                values.append(first - offset)
            if first + offset <= end:
                values.append(first + offset)
            offset += interval
        return values
    value = first
    while value <= end:
        if value not in values:
            values.append(value)
        value += interval
    return values


def _current_frame_in_range(context, clip, frames: list[int]) -> int:
    if not frames:
        return 1
    scene_frame = int(getattr(context.scene, "frame_current", 1))
    clip_start = int(getattr(clip, "frame_start", 1))
    current = scene_frame - clip_start + 1
    return _clamp_frame(current, min(frames), max(frames))


def _is_periodic_detection_frame(frame: int, first_frame: int, props) -> bool:
    interval = max(1, int(getattr(props, "redetect_interval", 15)))
    return abs(int(frame) - int(first_frame)) % interval == 0


def _clamp_frame(frame, start: int, end: int) -> int:
    return max(int(start), min(int(end), int(frame)))


def _forward_supplemental_anchors(first: int, end: int, interval: int) -> list[int]:
    span = int(end) - int(first)
    if span < max(12, int(interval)):
        return []
    return [
        int(first) + max(int(interval), int(span * 0.5)),
        int(first) + max(int(interval) * 2, int(span * 0.75)),
    ]


def _unique_frames(values: list[int], start: int, end: int) -> list[int]:
    frames = []
    seen = set()
    for value in values:
        frame = _clamp_frame(value, start, end)
        if frame not in seen:
            frames.append(frame)
            seen.add(frame)
    return frames


def _delete_autotrack_tracks(context, clip) -> int:
    delete_count = 0
    for track in target_tracks(clip):
        should_delete = is_autotrack_track(track)
        track.select = should_delete
        track.select_anchor = should_delete
        track.select_pattern = should_delete
        track.select_search = should_delete
        if should_delete:
            delete_count += 1
    if delete_count <= 0:
        return 0
    override = _clip_editor_override(context, clip)
    if override is None:
        mute_autotrack_tracks(clip)
        return 0
    area, region, space, previous_clip = override
    try:
        with context.temp_override(area=area, region=region, space_data=space):
            result = bpy.ops.clip.delete_track(confirm=False)
    except RuntimeError:
        mute_autotrack_tracks(clip)
        return 0
    finally:
        space.clip = previous_clip
    if "FINISHED" not in result:
        mute_autotrack_tracks(clip)
        return 0
    return delete_count


def _clip_editor_override(context, clip):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "CLIP_EDITOR":
            continue
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        space = next((item for item in area.spaces if item.type == "CLIP_EDITOR"), None)
        if region is None or space is None:
            continue
        previous_clip = space.clip
        space.clip = clip
        return area, region, space, previous_clip
    return None




def _active_points(candidates: list[TrackCandidate], frame: int) -> list[tuple[float, float]]:
    points = []
    for candidate in candidates:
        if candidate.disabled:
            continue
        for sample in candidate.valid_samples:
            if sample.frame == frame:
                points.append((sample.x, sample.y))
                break
    return points


def _detection_settings(props, provider=None) -> DetectionSettings:
    scale = _pixel_parameter_scale(props, provider)
    return DetectionSettings(
        detector_type=str(getattr(props, "detector_type", "SHI_TOMASI")),
        maximum_features=int(props.maximum_features),
        quality_level=float(props.quality_level),
        minimum_distance=_scale_float(props.minimum_distance, scale),
        block_size=_scale_odd_int(props.block_size, scale, minimum=3),
        use_harris_detector=bool(props.use_harris_detector),
        harris_k=float(props.harris_k),
        edge_margin=_scale_int(props.edge_margin, scale),
        grid_columns=int(props.grid_columns),
        grid_rows=int(props.grid_rows),
        max_per_cell=int(props.maximum_tracks_per_cell),
    )


def _lk_settings(props, provider=None) -> LKSettings:
    scale = _pixel_parameter_scale(props, provider)
    return LKSettings(
        window_size=_scale_odd_int(props.window_size, scale, minimum=3),
        pyramid_levels=int(props.pyramid_levels),
        termination_count=int(props.termination_count),
        termination_epsilon=float(props.termination_epsilon),
        minimum_eigen_threshold=float(props.minimum_eigen_threshold),
        maximum_lk_error=float(props.maximum_lk_error),
        maximum_fb_error=_scale_float(props.maximum_fb_error, scale),
        maximum_motion=_scale_float(props.maximum_motion, scale),
        edge_margin=_scale_int(props.edge_margin, scale),
        enable_forward_backward=bool(props.enable_forward_backward),
    )


def _filtering_settings(props, provider=None) -> FilteringSettings:
    scale = _pixel_parameter_scale(props, provider)
    return FilteringSettings(
        minimum_track_length=int(props.minimum_track_length),
        preferred_track_length=int(props.preferred_track_length),
        minimum_valid_ratio=float(props.minimum_valid_ratio),
        enable_ransac=bool(props.enable_ransac),
        ransac_model=str(props.ransac_model),
        ransac_threshold=_scale_float(props.ransac_threshold, scale),
        ransac_confidence=float(props.ransac_confidence),
        ransac_minimum_points=int(props.ransac_minimum_points),
        duplicate_distance=_scale_float(props.duplicate_distance, scale),
    )


def _pixel_parameter_scale(props, provider=None) -> float:
    if not bool(getattr(props, "auto_scale_pixel_parameters", True)):
        return 1.0
    if provider is None:
        return 1.0
    analysis_width = int(getattr(provider, "analysis_width", 0))
    if analysis_width <= 0:
        return 1.0
    return max(0.05, float(analysis_width) / float(REFERENCE_ANALYSIS_WIDTH))


def _scale_float(value, scale: float) -> float:
    return float(value) * float(scale)


def _scale_int(value, scale: float, minimum: int = 0) -> int:
    return max(int(minimum), int(round(float(value) * float(scale))))


def _scale_odd_int(value, scale: float, minimum: int = 3) -> int:
    result = _scale_int(value, scale, minimum=minimum)
    if result % 2 == 0:
        result += 1
    return result


def _distribution_settings(props) -> DistributionSettings:
    return DistributionSettings(
        grid_columns=int(props.grid_columns),
        grid_rows=int(props.grid_rows),
        maximum_tracks_per_cell=int(props.maximum_tracks_per_cell),
        minimum_tracks_per_cell=int(props.minimum_tracks_per_cell),
        distribution_strength=float(props.distribution_strength),
    )


def _distribution_temporal_bucket(props) -> int:
    if not bool(getattr(props, "enable_redetect", True)):
        return 0
    return max(1, int(getattr(props, "redetect_interval", 15)) * 4)


def _fill_length_stats(stats: TrackingStats, candidates: list[TrackCandidate]) -> None:
    import statistics

    lengths = [candidate.length for candidate in candidates]
    if lengths:
        stats.average_track_length = sum(lengths) / len(lengths)
        stats.median_track_length = float(statistics.median(lengths))
    fb_values = [
        sample.fb_error
        for candidate in candidates
        for sample in candidate.valid_samples
        if sample.fb_error is not None
    ]
    if fb_values:
        stats.average_fb_error = sum(fb_values) / len(fb_values)


def _sort_candidates_for_bake(candidates: list[TrackCandidate]) -> None:
    candidates.sort(
        key=lambda candidate: (
            bool(candidate.disabled),
            -candidate.length,
            -float(candidate.quality_score),
            int(candidate.detection_frame),
            int(candidate.id),
        )
    )


def _quick_ransac_rate(candidates: list[TrackCandidate], settings: FilteringSettings) -> float:
    pairs_a = []
    pairs_b = []
    for candidate in candidates:
        samples = candidate.valid_samples
        if len(samples) >= 2:
            pairs_a.append((samples[0].x, samples[0].y))
            pairs_b.append((samples[-1].x, samples[-1].y))
    return ransac_inlier_rate_for_pair(pairs_a, pairs_b, settings)
