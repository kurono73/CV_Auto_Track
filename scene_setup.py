from __future__ import annotations

from dataclasses import dataclass
import math

import bpy


EPSILON = 1e-8


@dataclass(slots=True)
class SceneSetupResult:
    camera_created: bool = False
    solver_created: bool = False
    background_created: bool = False
    background_updated: bool = False
    undistorted: bool = False


def ensure_scene_setup(context, clip) -> SceneSetupResult:
    result = SceneSetupResult()
    camera_obj, camera_created = _ensure_scene_camera(context.scene)
    result.camera_created = camera_created
    _solver_constraint_for_clip(camera_obj, clip, result)
    background = _background_for_clip(camera_obj.data, clip, result)

    undistorted = _clip_has_distortion(clip)
    result.undistorted = undistorted
    if background is not None:
        result.background_updated = _set_background_undistorted(background, undistorted) or result.background_updated

    _print_result(result)
    return result

def _ensure_scene_camera(scene):
    camera_obj = getattr(scene, "camera", None)
    if camera_obj is not None and getattr(camera_obj, "type", None) == "CAMERA":
        return camera_obj, False

    camera_data = bpy.data.cameras.new("Camera")
    camera_obj = bpy.data.objects.new("Camera", camera_data)
    camera_obj.rotation_euler[0] = math.radians(90.0)
    scene.collection.objects.link(camera_obj)
    scene.camera = camera_obj
    return camera_obj, True


def _solver_constraint_for_clip(camera_obj, clip, result: SceneSetupResult):
    for constraint in camera_obj.constraints:
        if getattr(constraint, "type", None) != "CAMERA_SOLVER":
            continue
        if bool(getattr(constraint, "use_active_clip", False)):
            return constraint
        if getattr(constraint, "clip", None) is clip:
            return constraint

    constraint = camera_obj.constraints.new(type="CAMERA_SOLVER")
    if hasattr(constraint, "use_active_clip"):
        constraint.use_active_clip = False
    if hasattr(constraint, "clip"):
        constraint.clip = clip
    result.solver_created = True
    return constraint


def _background_for_clip(camera_data, clip, result: SceneSetupResult):
    backgrounds = getattr(camera_data, "background_images", None)
    if backgrounds is None:
        return None
    result.background_updated = _set_if_available(camera_data, "show_background_images", True) or result.background_updated

    for background in backgrounds:
        if _background_matches_clip(background, clip):
            return background

    background = backgrounds.new()
    background.source = "MOVIE_CLIP"
    background.clip = clip
    _set_if_available(background, "alpha", 1.0)
    _set_if_available(background, "show_background_image", True)
    result.background_created = True
    return background


def _background_matches_clip(background, clip) -> bool:
    if getattr(background, "source", None) != "MOVIE_CLIP":
        return False
    if bool(getattr(background, "use_camera_clip", False)):
        return True
    return getattr(background, "clip", None) is clip


def _set_if_available(item, attr: str, value) -> bool:
    if not hasattr(item, attr):
        return False
    try:
        if getattr(item, attr) == value:
            return False
        setattr(item, attr, value)
        return True
    except Exception:
        return False


def _set_background_undistorted(background, value: bool) -> bool:
    clip_user = getattr(background, "clip_user", None)
    if clip_user is None or not hasattr(clip_user, "use_render_undistorted"):
        return False
    try:
        if bool(clip_user.use_render_undistorted) == bool(value):
            return False
        clip_user.use_render_undistorted = bool(value)
        return True
    except Exception:
        return False


def _clip_has_distortion(clip) -> bool:
    camera = getattr(getattr(clip, "tracking", None), "camera", None)
    if camera is None:
        return False
    for attr in _DISTORTION_ATTRIBUTES:
        try:
            value = float(getattr(camera, attr))
        except Exception:
            continue
        if abs(value) > EPSILON:
            return True
    return False


_DISTORTION_ATTRIBUTES = (
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
    "brown_p1",
    "brown_p2",
)


def _print_result(result: SceneSetupResult) -> None:
    camera = "created" if result.camera_created else "existing"
    solver = "created" if result.solver_created else "existing"
    background = "created" if result.background_created else "existing"
    display = "Undistorted" if result.undistorted else "Distorted"
    print("[CV Auto Track] Auto Scene Setup:")
    print(f"[CV Auto Track]   Camera: {camera}")
    print(f"[CV Auto Track]   Camera Solver: {solver}")
    print(f"[CV Auto Track]   Background: {background}")
    print(f"[CV Auto Track]   Display: {display}")
