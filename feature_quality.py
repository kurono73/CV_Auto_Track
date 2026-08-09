from __future__ import annotations


def corner_ratio(gray, x: float, y: float, size: int = 15) -> float | None:
    patch = extract_patch(gray, x, y, size)
    if patch is None:
        return None
    return patch_corner_ratio(patch)


def is_edge_ambiguous(gray, x: float, y: float, size: int, minimum_ratio: float) -> bool:
    ratio = corner_ratio(gray, x, y, size)
    return ratio is not None and ratio < float(minimum_ratio)


def patch_corner_ratio(patch) -> float | None:
    if patch.shape[0] < 3 or patch.shape[1] < 3:
        return None
    item = patch.astype("float32", copy=False)
    gx = item[1:-1, 2:] - item[1:-1, :-2]
    gy = item[2:, 1:-1] - item[:-2, 1:-1]
    xx = float((gx * gx).sum())
    yy = float((gy * gy).sum())
    xy = float((gx * gy).sum())
    trace = xx + yy
    if trace <= 1e-6:
        return None
    discriminant = max(0.0, ((xx - yy) * (xx - yy)) + (4.0 * xy * xy)) ** 0.5
    major = 0.5 * (trace + discriminant)
    minor = 0.5 * (trace - discriminant)
    if major <= 1e-6:
        return None
    return max(0.0, min(1.0, minor / major))


def extract_patch(gray, x: float, y: float, size: int):
    size = max(5, int(size))
    if size % 2 == 0:
        size += 1
    radius = size // 2
    cx = int(round(float(x)))
    cy = int(round(float(y)))
    height, width = gray.shape[:2]
    x0 = cx - radius
    x1 = cx + radius + 1
    y0 = cy - radius
    y1 = cy + radius + 1
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        return None
    return gray[y0:y1, x0:x1]
