from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TrackSample:
    frame: int
    x: float
    y: float
    lk_error: float | None = None
    fb_error: float | None = None
    valid: bool = True


@dataclass(slots=True)
class TrackCandidate:
    id: int
    detection_frame: int
    samples: list[TrackSample] = field(default_factory=list)
    quality_score: float = 0.0
    termination_reason: str | None = None
    disabled: bool = False
    source_batch_id: int = 0

    @property
    def valid_samples(self) -> list[TrackSample]:
        return [sample for sample in self.samples if sample.valid]

    @property
    def length(self) -> int:
        return len(self.valid_samples)


@dataclass(slots=True)
class TrackingStats:
    generated_tracks: int = 0
    valid_tracks: int = 0
    disabled_tracks: int = 0
    deleted_tracks: int = 0
    average_track_length: float = 0.0
    median_track_length: float = 0.0
    average_fb_error: float = 0.0
    ransac_inlier_rate: float = 0.0
    solve_error_before: float = -1.0
    solve_error_after: float = -1.0
    refine_iterations: int = 0
    processing_time: float = 0.0
    cancellation_state: str = "Completed"
    warning_count: int = 0

    def as_lines(self) -> list[str]:
        lines = []
        if self.generated_tracks > 0:
            lines.append(f"Generated Tracks: {self.generated_tracks}")
        lines.append(f"Valid Tracks: {self.valid_tracks}")
        if self.disabled_tracks > 0:
            lines.append(f"Disabled Tracks: {self.disabled_tracks}")
        if self.deleted_tracks > 0:
            lines.append(f"Deleted Tracks: {self.deleted_tracks}")
        if self.average_track_length > 0.0:
            lines.append(f"Average Track Length: {self.average_track_length:.2f}")
        if self.median_track_length > 0.0:
            lines.append(f"Median Track Length: {self.median_track_length:.2f}")
        if self.average_fb_error > 0.0:
            lines.append(f"Average FB Error: {self.average_fb_error:.3f}")
        if self.ransac_inlier_rate > 0.0 and self.generated_tracks > 0:
            lines.append(f"RANSAC Inlier Rate: {self.ransac_inlier_rate:.3f}")
        if self.solve_error_before >= 0.0:
            lines.append(f"Solve Error Before: {self.solve_error_before:.3f}")
        if self.solve_error_after >= 0.0:
            lines.append(f"Solve Error After: {self.solve_error_after:.3f}")
        if self.refine_iterations > 0:
            lines.append(f"Refine Iterations: {self.refine_iterations}")
        lines.append(f"Processing Time: {self.processing_time:.2f}s")
        if self.cancellation_state and self.cancellation_state != "Completed":
            lines.append(f"Status: {self.cancellation_state}")
        if self.warning_count > 0:
            lines.append(f"Warning Count: {self.warning_count}")
        return lines
