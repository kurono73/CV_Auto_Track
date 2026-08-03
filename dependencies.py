from __future__ import annotations

import importlib
import platform
import sys
import tempfile
import zipfile
from pathlib import Path


def _add_wheel_paths() -> None:
    wheels_dir = Path(__file__).with_name("wheels")
    if not wheels_dir.exists():
        return
    extracted_paths = []
    for wheel in sorted(wheels_dir.glob("*.whl")):
        if not _wheel_matches_current_platform(wheel.name):
            continue
        extracted = _extract_wheel(wheel)
        extracted_paths.append(str(extracted))
    for path in reversed(extracted_paths):
        if path not in sys.path:
            sys.path.insert(0, path)


def _cache_root() -> Path:
    try:
        import bpy  # type: ignore

        root = bpy.utils.user_resource("SCRIPTS", path="cv_autotrack_wheels", create=True)
        if root:
            return Path(root)
    except Exception:
        pass
    return Path(tempfile.gettempdir()) / "cv_autotrack_wheels"


def _wheel_matches_current_platform(name: str) -> bool:
    lower = name.lower()
    machine = platform.machine().lower()
    is_x64 = machine in {"amd64", "x86_64"}
    is_arm64 = machine in {"arm64", "aarch64"}

    if sys.platform.startswith("win"):
        return is_x64 and "win_amd64" in lower
    if sys.platform == "darwin":
        return "macosx" in lower and (("arm64" in lower and is_arm64) or ("x86_64" in lower and is_x64) or "universal2" in lower)
    if sys.platform.startswith("linux"):
        return is_x64 and "x86_64" in lower and ("manylinux" in lower or "linux" in lower)
    return False


def _extract_wheel(wheel: Path) -> Path:
    root = _cache_root()
    target = root / f"{wheel.stem}-{wheel.stat().st_size}"
    marker = target / ".complete"
    if marker.exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "r") as archive:
        archive.extractall(target)
    marker.write_text(wheel.name, encoding="utf-8")
    return target


def ensure_numpy_cv2():
    """Import NumPy/OpenCV, preferring bundled wheels when present."""
    _add_wheel_paths()
    numpy = importlib.import_module("numpy")
    cv2 = importlib.import_module("cv2")
    return numpy, cv2


def dependency_status() -> tuple[bool, str]:
    try:
        numpy, cv2 = ensure_numpy_cv2()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"OpenCV {cv2.__version__} (NumPy {numpy.__version__})"
