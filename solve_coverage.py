from __future__ import annotations

from dataclasses import dataclass

from .blender_tracks import target_tracks


@dataclass(frozen=True, slots=True)
class SolveCoverage:
    frame_count: int
    insufficient_frames: int
    weak_links: int

    @property
    def needs_confirmation(self) -> bool:
        return self.frame_count < 2 or self.insufficient_frames > 0 or self.weak_links > 0


def inspect_solve_coverage(clip, start_frame: int, end_frame: int, minimum_tracks: int = 8) -> SolveCoverage:
    """Conservative advisory check, not a guarantee that Blender can solve a shot."""
    start_frame, end_frame = int(start_frame), int(end_frame)
    frame_count = max(0, end_frame - start_frame + 1)
    counts = [0] * frame_count
    links = [0] * max(0, frame_count - 1)
    for track in target_tracks(clip):
        if float(getattr(track, "weight", 1.0)) <= 0.0:
            continue
        frames = {
            int(marker.frame)
            for marker in track.markers
            if not marker.mute and start_frame <= int(marker.frame) <= end_frame
        }
        for frame in frames:
            index = frame - start_frame
            counts[index] += 1
            if index > 0 and frame - 1 in frames:
                links[index - 1] += 1
    minimum_tracks = max(1, int(minimum_tracks))
    return SolveCoverage(
        frame_count,
        sum(count < minimum_tracks for count in counts),
        sum(count < minimum_tracks for count in links),
    )
