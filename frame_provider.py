from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .compatibility import resolve_blender_path
from .dependencies import ensure_numpy_cv2


@dataclass(frozen=True, slots=True)
class FrameRange:
    start: int
    end: int

    def frames(self) -> list[int]:
        step = 1 if self.end >= self.start else -1
        return list(range(self.start, self.end + step, step))


class FrameProvider:
    def __init__(
        self,
        clip,
        analysis_scale: float = 0.5,
        cache_size: int = 8,
        blend_filepath: str = "",
        minimum_analysis_width: int = 1,
        minimum_analysis_height: int = 1,
    ):
        self.clip = clip
        self.cache_size = max(2, int(cache_size))
        self.cache: OrderedDict[int, object] = OrderedDict()
        self.np, self.cv2 = ensure_numpy_cv2()
        self.filepath = resolve_blender_path(str(clip.filepath), blend_filepath)
        self.source = str(getattr(clip, "source", "MOVIE"))
        self.width = int(clip.size[0]) if clip.size else 0
        self.height = int(clip.size[1]) if clip.size else 0
        self.analysis_scale = _effective_analysis_scale(
            float(analysis_scale),
            self.width,
            self.height,
            int(minimum_analysis_width),
            int(minimum_analysis_height),
        )
        self._capture = None
        self._sequence = _SequencePath(self.filepath, getattr(clip, "frame_start", 1))

    @property
    def analysis_width(self) -> int:
        return max(1, int(round(self.width * self.analysis_scale)))

    @property
    def analysis_height(self) -> int:
        return max(1, int(round(self.height * self.analysis_scale)))

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def read_gray(self, clip_frame: int):
        clip_frame = int(clip_frame)
        if clip_frame in self.cache:
            value = self.cache.pop(clip_frame)
            self.cache[clip_frame] = value
            return value
        bgr = self._read_bgr(clip_frame)
        if bgr is None:
            raise RuntimeError(f"Frame read failed: {clip_frame}")
        if self.width <= 0 or self.height <= 0:
            self.height, self.width = bgr.shape[:2]
        if self.analysis_scale != 1.0:
            bgr = self.cv2.resize(
                bgr,
                (self.analysis_width, self.analysis_height),
                interpolation=self.cv2.INTER_AREA,
            )
        gray = self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2GRAY)
        self.cache[clip_frame] = gray
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return gray

    def read_frames(self, frames: list[int]) -> list[tuple[int, object]]:
        return [(frame, self.read_gray(frame)) for frame in frames]

    def _read_bgr(self, clip_frame: int):
        if self.source == "SEQUENCE":
            offset_frame = max(1, int(clip_frame) + int(getattr(self.clip, "frame_offset", 0)))
            path = self._sequence.path_for_clip_frame(offset_frame)
            image = self.cv2.imread(path, self.cv2.IMREAD_COLOR)
            return image
        if self._capture is None:
            self._capture = self.cv2.VideoCapture(self.filepath)
        index = max(0, int(clip_frame) - 1 + int(getattr(self.clip, "frame_offset", 0)))
        self._capture.set(self.cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self._capture.read()
        return frame if ok else None


class _SequencePath:
    def __init__(self, first_path: str, clip_frame_start: int):
        self.first_path = Path(first_path)
        self.clip_frame_start = int(clip_frame_start)
        match = re.search(r"(\d+)(\.[^.]+)$", self.first_path.name)
        self.padding = len(match.group(1)) if match else 0
        self.first_number = int(match.group(1)) if match else None
        self.prefix = self.first_path.name[: match.start(1)] if match else ""
        self.suffix = self.first_path.name[match.end(1) :] if match else self.first_path.name

    def path_for_clip_frame(self, clip_frame: int) -> str:
        if self.first_number is None:
            return str(self.first_path)
        number = self.first_number + (int(clip_frame) - 1)
        name = f"{self.prefix}{number:0{self.padding}d}{self.suffix}"
        return str(self.first_path.with_name(name))


def range_from_props(context, clip, props) -> FrameRange:
    mode = props.frame_range_mode
    duration = max(1, int(getattr(clip, "frame_duration", 1)))
    current = _current_clip_frame(context, clip, duration)
    if mode == "PREVIEW" and context.scene.use_preview_range:
        start = context.scene.frame_preview_start
        end = context.scene.frame_preview_end
        return FrameRange(max(1, start - clip.frame_start + 1), max(1, end - clip.frame_start + 1))
    if mode == "SCENE":
        start = context.scene.frame_start
        end = context.scene.frame_end
        return FrameRange(max(1, start - clip.frame_start + 1), max(1, end - clip.frame_start + 1))
    if mode == "CURRENT_TO_END":
        return FrameRange(current, duration)
    if mode == "START_TO_CURRENT":
        return FrameRange(1, current)
    if mode == "CUSTOM":
        return FrameRange(int(props.custom_start_frame), int(props.custom_end_frame))
    return FrameRange(1, duration)


def _current_clip_frame(context, clip, duration: int) -> int:
    scene_frame = int(getattr(context.scene, "frame_current", 1))
    clip_start = int(getattr(clip, "frame_start", 1))
    return max(1, min(int(duration), scene_frame - clip_start + 1))


def _effective_analysis_scale(requested_scale: float, width: int, height: int, min_width: int, min_height: int) -> float:
    scale = max(0.05, min(1.0, float(requested_scale)))
    if int(width) <= 0 or int(height) <= 0:
        return scale
    min_width = max(1, int(min_width))
    min_height = max(1, int(min_height))
    required = max(float(min_width) / float(width), float(min_height) / float(height))
    return max(scale, min(1.0, required))
