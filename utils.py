from __future__ import annotations

import math
import statistics
from collections.abc import Iterable


def is_finite_point(x: float, y: float) -> bool:
    return math.isfinite(float(x)) and math.isfinite(float(y))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def median(values: Iterable[float], default: float = 0.0) -> float:
    data = [float(v) for v in values]
    return statistics.median(data) if data else default


def mad(values: Iterable[float], center: float | None = None, default: float = 0.0) -> float:
    data = [float(v) for v in values]
    if not data:
        return default
    center_value = statistics.median(data) if center is None else center
    return statistics.median(abs(v - center_value) for v in data)
