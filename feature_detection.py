from __future__ import annotations

from dataclasses import dataclass

from .dependencies import ensure_numpy_cv2


@dataclass(frozen=True, slots=True)
class DetectionSettings:
    detector_type: str = "SHI_TOMASI"
    maximum_features: int = 500
    quality_level: float = 0.01
    minimum_distance: float = 12.0
    block_size: int = 7
    use_harris_detector: bool = False
    harris_k: float = 0.04
    edge_margin: int = 16
    grid_columns: int = 8
    grid_rows: int = 5
    max_per_cell: int = 20


def build_edge_mask(width: int, height: int, edge_margin: int, np):
    mask = np.full((height, width), 255, dtype=np.uint8)
    margin = max(0, int(edge_margin))
    if margin:
        mask[:margin, :] = 0
        mask[-margin:, :] = 0
        mask[:, :margin] = 0
        mask[:, -margin:] = 0
    return mask


def suppress_points(mask, points: list[tuple[float, float]], radius: float) -> None:
    if not points:
        return
    _, cv2 = ensure_numpy_cv2()
    r = max(1, int(radius))
    for x, y in points:
        cv2.circle(mask, (int(round(x)), int(round(y))), r, 0, thickness=-1)


def detect_shi_tomasi(
    gray,
    settings: DetectionSettings,
    exclusion_points: list[tuple[float, float]] | None = None,
    external_mask=None,
) -> list[tuple[float, float]]:
    np, cv2 = ensure_numpy_cv2()
    height, width = gray.shape[:2]
    mask = build_edge_mask(width, height, settings.edge_margin, np)
    if external_mask is not None:
        mask = cv2.bitwise_and(mask, external_mask.astype(np.uint8))
    suppress_points(mask, exclusion_points or [], settings.minimum_distance)

    detector_type = str(getattr(settings, "detector_type", "SHI_TOMASI"))
    if detector_type != "SHI_TOMASI":
        points = _detect_keypoints(gray, mask, settings, cv2)
        return limit_points_per_grid(points, width, height, settings)

    pts = cv2.goodFeaturesToTrack(
        gray,
        maxCorners=max(1, int(settings.maximum_features)),
        qualityLevel=max(0.000001, float(settings.quality_level)),
        minDistance=max(1.0, float(settings.minimum_distance)),
        mask=mask,
        blockSize=max(3, int(settings.block_size) | 1),
        useHarrisDetector=bool(settings.use_harris_detector),
        k=float(settings.harris_k),
    )
    if pts is None:
        return []

    points = [(float(p[0][0]), float(p[0][1])) for p in pts]
    return limit_points_per_grid(points, width, height, settings)


def _detect_keypoints(gray, mask, settings: DetectionSettings, cv2) -> list[tuple[float, float]]:
    detector_type = str(settings.detector_type)
    maximum = max(1, int(settings.maximum_features))
    if detector_type == "SIFT" and hasattr(cv2, "SIFT_create"):
        detector = cv2.SIFT_create(
            nfeatures=maximum,
            contrastThreshold=max(0.0001, float(settings.quality_level)),
            edgeThreshold=max(1, int(settings.block_size) * 2),
        )
        keypoints = detector.detect(gray, mask)
    elif detector_type == "ORB" and hasattr(cv2, "ORB_create"):
        detector = cv2.ORB_create(
            nfeatures=maximum,
            edgeThreshold=max(1, int(settings.edge_margin)),
            fastThreshold=max(1, min(255, int(round(float(settings.quality_level) * 1000.0)))),
        )
        keypoints = detector.detect(gray, mask)
    elif detector_type == "FAST" and hasattr(cv2, "FastFeatureDetector_create"):
        detector = cv2.FastFeatureDetector_create(
            threshold=max(1, min(255, int(round(float(settings.quality_level) * 1000.0)))),
            nonmaxSuppression=True,
        )
        keypoints = detector.detect(gray, mask)
    else:
        return []
    if keypoints is None:
        return []
    keypoints = sorted(keypoints, key=lambda item: float(getattr(item, "response", 0.0)), reverse=True)
    return _spaced_keypoints(keypoints, maximum, max(1.0, float(settings.minimum_distance)))


def _spaced_keypoints(keypoints, maximum: int, minimum_distance: float) -> list[tuple[float, float]]:
    selected: list[tuple[float, float]] = []
    min_distance_sq = float(minimum_distance) * float(minimum_distance)
    for keypoint in keypoints:
        x, y = float(keypoint.pt[0]), float(keypoint.pt[1])
        if any(((x - ox) * (x - ox)) + ((y - oy) * (y - oy)) < min_distance_sq for ox, oy in selected):
            continue
        selected.append((x, y))
        if len(selected) >= int(maximum):
            break
    return selected


def limit_points_per_grid(
    points: list[tuple[float, float]],
    width: int,
    height: int,
    settings: DetectionSettings,
) -> list[tuple[float, float]]:
    cols = max(1, int(settings.grid_columns))
    rows = max(1, int(settings.grid_rows))
    max_per_cell = max(1, int(settings.max_per_cell))
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, y in points:
        cx = min(cols - 1, max(0, int((x / max(1, width)) * cols)))
        cy = min(rows - 1, max(0, int((y / max(1, height)) * rows)))
        bucket = buckets.setdefault((cx, cy), [])
        if len(bucket) < max_per_cell:
            bucket.append((x, y))
    selected: list[tuple[float, float]] = []
    bucket_keys = _spread_bucket_keys(buckets, cols, rows)
    while bucket_keys:
        next_keys = []
        for key in bucket_keys:
            bucket = buckets.get(key) or []
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            if bucket:
                next_keys.append(key)
        bucket_keys = next_keys
    return selected


def _spread_bucket_keys(buckets: dict[tuple[int, int], list[tuple[float, float]]], cols: int, rows: int) -> list[tuple[int, int]]:
    remaining = set(buckets)
    if not remaining:
        return []
    first = _nearest_cell_to_center(remaining, cols, rows)
    ordered = [first]
    remaining.remove(first)
    while remaining:
        next_key = max(
            remaining,
            key=lambda key: (
                min(_cell_distance(key, chosen, cols, rows) for chosen in ordered),
                -abs(float(key[0]) - ((max(1, cols) - 1) * 0.5)),
                -abs(float(key[1]) - ((max(1, rows) - 1) * 0.5)),
                -key[1],
                -key[0],
            ),
        )
        ordered.append(next_key)
        remaining.remove(next_key)
    return ordered


def _nearest_cell_to_center(cells: set[tuple[int, int]], cols: int, rows: int) -> tuple[int, int]:
    center_x = (max(1, cols) - 1) * 0.5
    center_y = (max(1, rows) - 1) * 0.5
    return min(cells, key=lambda key: ((float(key[0]) - center_x) ** 2 + (float(key[1]) - center_y) ** 2, key))


def _cell_distance(a: tuple[int, int], b: tuple[int, int], cols: int, rows: int) -> float:
    dx = (float(a[0]) - float(b[0])) / float(max(1, cols - 1))
    dy = (float(a[1]) - float(b[1])) / float(max(1, rows - 1))
    return (dx * dx) + (dy * dy)
