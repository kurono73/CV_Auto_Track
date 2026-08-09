from __future__ import annotations

import traceback
import time

import bl_operators
import bpy
from bpy.props import StringProperty

from . import blender_tracks
from .dependencies import dependency_status
from .frame_provider import OpenCVUnsupportedMediaError
from .properties import USER_DEFAULT_PROPERTY_NAMES
from .scene_setup import ensure_scene_setup
from .solve_refinement import analyze_solve, choose_outliers, refine_solve, solve_camera
from .solve_keyframes import (
    apply_keyframes,
    choose_keyframes_from_candidates,
    choose_keyframes_from_clip,
    disable_keyframe_selection,
)
from .tracking_engine import DetectTrackSession, add_cached_candidates, run_detect_track
from .tracking_types import TrackingStats


def active_clip(context):
    space = getattr(context, "space_data", None)
    if space and space.type == "CLIP_EDITOR" and space.clip:
        return space.clip
    if space is not None:
        return None
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) or getattr(context, "screen", None)
    if screen is not None:
        clips = []
        for area in screen.areas:
            if area.type != "CLIP_EDITOR":
                continue
            for area_space in area.spaces:
                if area_space.type == "CLIP_EDITOR" and area_space.clip:
                    clips.append(area_space.clip)
        unique = {getattr(clip, "session_uid", None) or id(clip): clip for clip in clips}
        if len(unique) == 1:
            return next(iter(unique.values()))
    return None


def _store_stats(props, stats: TrackingStats) -> None:
    lines = stats.as_lines()
    props.results_text = "\n".join(lines)
    props.status_message = _status_from_stats(stats)
    for line in lines:
        print(f"[CV Auto Track] {line}")


def _status_from_stats(stats: TrackingStats) -> str:
    elapsed = max(0.0, float(getattr(stats, "processing_time", 0.0)))
    state = str(getattr(stats, "cancellation_state", "") or "Completed")
    if state.startswith("Cancelled"):
        return f"Cancelled after {elapsed:.2f}s"
    if state.startswith("Error"):
        return state
    if state != "Completed" and int(getattr(stats, "generated_tracks", 0)) <= 0 and int(getattr(stats, "valid_tracks", 0)) <= 0:
        return state
    track_text = f", {int(stats.valid_tracks)} tracks" if int(getattr(stats, "valid_tracks", 0)) > 0 else ""
    return f"Completed in {elapsed:.2f}s{track_text}"


def _flush_clip_tracking(context, clip) -> None:
    try:
        clip.update_tag()
    except (AttributeError, RuntimeError) as exc:
        print(f"[CV Auto Track] Clip update skipped: {exc}")


def _clip_editor_spaces(context, clip):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) or getattr(context, "screen", None)
    if screen is None:
        return []
    current_area = getattr(context, "area", None)
    areas = []
    if current_area is not None:
        areas.append(current_area)
    areas.extend(area for area in screen.areas if area is not current_area)
    result = []
    for area in areas:
        if area.type != "CLIP_EDITOR":
            continue
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        active_space = getattr(area.spaces, "active", None)
        space = active_space if active_space and active_space.type == "CLIP_EDITOR" else None
        if space is None:
            space = next((item for item in area.spaces if item.type == "CLIP_EDITOR"), None)
        if region is not None and space is not None and (space.clip is None or space.clip == clip):
            result.append((area, region, space))
    return result


def _pin_active_editor_mask(context, props) -> None:
    if not bool(getattr(props, "use_mask", False)):
        return
    if str(getattr(props, "mask_source", "BLENDER")) != "BLENDER":
        return
    if getattr(props, "tracking_mask", None) is not None:
        return
    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == "CLIP_EDITOR":
        mask = getattr(space, "mask", None)
        if mask is not None:
            props.tracking_mask = mask


def _ensure_tracking_mode(context, clip, props=None) -> bool:
    if props is not None:
        _pin_active_editor_mask(context, props)
    changed = False
    for _area, _region, space in _clip_editor_spaces(context, clip):
        if space.clip is None:
            space.clip = clip
        if getattr(space, "mode", "TRACKING") != "TRACKING":
            try:
                space.mode = "TRACKING"
                changed = True
            except TypeError as exc:
                raise RuntimeError("Switch the Movie Clip Editor from Mask mode to Tracking mode before running CV Auto Track.") from exc
    return changed


def _clip_editor_override(context, clip):
    spaces = _clip_editor_spaces(context, clip)
    if not spaces:
        raise RuntimeError("Movie Clip Editor area is required.")
    candidate_area, candidate_region, candidate_space = spaces[0]
    previous_clip = candidate_space.clip
    candidate_space.clip = clip
    if getattr(candidate_space, "mode", "TRACKING") != "TRACKING":
        candidate_space.mode = "TRACKING"
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) or getattr(context, "screen", None)
    override = {
        "area": candidate_area,
        "region": candidate_region,
        "space_data": candidate_space,
        "edit_movieclip": clip,
    }
    if window is not None:
        override["window"] = window
    if screen is not None:
        override["screen"] = screen
    return override, candidate_space, previous_clip


def _delete_selected_clip_tracks(context, clip):
    override, space, previous_clip = _clip_editor_override(context, clip)
    try:
        with context.temp_override(**override):
            if not bpy.ops.clip.delete_track.poll():
                raise RuntimeError("bpy.ops.clip.delete_track.poll() failed after CV Auto Track context override.")
            result = bpy.ops.clip.delete_track(confirm=False)
    finally:
        space.clip = previous_clip
    if "FINISHED" not in result:
        raise RuntimeError(f"bpy.ops.clip.delete_track returned {result}.")


def _start_proxy_build(context, clip) -> str:
    proxy = getattr(clip, "proxy", None)
    if proxy is None:
        raise RuntimeError("This Movie Clip does not expose Blender proxy settings.")
    clip.use_proxy = True
    proxy.build_25 = False
    proxy.build_50 = False
    proxy.build_75 = False
    proxy.build_100 = True
    proxy.quality = 100
    override, space, previous_clip = _clip_editor_override(context, clip)
    try:
        with context.temp_override(**override):
            if not bpy.ops.clip.rebuild_proxy.poll():
                raise RuntimeError("bpy.ops.clip.rebuild_proxy.poll() failed after CV Auto Track context override.")
            result = bpy.ops.clip.rebuild_proxy()
    finally:
        space.clip = previous_clip
    if "FINISHED" not in result:
        raise RuntimeError(f"bpy.ops.clip.rebuild_proxy returned {result}.")
    return "OpenCV cannot read this media. Blender 100% proxy build started; run CV Auto Track again after it finishes."


def _request_proxy_build(operator, context, clip, exc) -> set[str]:
    props = context.scene.cv_autotrack
    message = "OpenCV cannot read this media. Waiting for proxy build confirmation."
    props.status_message = message
    try:
        result = bpy.ops.clip.cv_autotrack_build_proxy_confirm("INVOKE_DEFAULT", clip_name=clip.name)
    except Exception as dialog_exc:
        traceback.print_exc()
        props.status_message = f"Error: {exc} Proxy confirmation could not be opened: {dialog_exc}"
        operator.report({"ERROR"}, props.status_message)
        return {"CANCELLED"}
    if "RUNNING_MODAL" not in result and "FINISHED" not in result:
        props.status_message = f"Error: {exc} Proxy confirmation could not be opened."
        operator.report({"ERROR"}, props.status_message)
        return {"CANCELLED"}
    operator.report({"WARNING"}, message)
    return {"CANCELLED"}


def _handle_unsupported_media(operator, context, clip, exc) -> set[str]:
    return _request_proxy_build(operator, context, clip, exc)


def _build_proxy_now(operator, context, clip) -> set[str]:
    props = context.scene.cv_autotrack
    try:
        message = _start_proxy_build(context, clip)
    except Exception as proxy_exc:
        traceback.print_exc()
        message = f"Proxy build could not be started: {proxy_exc}"
        props.status_message = f"Error: {message}"
        operator.report({"ERROR"}, message)
        return {"CANCELLED"}
    props.status_message = message
    operator.report({"INFO"}, message)
    return {"FINISHED"}


def _delete_tracks_by_name(context, clip, names, props=None) -> int:
    names = {str(name) for name in names}
    if not names:
        return 0
    delete_count = 0
    for track in blender_tracks.target_tracks(clip):
        should_delete = track.name in names
        if should_delete and props is not None:
            if bool(getattr(props, "protect_existing_tracks", True)) and not blender_tracks.is_autotrack_track(track):
                should_delete = False
            if bool(getattr(props, "protect_selected_tracks", True)) and bool(track.select):
                should_delete = False
            prefix = str(getattr(props, "protected_name_prefix", "") or "")
            if prefix and track.name.startswith(prefix):
                should_delete = False
        track.select = should_delete
        track.select_anchor = should_delete
        track.select_pattern = should_delete
        track.select_search = should_delete
        if should_delete:
            delete_count += 1
    if delete_count:
        _delete_selected_clip_tracks(context, clip)
    return delete_count


def _apply_solve_keyframes_from_clip(clip, props) -> None:
    if bool(props.auto_solve_keyframes):
        apply_keyframes(clip, choose_keyframes_from_clip(clip, 1, max(1, int(getattr(clip, "frame_duration", 1)))))
    else:
        disable_keyframe_selection(clip)


def _maybe_auto_scene_setup(operator, context, clip, props) -> None:
    if not bool(getattr(props, "auto_scene_setup", True)):
        return
    try:
        ensure_scene_setup(context, clip)
    except Exception as exc:
        traceback.print_exc()
        operator.report({"WARNING"}, f"Auto Scene Setup failed: {exc}")


def _refine_solve_succeeded(result) -> bool:
    message = str(getattr(result, "message", "") or "").lower()
    return bool(message) and "failed" not in message and "returned" not in message and "cancelled" not in message


def _run_safely(operator, context, func):
    props = context.scene.cv_autotrack
    props.is_running = True
    props.cancel_requested = False
    props.status_message = "Running"
    clip = None
    try:
        ok, message = dependency_status()
        if not ok:
            raise RuntimeError(f"OpenCV dependency import failed: {message}")
        clip = active_clip(context)
        if clip is None:
            raise RuntimeError("Movie Clip is not selected.")
        if not clip.filepath:
            raise RuntimeError("Selected Movie Clip has no filepath.")
        _ensure_tracking_mode(context, clip, props)
        return func(clip, props)
    except OpenCVUnsupportedMediaError as exc:
        traceback.print_exc()
        if clip is not None:
            return _handle_unsupported_media(operator, context, clip, exc)
        props.status_message = f"Error: {exc}"
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    except Exception as exc:
        traceback.print_exc()
        props.status_message = f"Error: {exc}"
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    finally:
        props.is_running = False
        if not props.status_message.startswith("Error") and props.status_message in {"Running", "Starting"}:
            props.status_message = "Idle"


class CV_AUTOTRACK_OT_detect_track(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_detect_track"
    bl_label = "Detect & Track"
    bl_description = "Detect features with OpenCV, track them, clean candidates, and bake Blender Movie Tracking tracks"
    bl_options = {"REGISTER", "UNDO"}
    _timer = None
    _session = None
    _last_ui_update = 0.0

    def invoke(self, context, _event):
        props = context.scene.cv_autotrack
        if props.tracking_direction not in {"FORWARD", "AUTO"}:
            return self.execute(context)
        try:
            ok, message = dependency_status()
            if not ok:
                raise RuntimeError(f"OpenCV dependency import failed: {message}")
            clip = active_clip(context)
            if clip is None:
                raise RuntimeError("Movie Clip is not selected in the Clip Editor.")
            if not clip.filepath:
                raise RuntimeError("Selected Movie Clip has no filepath.")
            _ensure_tracking_mode(context, clip, props)
            props.is_running = True
            props.cancel_requested = False
            props.status_message = "Starting"
            self._session = DetectTrackSession(context, clip, props)
            wm = context.window_manager
            wm.progress_begin(0, 100)
            self._timer = wm.event_timer_add(0.01, window=context.window)
            wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except OpenCVUnsupportedMediaError as exc:
            traceback.print_exc()
            clip = self._session.clip if self._session is not None else active_clip(context)
            if clip is not None:
                _handle_unsupported_media(self, context, clip, exc)
            else:
                props.status_message = f"Error: {exc}"
                self.report({"ERROR"}, str(exc))
            return self._finish_modal(context, cancelled=True, close_session=True, keep_status=True)
        except Exception as exc:
            traceback.print_exc()
            props.status_message = f"Error: {exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        props = context.scene.cv_autotrack
        if event.type in {"ESC"} or props.cancel_requested:
            return self._finish_modal(context, cancelled=True)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            deadline = time.perf_counter() + 0.08
            message = props.status_message
            while self._session and not self._session.done and time.perf_counter() < deadline:
                message = self._session.step()
            if self._session:
                context.window_manager.progress_update(self._session.progress * 100.0)
                if time.perf_counter() - self._last_ui_update > 0.1:
                    props.status_message = message
                    self._last_ui_update = time.perf_counter()
                if self._session.done:
                    _, stats = self._session.finish()
                    _store_stats(props, stats)
                    self.report({"INFO"}, f"CV Auto Track generated {stats.generated_tracks} tracks.")
                    return self._finish_modal(context, cancelled=False, close_session=False)
        except OpenCVUnsupportedMediaError as exc:
            traceback.print_exc()
            clip = self._session.clip if self._session is not None else active_clip(context)
            if clip is not None:
                _handle_unsupported_media(self, context, clip, exc)
            else:
                props.status_message = f"Error: {exc}"
                self.report({"ERROR"}, str(exc))
            return self._finish_modal(context, cancelled=True, close_session=True, keep_status=True)
        except Exception as exc:
            traceback.print_exc()
            props.status_message = f"Error: {exc}"
            self.report({"ERROR"}, str(exc))
            return self._finish_modal(context, cancelled=True, close_session=True)
        return {"RUNNING_MODAL"}

    def _finish_modal(self, context, cancelled=False, close_session=True, keep_status=False):
        props = context.scene.cv_autotrack
        if cancelled and self._session and close_session and not keep_status and not props.status_message.startswith("Error"):
            stats = self._session.cancel()
            _store_stats(props, stats)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()
        props.is_running = False
        if not keep_status and not props.status_message.startswith("Error") and props.status_message in {"Running", "Starting"}:
            props.status_message = "Cancelled" if cancelled else "Idle"
        self._session = None
        return {"CANCELLED"} if cancelled else {"FINISHED"}

    def execute(self, context):
        def work(clip, props):
            wm = context.window_manager
            wm.progress_begin(0, 100)

            def progress(index, total, message):
                value = 100.0 if total <= 0 else min(100.0, (index / max(1, total)) * 100.0)
                wm.progress_update(value)
                props.status_message = message

            try:
                _, stats = run_detect_track(
                    context,
                    clip,
                    props,
                    cancel_cb=lambda: bool(props.cancel_requested),
                    progress_cb=progress,
                )
            finally:
                wm.progress_end()
            _store_stats(props, stats)
            self.report({"INFO"}, f"CV Auto Track generated {stats.generated_tracks} tracks.")
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_clean_tracks(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_clean_tracks"
    bl_label = "Clean Tracks"
    bl_description = "Mute short or high-error tracks, and delete empty CV Auto Track tracks"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        def work(clip, props):
            disabled = 0
            delete_empty = 0
            for track in blender_tracks.target_tracks(clip):
                track.select = False
                track.select_anchor = False
                track.select_pattern = False
                track.select_search = False
            for track in blender_tracks.target_tracks(clip):
                if props.preserve_existing_tracks and not blender_tracks.is_autotrack_track(track):
                    continue
                unmuted = [marker for marker in track.markers if not marker.mute]
                error = _track_error(track)
                should_clean = len(unmuted) < props.minimum_track_length or (error is not None and error > props.maximum_track_error)
                if should_clean and len(unmuted) == 0 and blender_tracks.is_autotrack_track(track):
                    track.select = True
                    track.select_anchor = True
                    track.select_pattern = True
                    track.select_search = True
                    delete_empty += 1
                elif should_clean:
                    for marker in track.markers:
                        marker.mute = True
                    disabled += 1
            if delete_empty:
                _delete_selected_clip_tracks(context, clip)
            stats = TrackingStats(disabled_tracks=disabled, cancellation_state="Completed")
            stats.deleted_tracks = delete_empty
            stats.valid_tracks = _enabled_track_count(clip)
            _store_stats(props, stats)
            self.report({"INFO"}, f"Disabled {disabled} tracks.")
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_analyze_solve(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_analyze_solve"
    bl_label = "Analyze Solve"
    bl_description = "Analyze track bundle errors from the current solve"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        def work(clip, props):
            outliers = choose_outliers(clip, props)
            for track in blender_tracks.target_tracks(clip):
                track.select = False
            if props.analyze_solve_action == "SELECT":
                for track in outliers:
                    track.select = True
            elif props.analyze_solve_action == "DISABLE":
                blender_tracks.disable_tracks(outliers)
            all_errors = analyze_solve(clip, props)
            stats = TrackingStats(
                valid_tracks=_enabled_track_count(clip),
                disabled_tracks=len(outliers) if props.analyze_solve_action == "DISABLE" else 0,
                solve_error_before=_solve_error(clip),
                cancellation_state="Completed",
            )
            props.results_text = "\n".join(
                stats.as_lines()
                + [f"Analyzed Tracks: {len(all_errors)}", f"Outlier Candidates: {len(outliers)}"]
            )
            print(f"[CV Auto Track] Analyze Solve: {len(outliers)} outliers from {len(all_errors)} tracks")
            self.report({"INFO"}, f"Found {len(outliers)} solve outliers.")
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_solve_camera(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_solve_camera"
    bl_label = "Solve"
    bl_description = "Run Blender's standard camera solve using the active Movie Clip"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        def work(clip, props):
            started = time.perf_counter()
            _apply_solve_keyframes_from_clip(clip, props)
            ok, message = solve_camera(context, clip, reset_radial=True)
            if not ok:
                raise RuntimeError(message)
            _maybe_auto_scene_setup(self, context, clip, props)
            stats = TrackingStats(
                valid_tracks=_enabled_track_count(clip),
                solve_error_after=_solve_error(clip),
                processing_time=time.perf_counter() - started,
                cancellation_state=message,
            )
            _store_stats(props, stats)
            self.report({"INFO"}, message)
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_solve_refine(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_solve_refine"
    bl_label = "Solve & Refine"
    bl_description = "Run Blender camera solve and progressively disable high-error tracks"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        def work(clip, props):
            started = time.perf_counter()
            _apply_solve_keyframes_from_clip(clip, props)
            result = refine_solve(context, clip, props, cancel_cb=lambda: bool(props.cancel_requested))
            deleted = 0
            if bool(props.delete_refined_tracks):
                deleted = _delete_tracks_by_name(context, clip, result.disabled_track_names, props)
            if _refine_solve_succeeded(result):
                _maybe_auto_scene_setup(self, context, clip, props)
            stats = TrackingStats(
                valid_tracks=_enabled_track_count(clip),
                disabled_tracks=0 if deleted else result.disabled_tracks,
                deleted_tracks=deleted,
                solve_error_before=result.solve_error_before,
                solve_error_after=result.solve_error_after,
                refine_iterations=result.iterations,
                processing_time=time.perf_counter() - started,
                cancellation_state=result.message,
            )
            _store_stats(props, stats)
            self.report({"INFO"}, result.message)
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_solve_setup_dialog(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_solve_setup_dialog"
    bl_label = "Solve Setup"
    bl_description = "Open CV Auto Track and Blender camera solve settings together"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=280)

    def draw(self, context):
        clip = active_clip(context)
        props = context.scene.cv_autotrack
        layout = self.layout
        if clip is None:
            layout.label(text="No active Movie Clip.", icon="ERROR")
            return
        settings = clip.tracking.settings
        camera = clip.tracking.camera
        active_object = getattr(clip.tracking.objects, "active", None)
        box = layout.box()
        box.label(text="Keyframes", icon="KEYFRAME")
        box.prop(props, "auto_solve_keyframes")
        if active_object is not None:
            row = box.row(align=True)
            row.prop(active_object, "keyframe_a", text="A")
            row.prop(active_object, "keyframe_b", text="B")
        box = layout.box()
        box.label(text="Solve", icon="TRACKING", translate=False)
        box.prop(settings, "use_tripod_solver", text="Tripod Motion")
        row = box.row(align=True)
        row.prop(settings, "refine_intrinsics_focal_length", text="Focal Length")
        row.prop(settings, "refine_intrinsics_principal_point", text="Optical Center")
        row = box.row(align=True)
        row.prop(settings, "refine_intrinsics_radial_distortion", text="Radial Distortion")
        row.prop(settings, "refine_intrinsics_tangential_distortion", text="Tangential Distortion")
        row = box.row(align=True)
        row.prop(props, "auto_scene_setup", text="Scene Setup")
        row.prop(props, "full_auto_refine_iterations", text="Refine Passes")
        box = layout.box()
        box.label(text="Camera", icon="CAMERA_DATA")
        row = box.row(align=True)
        row.prop(camera, "sensor_width", text="Sensor")
        row.prop(camera, "focal_length", text="Focal")
        box.prop(camera, "distortion_model")
        box = layout.box()
        box.label(text="Bake Marker Size", icon="TRACKER")
        row = box.row(align=True)
        row.prop(props, "bake_pattern_size", text="Pattern")
        row.prop(props, "bake_search_size", text="Search")

    def execute(self, _context):
        return {"FINISHED"}


class CV_AUTOTRACK_OT_build_proxy_confirm(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_build_proxy_confirm"
    bl_label = "Build 100% Proxy"
    bl_description = "Build a Blender 100% proxy when OpenCV cannot read the active footage"
    bl_options = {"REGISTER", "UNDO"}

    clip_name: StringProperty(name="Movie Clip")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        layout = self.layout
        layout.label(text="OpenCV cannot read this media.", icon="ERROR")
        layout.label(text="Build a Blender 100% proxy now?")
        layout.label(text="Run CV Auto Track again after the proxy finishes.")
        if self.clip_name:
            layout.separator()
            layout.label(text=f"Clip: {self.clip_name}")

    def execute(self, context):
        clip = bpy.data.movieclips.get(self.clip_name) if self.clip_name else active_clip(context)
        if clip is None:
            self.report({"ERROR"}, "Movie Clip is not selected.")
            context.scene.cv_autotrack.status_message = "Error: Movie Clip is not selected."
            return {"CANCELLED"}
        return _build_proxy_now(self, context, clip)

    def cancel(self, context):
        message = "Proxy build cancelled."
        context.scene.cv_autotrack.status_message = message
        self.report({"INFO"}, message)


class CV_AUTOTRACK_OT_load_external_mask_clip(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_load_external_mask_clip"
    bl_label = "Load Mask Clip"
    bl_description = "Load an external mask as a Blender Movie Clip"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(name="File Path", subtype="FILE_PATH")
    filter_glob: StringProperty(
        default="*.avi;*.mov;*.mp4;*.mkv;*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.dpx",
        options={"HIDDEN"},
    )

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            self.report({"ERROR"}, "Mask clip filepath is empty.")
            return {"CANCELLED"}
        props = context.scene.cv_autotrack
        try:
            try:
                mask_clip = bpy.data.movieclips.load(self.filepath, check_existing=True)
            except TypeError:
                mask_clip = bpy.data.movieclips.load(self.filepath)
            props.mask_source = "EXTERNAL"
            props.external_mask_clip = mask_clip
            active = active_clip(context)
            if active is not None:
                _sync_mask_clip_footage_settings(active, mask_clip)
            self.report({"INFO"}, f"Loaded mask clip: {mask_clip.name}")
            return {"FINISHED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class CV_AUTOTRACK_OT_sync_external_mask_clip(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_sync_external_mask_clip"
    bl_label = "Sync Mask Clip"
    bl_description = "Sync mask clip footage settings from the active Movie Clip"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        active = active_clip(context)
        mask_clip = context.scene.cv_autotrack.external_mask_clip
        if active is None:
            self.report({"ERROR"}, "Movie Clip is not selected.")
            return {"CANCELLED"}
        if mask_clip is None:
            self.report({"ERROR"}, "Mask Clip is not selected.")
            return {"CANCELLED"}
        _sync_mask_clip_footage_settings(active, mask_clip)
        message = "Synced mask clip start and offset."
        active_duration = int(getattr(active, "frame_duration", 0))
        mask_duration = int(getattr(mask_clip, "frame_duration", 0))
        if active_duration and mask_duration and active_duration != mask_duration:
            message += f" Length differs: {mask_duration} / {active_duration}."
            self.report({"WARNING"}, message)
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class CV_AUTOTRACK_OT_full_auto_track(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_full_auto_track"
    bl_label = "Full Auto Track"
    bl_description = "Run detect, clean, bake, and optional solve refinement"
    bl_options = {"REGISTER", "UNDO"}
    _timer = None
    _session = None
    _started = 0.0
    _last_ui_update = 0.0

    def invoke(self, context, _event):
        props = context.scene.cv_autotrack
        try:
            ok, message = dependency_status()
            if not ok:
                raise RuntimeError(f"OpenCV dependency import failed: {message}")
            clip = active_clip(context)
            if clip is None:
                raise RuntimeError("Movie Clip is not selected in the Clip Editor.")
            if not clip.filepath:
                raise RuntimeError("Selected Movie Clip has no filepath.")
            _ensure_tracking_mode(context, clip, props)
            use_existing_autotrack = _full_auto_reuses_existing_autotrack(props) and _autotrack_track_count(clip) > 0
            if use_existing_autotrack or props.tracking_direction not in {"FORWARD", "AUTO"}:
                return self.execute(context)
            props.is_running = True
            props.cancel_requested = False
            props.status_message = "Starting"
            self._started = time.perf_counter()
            self._session = DetectTrackSession(context, clip, props)
            wm = context.window_manager
            wm.progress_begin(0, 100)
            self._timer = wm.event_timer_add(0.01, window=context.window)
            wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except OpenCVUnsupportedMediaError as exc:
            traceback.print_exc()
            clip = self._session.clip if self._session is not None else active_clip(context)
            if clip is not None:
                _handle_unsupported_media(self, context, clip, exc)
            else:
                props.status_message = f"Error: {exc}"
                self.report({"ERROR"}, str(exc))
            return self._finish_modal(context, cancelled=True, close_session=True, keep_status=True)
        except Exception as exc:
            traceback.print_exc()
            props.status_message = f"Error: {exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        props = context.scene.cv_autotrack
        if event.type in {"ESC"} or props.cancel_requested:
            return self._finish_modal(context, cancelled=True)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            deadline = time.perf_counter() + 0.08
            message = props.status_message
            while self._session and not self._session.done and time.perf_counter() < deadline:
                message = self._session.step()
            if self._session:
                context.window_manager.progress_update(self._session.progress * 75.0)
                if time.perf_counter() - self._last_ui_update > 0.1:
                    props.status_message = message
                    self._last_ui_update = time.perf_counter()
                if self._session.done:
                    clip = self._session.clip
                    candidates, stats = self._session.finish()
                    self._remove_timer(context)

                    def solve_progress(index, total, solve_message):
                        props.status_message = solve_message
                        context.window_manager.progress_update(75.0 + ((index / max(1, total)) * 25.0))

                    stats = self._complete_after_detect(
                        context,
                        clip,
                        props,
                        candidates,
                        stats,
                        use_existing_autotrack=False,
                        started=self._started,
                        solve_progress=solve_progress,
                    )
                    _store_stats(props, stats)
                    self.report({"INFO"}, "Full Auto Track finished.")
                    return self._finish_modal(context, cancelled=False, close_session=False, keep_status=True)
        except OpenCVUnsupportedMediaError as exc:
            traceback.print_exc()
            clip = self._session.clip if self._session is not None else active_clip(context)
            if clip is not None:
                _handle_unsupported_media(self, context, clip, exc)
            else:
                props.status_message = f"Error: {exc}"
                self.report({"ERROR"}, str(exc))
            return self._finish_modal(context, cancelled=True, close_session=True, keep_status=True)
        except Exception as exc:
            traceback.print_exc()
            props.status_message = f"Error: {exc}"
            self.report({"ERROR"}, str(exc))
            return self._finish_modal(context, cancelled=True, close_session=True, keep_status=True)
        return {"RUNNING_MODAL"}

    def _remove_timer(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _finish_modal(self, context, cancelled=False, close_session=True, keep_status=False):
        props = context.scene.cv_autotrack
        if cancelled and self._session and close_session and not keep_status and not props.status_message.startswith("Error"):
            stats = self._session.cancel()
            _store_stats(props, stats)
        self._remove_timer(context)
        context.window_manager.progress_end()
        props.is_running = False
        if not keep_status and not props.status_message.startswith("Error") and props.status_message in {"Running", "Starting"}:
            props.status_message = "Cancelled" if cancelled else "Idle"
        self._session = None
        return {"CANCELLED"} if cancelled else {"FINISHED"}

    def _complete_after_detect(
        self,
        context,
        clip,
        props,
        candidates,
        stats,
        use_existing_autotrack: bool,
        started: float,
        solve_progress,
    ) -> TrackingStats:
        if props.auto_solve_refine and not props.cancel_requested:
            _flush_clip_tracking(context, clip)
            if bool(props.auto_solve_keyframes):
                props.status_message = "Choosing keyframes"
                duration = max(1, int(getattr(clip, "frame_duration", 1)))
                keyframes = (
                    choose_keyframes_from_clip(clip, 1, duration)
                    if use_existing_autotrack
                    else choose_keyframes_from_candidates(candidates, 1, duration)
                )
                apply_keyframes(clip, keyframes)
            else:
                disable_keyframe_selection(clip)
            refine = refine_solve(
                context,
                clip,
                props,
                cancel_cb=lambda: bool(props.cancel_requested),
                progress_cb=solve_progress,
                max_iterations=int(props.full_auto_refine_iterations),
            )
            deleted = 0
            if bool(props.delete_refined_tracks):
                deleted = _delete_tracks_by_name(context, clip, refine.disabled_track_names, props)
            if _refine_solve_succeeded(refine):
                _maybe_auto_scene_setup(self, context, clip, props)
            stats.solve_error_before = refine.solve_error_before
            stats.solve_error_after = refine.solve_error_after
            stats.refine_iterations = refine.iterations
            stats.disabled_tracks += 0 if deleted else refine.disabled_tracks
            stats.deleted_tracks += deleted
            stats.valid_tracks = _enabled_track_count(clip)
            stats.cancellation_state = refine.message
        stats.processing_time = time.perf_counter() - started
        return stats

    def execute(self, context):
        def work(clip, props):
            started = time.perf_counter()
            wm = context.window_manager
            wm.progress_begin(0, 100)

            def progress(index, total, message):
                props.status_message = message
                wm.progress_update((index / max(1, total)) * 75.0)

            def solve_progress(index, total, message):
                props.status_message = message
                wm.progress_update(75.0 + ((index / max(1, total)) * 25.0))

            try:
                use_existing_autotrack = _full_auto_reuses_existing_autotrack(props) and _autotrack_track_count(clip) > 0
                if use_existing_autotrack:
                    props.status_message = "Using existing CV Auto Track tracks"
                    wm.progress_update(75.0)
                    candidates = []
                    stats = TrackingStats(
                        generated_tracks=_autotrack_track_count(clip),
                        valid_tracks=_enabled_track_count(clip),
                        cancellation_state="Skipped detect; using existing CV Auto Track tracks",
                    )
                else:
                    candidates, stats = run_detect_track(
                        context,
                        clip,
                        props,
                        cancel_cb=lambda: bool(props.cancel_requested),
                        progress_cb=progress,
                    )
                if props.auto_solve_refine and not props.cancel_requested:
                    stats = self._complete_after_detect(
                        context,
                        clip,
                        props,
                        candidates,
                        stats,
                        use_existing_autotrack=use_existing_autotrack,
                        started=started,
                        solve_progress=solve_progress,
                    )
            finally:
                wm.progress_end()
            if not props.auto_solve_refine or props.cancel_requested:
                stats.processing_time = time.perf_counter() - started
            _store_stats(props, stats)
            self.report({"INFO"}, "Full Auto Track finished.")
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_cancel(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_cancel"
    bl_label = "Cancel"
    bl_description = "Request cancellation of the current CV Auto Track operation"

    def execute(self, context):
        context.scene.cv_autotrack.cancel_requested = True
        context.scene.cv_autotrack.status_message = "Cancel requested"
        return {"FINISHED"}


class CV_AUTOTRACK_OT_add_cached_tracks(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_add_cached_tracks"
    bl_label = "Add Tracks"
    bl_description = "Add more Blender tracks from the latest cached OpenCV tracking candidates"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        clip = active_clip(context)
        if clip is None:
            return False
        props = context.scene.cv_autotrack
        if bool(getattr(props, "is_running", False)):
            return False
        from .candidate_cache import has_candidate_cache

        return has_candidate_cache(context, clip, props)

    def execute(self, context):
        def work(clip, props):
            stats = add_cached_candidates(context, clip, props)
            _store_stats(props, stats)
            if stats.valid_tracks > 0:
                self.report({"INFO"}, f"Added {stats.valid_tracks} cached tracks.")
            else:
                self.report({"INFO"}, stats.cancellation_state or "No additional cached tracks are available.")
            return {"FINISHED"}

        return _run_safely(self, context, work)


class CV_AUTOTRACK_OT_toggle_auto_refine(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_toggle_auto_refine"
    bl_label = "Auto Refine"
    bl_description = "Toggle whether Full Auto Track runs solve refinement"

    def execute(self, context):
        props = context.scene.cv_autotrack
        props.auto_solve_refine = not bool(props.auto_solve_refine)
        return {"FINISHED"}


class CV_AUTOTRACK_OT_restore_previous_state(bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_restore_previous_state"
    bl_label = "Delete Auto Tracks"
    bl_description = "Delete CV Auto Track-created tracks from the active Movie Clip"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        clip = active_clip(context)
        if clip is None:
            self.report({"ERROR"}, "Movie Clip is not selected.")
            return {"CANCELLED"}
        try:
            _ensure_tracking_mode(context, clip, context.scene.cv_autotrack)
            delete_count = blender_tracks.select_autotrack_tracks_for_deletion(clip)
            if delete_count == 0:
                final_message = "No CV Auto Track tracks to delete."
                self.report({"INFO"}, final_message)
                context.scene.cv_autotrack.status_message = final_message
                return {"FINISHED"}
            _delete_selected_clip_tracks(context, clip)
            final_message = f"Deleted {delete_count} CV Auto Track tracks."
            self.report({"INFO"}, final_message)
            context.scene.cv_autotrack.status_message = final_message
            return {"FINISHED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, str(exc))
            context.scene.cv_autotrack.status_message = f"Error: {exc}"
            return {"CANCELLED"}


class CV_AUTOTRACK_OT_add_preset(bl_operators.presets.AddPresetBase, bpy.types.Operator):
    bl_idname = "clip.cv_autotrack_preset_add"
    bl_label = "Add CV Auto Track Preset"
    preset_menu = "CV_AUTOTRACK_MT_presets"
    preset_subdir = "cv_autotrack"
    preset_defines = ["props = bpy.context.scene.cv_autotrack"]
    preset_values = [f"props.{name}" for name in USER_DEFAULT_PROPERTY_NAMES]


def _track_error(track):
    for attr in ("average_error", "bundle"):
        try:
            value = float(getattr(track, attr))
        except Exception:
            continue
        if value >= 0:
            return value
    return None


def _enabled_track_count(clip) -> int:
    count = 0
    for track in blender_tracks.target_tracks(clip):
        markers = list(track.markers)
        if markers and not all(marker.mute for marker in markers):
            count += 1
    return count


def _autotrack_track_count(clip) -> int:
    return sum(1 for track in blender_tracks.target_tracks(clip) if blender_tracks.is_autotrack_track(track))


def _full_auto_reuses_existing_autotrack(props) -> bool:
    return str(getattr(props, "track_replace_mode", "AUTO_REUSE")) == "AUTO_REUSE"


def _solve_error(clip) -> float:
    try:
        return float(clip.tracking.reconstruction.average_error)
    except Exception:
        return -1.0


def _sync_mask_clip_footage_settings(source_clip, mask_clip) -> None:
    for attr in ("frame_start", "frame_offset"):
        if hasattr(source_clip, attr) and hasattr(mask_clip, attr):
            try:
                setattr(mask_clip, attr, getattr(source_clip, attr))
            except Exception:
                pass
    if hasattr(source_clip, "colorspace_settings") and hasattr(mask_clip, "colorspace_settings"):
        try:
            mask_clip.colorspace_settings.name = source_clip.colorspace_settings.name
        except Exception:
            pass


classes = (
    CV_AUTOTRACK_OT_detect_track,
    CV_AUTOTRACK_OT_clean_tracks,
    CV_AUTOTRACK_OT_analyze_solve,
    CV_AUTOTRACK_OT_solve_camera,
    CV_AUTOTRACK_OT_solve_refine,
    CV_AUTOTRACK_OT_solve_setup_dialog,
    CV_AUTOTRACK_OT_build_proxy_confirm,
    CV_AUTOTRACK_OT_load_external_mask_clip,
    CV_AUTOTRACK_OT_sync_external_mask_clip,
    CV_AUTOTRACK_OT_full_auto_track,
    CV_AUTOTRACK_OT_cancel,
    CV_AUTOTRACK_OT_add_cached_tracks,
    CV_AUTOTRACK_OT_toggle_auto_refine,
    CV_AUTOTRACK_OT_restore_previous_state,
    CV_AUTOTRACK_OT_add_preset,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
