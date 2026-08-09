from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .compatibility import resolve_blender_path
from .dependencies import ensure_numpy_cv2


class OpenCVUnsupportedMediaError(RuntimeError):
    pass


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
        self.blend_filepath = str(blend_filepath or "")
        self.filepath = resolve_blender_path(str(clip.filepath), blend_filepath)
        self.source = str(getattr(clip, "source", "MOVIE"))
        self.width = int(clip.size[0]) if clip.size else 0
        self.height = int(clip.size[1]) if clip.size else 0
        self._original_width = self.width
        self._original_height = self.height
        self.analysis_scale = _effective_analysis_scale(
            float(analysis_scale),
            self.width,
            self.height,
            int(minimum_analysis_width),
            int(minimum_analysis_height),
        )
        self._capture = None
        self._proxy_capture = None
        self._proxy_source = None
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
        if self._proxy_capture is not None:
            self._proxy_capture.release()
            self._proxy_capture = None

    def read_gray(self, clip_frame: int):
        clip_frame = int(clip_frame)
        if clip_frame in self.cache:
            value = self.cache.pop(clip_frame)
            self.cache[clip_frame] = value
            return value
        bgr = self._read_bgr(clip_frame)
        if bgr is None:
            bgr = self._read_proxy_bgr(clip_frame)
        if bgr is None:
            raise OpenCVUnsupportedMediaError(
                "OpenCV cannot read this footage. Build a Blender 100% proxy, then run CV Auto Track again."
            )
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
            return self._read_image(path, self.cv2.IMREAD_COLOR)
        if self._capture is None:
            self._capture = self.cv2.VideoCapture(self.filepath)
        index = max(0, int(clip_frame) - 1 + int(getattr(self.clip, "frame_offset", 0)))
        self._capture.set(self.cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self._capture.read()
        return frame if ok else None

    def _read_proxy_bgr(self, clip_frame: int):
        if self._proxy_source is None:
            self._proxy_source = _find_proxy_source(self.clip, self.filepath, self.blend_filepath)
        if self._proxy_source is None:
            return None
        kind = self._proxy_source[0]
        if kind == "MOVIE":
            path = self._proxy_source[1]
            if self._proxy_capture is None:
                self._proxy_capture = self.cv2.VideoCapture(path)
            index = max(0, int(clip_frame) - 1 + int(getattr(self.clip, "frame_offset", 0)))
            self._proxy_capture.set(self.cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = self._proxy_capture.read()
            return frame if ok else None
        if kind == "SEQUENCE":
            files = self._proxy_source[1]
            index = max(0, int(clip_frame) - 1 + int(getattr(self.clip, "frame_offset", 0)))
            if index >= len(files):
                return None
            return self._read_image(str(files[index]), self.cv2.IMREAD_COLOR)
        return None

    def _read_image(self, path: str, flags: int):
        try:
            return self.cv2.imread(path, flags)
        except self.cv2.error:
            return None


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


def _find_proxy_source(clip, filepath: str, blend_filepath: str = ""):
    path = Path(filepath)
    roots = _proxy_roots(clip, filepath, blend_filepath)
    if not roots:
        return None
    for root in roots:
        movie = _find_proxy_movie(root, clip, path)
        if movie is not None:
            return ("MOVIE", str(movie))
    for root in roots:
        images = _find_proxy_sequence(root, clip, path)
        if images:
            return ("SEQUENCE", images)
    return None


def _proxy_roots(clip, filepath: str, blend_filepath: str = "") -> list[Path]:
    path = Path(filepath)
    if not path.name:
        return []
    proxy = getattr(clip, "proxy", None)
    directory = str(getattr(proxy, "directory", "") or "") if proxy is not None else ""
    roots = []
    if directory:
        roots.append(Path(resolve_blender_path(directory, blend_filepath)))
    roots.append(path.parent / "BL_proxy")
    unique = []
    seen = set()
    for root in roots:
        resolved = str(root)
        if resolved in seen:
            continue
        seen.add(resolved)
        if root.exists():
            unique.append(root)
    return unique


def _proxy_clip_directory_names(clip, source_path: Path) -> list[str]:
    names = [str(getattr(clip, "name", "") or ""), source_path.name, source_path.stem]
    unique = []
    seen = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique


def _find_proxy_movie(root: Path, clip, source_path: Path) -> Path | None:
    candidates = [
        root / directory / "proxy_100.avi"
        for directory in _proxy_clip_directory_names(clip, source_path)
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _find_proxy_sequence(root: Path, clip, source_path: Path) -> list[Path]:
    image_extensions = {".jpg", ".jpeg"}
    directories = [
        root / directory / "proxy_100"
        for directory in _proxy_clip_directory_names(clip, source_path)
    ]
    for directory in directories:
        if not directory.exists():
            continue
        files = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in image_extensions
        )
        if files:
            return files
    return []


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
