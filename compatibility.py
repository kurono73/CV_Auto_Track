from __future__ import annotations

from pathlib import Path


def pixel_to_marker_co(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    return (float(x) / float(width), 1.0 - (float(y) / float(height)))


def marker_co_to_pixel(co: tuple[float, float], width: int, height: int) -> tuple[float, float]:
    return (float(co[0]) * float(width), (1.0 - float(co[1])) * float(height))


def scene_frame_to_clip_frame(scene_frame: int, clip_frame_start: int) -> int:
    return int(scene_frame) - int(clip_frame_start) + 1


def clip_frame_to_scene_frame(clip_frame: int, clip_frame_start: int) -> int:
    return int(clip_frame) + int(clip_frame_start) - 1


def clip_frame_to_index(clip_frame: int, start_clip_frame: int) -> int:
    return int(clip_frame) - int(start_clip_frame)


def index_to_clip_frame(index: int, start_clip_frame: int) -> int:
    return int(index) + int(start_clip_frame)


def resolve_blender_path(path: str, blend_filepath: str = "") -> str:
    if path.startswith("//") and blend_filepath:
        return str((Path(blend_filepath).parent / path[2:]).resolve())
    return path
