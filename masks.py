from __future__ import annotations

import re
from pathlib import Path

from .compatibility import clip_frame_to_scene_frame, resolve_blender_path
from .dependencies import ensure_numpy_cv2

_MASK_CACHE: dict[tuple, object] = {}
_MASK_CACHE_LIMIT = 256


def active_mask(context, props):
    explicit_mask = getattr(props, "tracking_mask", None)
    if explicit_mask is not None:
        return explicit_mask
    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == "CLIP_EDITOR":
        space_mask = getattr(space, "mask", None)
        if space_mask is not None:
            return space_mask
    screen = getattr(getattr(context, "window", None), "screen", None)
    if screen is not None:
        for area in screen.areas:
            if area.type != "CLIP_EDITOR":
                continue
            for space in area.spaces:
                if space.type == "CLIP_EDITOR":
                    space_mask = getattr(space, "mask", None)
                    if space_mask is not None:
                        return space_mask
    return None


def build_detection_mask(context, clip, props, width: int, height: int, np, frame: int | None = None):
    if not bool(getattr(props, "use_mask", False)):
        return np.full((height, width), 255, dtype=np.uint8)

    _, cv2 = ensure_numpy_cv2()
    if getattr(props, "mask_source", "BLENDER") == "EXTERNAL":
        mask_data = None
        cache_key = _cache_key(context, mask_data, clip, props, width, height, frame)
    else:
        mask_data = active_mask(context, props)
        if mask_data is None:
            raise RuntimeError("Use Mask is enabled, but no Mask datablock is selected.")
        cache_key = _cache_key(context, mask_data, clip, props, width, height, frame)
    cached = _MASK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if getattr(props, "mask_source", "BLENDER") == "EXTERNAL":
        result = _build_external_mask(context, clip, props, width, height, np, cv2, frame)
    else:
        original_frame = _set_scene_frame_for_mask(context, clip, frame)
        try:
            result = np.zeros((height, width), dtype=np.uint8)
            for layer in mask_data.layers:
                if getattr(layer, "hide", False) or getattr(layer, "hide_render", False):
                    continue
                layer_mask = _rasterize_layer(layer, width, height, np, cv2)
                if layer_mask is None:
                    continue
                if bool(getattr(layer, "invert", False)):
                    layer_mask = cv2.bitwise_not(layer_mask)
                blend = getattr(layer, "blend", "MERGE_ADD")
                if blend in {"MERGE_SUBTRACT", "SUBTRACT"}:
                    result = cv2.bitwise_and(result, cv2.bitwise_not(layer_mask))
                elif blend in {"MUL", "DARKEN"}:
                    result = cv2.bitwise_and(result, layer_mask)
                elif blend == "REPLACE":
                    result = layer_mask
                elif blend == "DIFFERENCE":
                    result = cv2.bitwise_xor(result, layer_mask)
                else:
                    result = cv2.bitwise_or(result, layer_mask)
        finally:
            _restore_scene_frame(context, original_frame)

    if getattr(props, "mask_mode", "INCLUDE_WHITE") == "EXCLUDE_WHITE":
        result = cv2.bitwise_not(result)
    margin = _scaled_margin(clip, width, height, props)
    if margin:
        result = _erode_allowed_area(result, margin, cv2)
    _store_cached_mask(cache_key, result)
    return result


def clear_detection_mask_cache() -> None:
    _MASK_CACHE.clear()


def _cache_key(context, mask_data, clip, props, width: int, height: int, frame: int | None) -> tuple:
    clip_start = int(getattr(clip, "frame_start", 1)) if clip is not None else 1
    scene_frame = None if frame is None else clip_frame_to_scene_frame(int(frame), clip_start)
    source = str(getattr(props, "mask_source", "BLENDER"))
    if source == "EXTERNAL":
        mask_info = _external_mask_info(context, clip, props, frame)
        mask_id = (
            "external",
            mask_info,
            _file_stamp(mask_info[1]),
            str(getattr(props, "external_mask_channel", "LUMA")),
        )
    else:
        mask_id = getattr(mask_data, "session_uid", None) or id(mask_data)
    return (
        source,
        mask_id,
        scene_frame,
        int(width),
        int(height),
        str(getattr(props, "mask_mode", "INCLUDE_WHITE")),
        int(getattr(props, "mask_margin", 0)),
    )


def _store_cached_mask(key: tuple, mask) -> None:
    if len(_MASK_CACHE) >= _MASK_CACHE_LIMIT:
        _MASK_CACHE.clear()
    _MASK_CACHE[key] = mask


def _rasterize_layer(layer, width: int, height: int, np, cv2):
    polygons = []
    for spline in layer.splines:
        if not bool(getattr(spline, "use_fill", True)):
            continue
        polygon = _sample_spline(spline, width, height)
        if len(polygon) >= 3:
            polygons.append(np.array(polygon, dtype=np.int32))
    if not polygons:
        return None
    layer_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(layer_mask, polygons, 255)
    return layer_mask


def _build_external_mask(context, clip, props, width: int, height: int, np, cv2, frame: int | None):
    source_kind, path, movie_index = _external_mask_info(context, clip, props, frame)
    if not path:
        raise RuntimeError("Use Mask is enabled, but no external Mask Clip is selected.")
    if not Path(path).exists():
        raise RuntimeError(f"External mask image does not exist: {path}")

    if source_kind == "MOVIE":
        mask = _read_external_mask_movie(path, movie_index, props, np, cv2)
    elif Path(path).suffix.lower() == ".exr":
        mask = _read_external_mask_with_blender(path, props, np)
    else:
        mask = _read_external_mask_with_cv2(path, props, np, cv2)

    if mask.shape[:2] != (int(height), int(width)):
        mask = cv2.resize(mask, (int(width), int(height)), interpolation=cv2.INTER_LINEAR)
    _, binary = cv2.threshold(mask.astype(np.uint8), 127, 255, cv2.THRESH_BINARY)
    return binary


def _external_mask_info(context, clip, props, frame: int | None) -> tuple[str, str, int]:
    mask_clip = getattr(props, "external_mask_clip", None)
    if mask_clip is None:
        return ("IMAGE", "", 0)
    blend_filepath = getattr(getattr(context, "blend_data", None), "filepath", "") if context is not None else ""
    path = resolve_blender_path(str(getattr(mask_clip, "filepath", "") or ""), blend_filepath)
    source = str(getattr(mask_clip, "source", "MOVIE"))
    source_frame = max(1, int(frame) if frame is not None else 1)
    source_start = int(getattr(clip, "frame_start", 1)) if clip is not None else 1
    mask_start = int(getattr(mask_clip, "frame_start", 1))
    scene_frame = clip_frame_to_scene_frame(source_frame, source_start)
    mask_frame = max(1, scene_frame - mask_start + 1)
    offset_frame = max(1, mask_frame + int(getattr(mask_clip, "frame_offset", 0)))
    if source == "SEQUENCE":
        return ("IMAGE", _sequence_path_for_clip_frame(path, offset_frame), 0)
    movie_index = max(0, offset_frame - 1)
    return ("MOVIE", path, movie_index)


def _sequence_path_for_clip_frame(first_path: str, clip_frame: int) -> str:
    path = Path(first_path)
    match = re.search(r"(\d+)(\.[^.]+)$", path.name)
    if not match:
        return str(path)
    padding = len(match.group(1))
    first_number = int(match.group(1))
    number = first_number + (int(clip_frame) - 1)
    name = f"{path.name[:match.start(1)]}{number:0{padding}d}{path.name[match.end(1):]}"
    return str(path.with_name(name))


def _file_stamp(path: str) -> tuple[int, int] | None:
    if not path:
        return None
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (int(stat.st_mtime_ns), int(stat.st_size))


def _read_external_mask_with_cv2(path: str, props, np, cv2):
    image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError(f"Could not read external mask image: {path}")
    if image.ndim == 2:
        return _normalize_mask_values(image, np)
    if str(getattr(props, "external_mask_channel", "LUMA")) == "ALPHA":
        if image.shape[2] < 4:
            raise RuntimeError("External mask channel is Alpha, but the image has no alpha channel.")
        return _normalize_mask_values(image[:, :, 3], np)
    if image.shape[2] >= 3:
        return _normalize_mask_values(cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY), np)
    return _normalize_mask_values(image[:, :, 0], np)


def _read_external_mask_movie(path: str, frame_index: int, props, np, cv2):
    if str(getattr(props, "external_mask_channel", "LUMA")) == "ALPHA":
        raise RuntimeError("Alpha channel masks are only supported for image files and image sequences.")
    capture = cv2.VideoCapture(path)
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_index)))
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read external mask movie frame: {path}")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _read_external_mask_with_blender(path: str, props, np):
    import bpy

    image = bpy.data.images.load(path, check_existing=True)
    image.colorspace_settings.name = "Non-Color"
    width, height = int(image.size[0]), int(image.size[1])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Could not read external mask image size: {path}")
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape((height, width, 4))
    pixels = np.flipud(pixels)
    if str(getattr(props, "external_mask_channel", "LUMA")) == "ALPHA":
        mask = pixels[:, :, 3]
    else:
        mask = (pixels[:, :, 0] * 0.2126) + (pixels[:, :, 1] * 0.7152) + (pixels[:, :, 2] * 0.0722)
    return _normalize_mask_values(mask, np)


def _normalize_mask_values(mask, np):
    if mask.dtype == np.uint8:
        return mask
    if np.issubdtype(mask.dtype, np.integer):
        max_value = max(1, int(np.iinfo(mask.dtype).max))
        return np.clip((mask.astype(np.float32) / float(max_value)) * 255.0, 0.0, 255.0).astype(np.uint8)
    values = mask.astype(np.float32)
    if values.size and float(values.max()) <= 1.0:
        values = values * 255.0
    return np.clip(values, 0.0, 255.0).astype(np.uint8)


def _set_scene_frame_for_mask(context, clip, frame: int | None) -> int | None:
    scene = getattr(context, "scene", None)
    if scene is None or frame is None:
        return None
    original_frame = int(getattr(scene, "frame_current", 1))
    target_frame = clip_frame_to_scene_frame(int(frame), int(getattr(clip, "frame_start", 1)))
    if original_frame != target_frame:
        scene.frame_set(target_frame)
        return original_frame
    return None


def _restore_scene_frame(context, frame: int | None) -> None:
    if frame is None:
        return
    scene = getattr(context, "scene", None)
    if scene is not None and int(getattr(scene, "frame_current", 1)) != int(frame):
        scene.frame_set(int(frame))


def _erode_allowed_area(mask, margin: int, cv2):
    kernel_size = (max(1, int(margin)) * 2) + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.erode(mask, kernel, iterations=1)


def _scaled_margin(clip, width: int, height: int, props) -> int:
    margin = max(0, int(getattr(props, "mask_margin", 0)))
    if margin <= 0 or clip is None or not getattr(clip, "size", None):
        return margin
    source_width = max(1, int(clip.size[0]))
    source_height = max(1, int(clip.size[1]))
    scale_x = float(width) / float(source_width)
    scale_y = float(height) / float(source_height)
    return max(0, int(round(float(margin) * min(scale_x, scale_y))))


def _sample_spline(spline, width: int, height: int) -> list[tuple[int, int]]:
    points = list(spline.points)
    if len(points) < 2:
        return []
    segment_count = len(points) if bool(getattr(spline, "use_cyclic", False)) else len(points) - 1
    polygon = []
    for index in range(segment_count):
        point_a = points[index]
        point_b = points[(index + 1) % len(points)]
        co_a = _co(point_a, "co")
        handle_a = _handle(point_a, "handle_right")
        handle_b = _handle(point_b, "handle_left")
        co_b = _co(point_b, "co")
        for step in range(16):
            t = step / 16.0
            polygon.append(_mask_co_to_pixel(_bezier(co_a, handle_a, handle_b, co_b, t), width, height))
    if bool(getattr(spline, "use_cyclic", False)):
        polygon.append(_mask_co_to_pixel(_co(points[0], "co"), width, height))
    else:
        polygon.append(_mask_co_to_pixel(_co(points[-1], "co"), width, height))
    return polygon


def _co(point, attr: str) -> tuple[float, float]:
    value = getattr(point, attr)
    return (float(value[0]), float(value[1]))


def _handle(point, attr: str) -> tuple[float, float]:
    co = _co(point, "co")
    type_attr = "handle_right_type" if attr == "handle_right" else "handle_left_type"
    if getattr(point, type_attr, getattr(point, "handle_type", "")) == "VECTOR":
        return co
    handle = _co(point, attr)
    if handle == (0.0, 0.0) and co != (0.0, 0.0):
        return co
    return handle


def _bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    omt = 1.0 - t
    a = omt * omt * omt
    b = 3.0 * omt * omt * t
    c = 3.0 * omt * t * t
    d = t * t * t
    return (
        (a * p0[0]) + (b * p1[0]) + (c * p2[0]) + (d * p3[0]),
        (a * p0[1]) + (b * p1[1]) + (c * p2[1]) + (d * p3[1]),
    )


def _mask_co_to_pixel(co: tuple[float, float], width: int, height: int) -> tuple[int, int]:
    size, x_offset, y_offset = _mask_display_space(width, height)
    x = (float(co[0]) * size) + x_offset
    y = ((1.0 - float(co[1])) * size) + y_offset
    return (int(round(x)), int(round(y)))


def _mask_display_space(width: int, height: int) -> tuple[float, float, float]:
    width = float(width)
    height = float(height)
    if width <= 0.0 or height <= 0.0:
        return 1.0, 0.0, 0.0
    # Blender masks use a width-based square data window, then crop that into the clip display window.
    return width, 0.0, (height - width) * 0.5
