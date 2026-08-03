from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .constants import FILTER_PRESETS, PRESETS, PROTECTED_PREFIX


USER_DEFAULT_PROPERTY_NAMES = (
    "advanced_mode",
    "preset",
    "frame_range_mode",
    "custom_start_frame",
    "custom_end_frame",
    "tracking_direction",
    "analysis_scale",
    "detector_type",
    "auto_scale_pixel_parameters",
    "minimum_analysis_width",
    "minimum_analysis_height",
    "maximum_features",
    "quality_level",
    "minimum_distance",
    "block_size",
    "use_harris_detector",
    "harris_k",
    "edge_margin",
    "window_size",
    "pyramid_levels",
    "termination_count",
    "termination_epsilon",
    "minimum_eigen_threshold",
    "maximum_lk_error",
    "maximum_motion",
    "enable_forward_backward",
    "maximum_fb_error",
    "enable_redetect",
    "adaptive_redetect",
    "redetect_interval",
    "minimum_active_tracks",
    "target_track_count",
    "auto_target_track_count",
    "track_density_percent",
    "auto_track_budget",
    "maximum_total_tracks",
    "auto_bake_track_limit",
    "maximum_baked_tracks",
    "lead_edge_redetect",
    "detect_only_when_needed",
    "enable_ransac",
    "ransac_model",
    "ransac_threshold",
    "ransac_confidence",
    "ransac_minimum_points",
    "filter_preset",
    "filter_preset_compact",
    "duplicate_distance",
    "minimum_track_length",
    "preferred_track_length",
    "minimum_valid_ratio",
    "grid_columns",
    "grid_rows",
    "maximum_tracks_per_cell",
    "minimum_tracks_per_cell",
    "distribution_strength",
    "use_mask",
    "mask_source",
    "external_mask_channel",
    "auto_solve_refine",
    "auto_solve_keyframes",
    "bake_pattern_size",
    "bake_search_size",
    "mask_mode",
    "mask_margin",
    "track_replace_mode",
    "preserve_existing_tracks",
    "use_existing_tracks_as_exclusion_points",
    "maximum_track_error",
    "mad_multiplier",
    "outlier_percentage_per_iteration",
    "maximum_disabled_per_iteration",
    "minimum_remaining_tracks",
    "delete_refined_tracks",
    "refine_motion_outliers",
    "motion_outlier_multiplier",
    "motion_outlier_min_residual",
    "motion_outlier_min_ratio",
    "motion_outlier_local_radius",
    "motion_outlier_local_min_tracks",
    "full_auto_refine_iterations",
    "maximum_refine_iterations",
    "target_solve_error",
    "minimum_solve_improvement",
    "protect_selected_tracks",
    "protect_existing_tracks",
    "protected_name_prefix",
    "analyze_solve_action",
    "cache_size",
)


def _apply_values(props, values: dict | None) -> None:
    if not values:
        return
    for key, value in values.items():
        setattr(props, key, value)


def _apply_preset_values(props, preset: str) -> None:
    _apply_values(props, PRESETS.get(preset))


def _preset_update(self, _context):
    if self.preset in PRESETS:
        _apply_preset_values(self, self.preset)


def _filter_preset_update(self, _context):
    if self.filter_preset in FILTER_PRESETS:
        _apply_values(self, FILTER_PRESETS.get(self.filter_preset))
        if self.filter_preset_compact != self.filter_preset:
            self.filter_preset_compact = self.filter_preset


def _filter_preset_compact_update(self, _context):
    if self.filter_preset_compact in FILTER_PRESETS and self.filter_preset != self.filter_preset_compact:
        self.filter_preset = self.filter_preset_compact


class CV_AUTOTRACK_PG_Settings(bpy.types.PropertyGroup):
    advanced_mode: BoolProperty(name="Advanced Mode", description="Show all CV Auto Track settings", default=False)
    preset: EnumProperty(
        name="Preset",
        description="Apply a tracking setup preset",
        items=[
            ("FAST", "Fast", "Low resolution, fewer tracks, quicker solve prep"),
            ("DYNAMIC", "Dynamic", "Long or changing camera views with frequent redetection"),
            ("HIGH_MOTION", "High Motion", "Fast camera motion with wider optical-flow tolerance"),
            ("BALANCED", "Balanced", "Default balance for general footage"),
            ("SENSITIVE", "Sensitive", "Low-contrast or weak-texture footage with more permissive detection"),
            ("DETAILED", "Detailed", "Higher resolution and denser tracks"),
        ],
        default="FAST",
        update=_preset_update,
    )

    frame_range_mode: EnumProperty(
        name="Frame Range",
        description="Choose which clip frames CV Auto Track should process",
        items=[
            ("SCENE", "Scene Range", ""),
            ("PREVIEW", "Preview Range", ""),
            ("CLIP", "Clip Full Range", ""),
            ("CURRENT_TO_END", "Current to End", ""),
            ("START_TO_CURRENT", "Start to Current", ""),
            ("CUSTOM", "Custom Range", ""),
        ],
        default="CLIP",
    )
    custom_start_frame: IntProperty(name="Start Frame", description="First clip frame for Custom Range", default=1, min=1)
    custom_end_frame: IntProperty(name="End Frame", description="Last clip frame for Custom Range", default=100, min=1)
    tracking_direction: EnumProperty(
        name="Direction",
        description="Choose how tracking passes run through the frame range",
        items=[
            ("FORWARD", "Forward", "Track forward from the range start"),
            ("BACKWARD", "Backward", "Track backward from the range end"),
            ("BOTH", "Both", "Track forward and backward from the range middle"),
            ("CURRENT", "Current", "Track forward and backward from the current frame"),
            ("AUTO", "Auto", "Run separate forward and backward passes"),
        ],
        default="AUTO",
    )
    analysis_scale: EnumProperty(
        name="Analysis Scale",
        description="Temporary analysis resolution used by detection and optical flow",
        items=[("100", "Full", ""), ("75", "75%", ""), ("50", "50%", ""), ("25", "25%", "")],
        default="25",
    )
    detector_type: EnumProperty(
        name="Detector",
        description="Feature detector used before Lucas-Kanade tracking",
        items=[
            ("SHI_TOMASI", "Shi-Tomasi", "Fast corner detector; default and usually best for LK tracking"),
            ("SIFT", "SIFT", "Scale-invariant features; slower and experimental for tracking seeds"),
            ("ORB", "ORB", "Fast oriented binary features; experimental tracking seeds"),
            ("FAST", "FAST", "Very fast corner detector; experimental tracking seeds"),
        ],
        default="SHI_TOMASI",
    )
    auto_scale_pixel_parameters: BoolProperty(
        name="Auto Scale Pixel Parameters",
        description="Scale pixel-based thresholds from the effective analysis resolution so presets behave more consistently across footage resolutions",
        default=True,
    )
    minimum_analysis_width: IntProperty(name="Minimum Analysis Width", description="Minimum analysis width used when Analysis Scale would downsample small footage too far", default=1280, min=64, max=8192)
    minimum_analysis_height: IntProperty(name="Minimum Analysis Height", description="Minimum analysis height used when Analysis Scale would downsample small footage too far", default=720, min=64, max=8192)

    maximum_features: IntProperty(name="Maximum Features", description="Maximum feature points requested per detection pass", default=300, min=1, max=100000)
    quality_level: FloatProperty(name="Quality Level", description="Minimum feature quality or detector threshold", default=0.01, min=0.000001, max=1.0)
    minimum_distance: FloatProperty(name="Minimum Distance", description="Minimum spacing between detected feature points before automatic resolution scaling", default=12.0, min=1.0)
    block_size: IntProperty(name="Block Size", description="Feature-detection neighborhood size before automatic resolution scaling", default=7, min=3)
    use_harris_detector: BoolProperty(name="Use Harris Detector", description="Use Harris corner scoring instead of the default Shi-Tomasi score", default=False)
    harris_k: FloatProperty(name="Harris K", description="Harris detector free parameter", default=0.04, min=0.001, max=1.0)
    edge_margin: IntProperty(name="Edge Margin", description="Avoid detecting or tracking near the clip border before automatic resolution scaling", default=16, min=0)

    window_size: IntProperty(name="Window Size", description="Lucas-Kanade tracking window size before automatic resolution scaling", default=21, min=3)
    pyramid_levels: IntProperty(name="Pyramid Levels", description="Optical-flow pyramid levels for larger motion", default=3, min=0)
    termination_count: IntProperty(name="Termination Count", description="Maximum Lucas-Kanade iteration count", default=30, min=1)
    termination_epsilon: FloatProperty(name="Termination Epsilon", description="Lucas-Kanade convergence epsilon", default=0.01, min=0.000001)
    minimum_eigen_threshold: FloatProperty(name="Minimum Eigen Threshold", description="Minimum eigenvalue threshold for optical-flow tracking", default=0.0001, min=0.0)
    maximum_lk_error: FloatProperty(name="Maximum LK Error", description="Maximum Lucas-Kanade residual error before ending a track", default=50.0, min=0.0)
    maximum_motion: FloatProperty(name="Maximum Motion", description="Maximum per-frame motion before automatic resolution scaling", default=96.0, min=1.0)
    enable_forward_backward: BoolProperty(name="Forward-Backward Check", description="Reject tracks that do not return close to the source point", default=True)
    maximum_fb_error: FloatProperty(name="Maximum FB Error", description="Maximum forward-backward error before automatic resolution scaling", default=1.5, min=0.0)

    enable_redetect: BoolProperty(name="Periodic Redetect", description="Allow CV Auto Track to add new points during the pass", default=True)
    adaptive_redetect: BoolProperty(name="Adaptive Redetect", description="Add new points when active tracks or screen distribution fall below target", default=True)
    redetect_interval: IntProperty(name="Redetect Interval", description="Minimum frame gap between adaptive detection passes", default=15, min=1)
    minimum_active_tracks: IntProperty(name="Minimum Active Tracks", description="Target floor for active tracks before adaptive redetection", default=150, min=0)
    target_track_count: IntProperty(name="Target Active Tracks", description="Preferred number of live tracks maintained on each frame", default=200, min=1)
    auto_target_track_count: BoolProperty(name="Auto Target Tracks", description="Choose Target Active Tracks from the preset, analysis resolution, and grid settings", default=True)
    track_density_percent: FloatProperty(name="Track Density", description="Scale automatic target and baked track counts as a simple percentage", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE")
    auto_track_budget: BoolProperty(name="Auto Track Budget", description="Scale the internal generated-track budget from the frame range and redetect interval", default=True)
    maximum_total_tracks: IntProperty(name="Maximum Track Budget", description="Manual upper limit for generated CV Auto Track candidates when Auto Track Budget is off", default=1500, min=1)
    auto_bake_track_limit: BoolProperty(name="Auto Bake Limit", description="Limit final baked CV Auto Track tracks automatically before Blender solve", default=True)
    maximum_baked_tracks: IntProperty(name="Maximum Baked Tracks", description="Maximum enabled CV Auto Track tracks baked into Blender when Auto Bake Limit is off, or the automatic upper cap when it is on", default=800, min=8, max=5000)
    lead_edge_redetect: BoolProperty(name="Lead Edge Redetect", description="Bias adaptive redetection toward the screen edge where new image content is entering", default=True)
    detect_only_when_needed: BoolProperty(name="Detect Only When Needed", description="Skip scheduled detection when current tracks already satisfy the targets", default=True)

    enable_ransac: BoolProperty(name="RANSAC", description="Estimate a rough inlier rate for generated tracks", default=True)
    ransac_model: EnumProperty(
        name="RANSAC Model",
        description="Geometric model used for the quick inlier-rate report",
        items=[("FUNDAMENTAL", "Fundamental Matrix", ""), ("HOMOGRAPHY", "Homography", ""), ("AUTO", "Automatic", "")],
        default="FUNDAMENTAL",
    )
    ransac_threshold: FloatProperty(name="RANSAC Threshold", description="RANSAC reprojection threshold before automatic resolution scaling", default=2.0, min=0.0)
    ransac_confidence: FloatProperty(name="RANSAC Confidence", description="RANSAC confidence used for fundamental-matrix estimation", default=0.99, min=0.5, max=1.0)
    ransac_minimum_points: IntProperty(name="RANSAC Minimum Points", description="Minimum point count required before running RANSAC", default=12, min=4)
    filter_preset: EnumProperty(
        name="Filter Preset",
        description="Apply candidate and refine rejection thresholds without changing detection density",
        items=[
            ("LENIENT", "Lenient", "Keep more tracks; useful for fast motion or difficult footage"),
            ("STANDARD", "Standard", "Default balance for general footage"),
            ("STRICT", "Strict", "Reject more aggressively when dense coverage is available"),
            ("CUSTOM", "Custom", "Manually edited filter settings"),
        ],
        default="STANDARD",
        update=_filter_preset_update,
    )
    filter_preset_compact: EnumProperty(
        name="Filter",
        description="Apply common filter thresholds",
        items=[
            ("LENIENT", "Lenient", "Keep more tracks; useful for fast motion or difficult footage"),
            ("STANDARD", "Standard", "Default balance for general footage"),
            ("STRICT", "Strict", "Reject more aggressively when dense coverage is available"),
        ],
        default="STANDARD",
        update=_filter_preset_compact_update,
    )
    duplicate_distance: FloatProperty(name="Duplicate Distance", description="Disable duplicate tracks that start within this distance before automatic resolution scaling", default=6.0, min=0.0)
    minimum_track_length: IntProperty(name="Minimum Track Length", description="Minimum valid marker count for keeping a generated track", default=8, min=1)
    preferred_track_length: IntProperty(name="Preferred Track Length", description="Track length considered good when scoring candidates", default=20, min=1)
    minimum_valid_ratio: FloatProperty(name="Minimum Valid Ratio", description="Minimum tracked-frame ratio before a generated candidate is rejected", default=0.8, min=0.0, max=1.0)
    grid_columns: IntProperty(name="Grid Columns", description="Horizontal cells used for detection distribution", default=6, min=1)
    grid_rows: IntProperty(name="Grid Rows", description="Vertical cells used for detection distribution", default=4, min=1)
    maximum_tracks_per_cell: IntProperty(name="Maximum Tracks Per Cell", description="Maximum generated tracks kept per distribution cell", default=20, min=1)
    minimum_tracks_per_cell: IntProperty(name="Minimum Tracks Per Cell", description="Minimum active tracks requested per distribution cell", default=3, min=0)
    distribution_strength: FloatProperty(name="Distribution Strength", description="Strength of distribution-aware redetection and pruning", default=1.0, min=0.0, max=1.0)

    use_mask: BoolProperty(name="Use Mask", description="Constrain detection and tracking with a Blender Mask or external mask clip", default=False)
    mask_source: EnumProperty(
        name="Mask Source",
        description="Choose whether to use the Clip Editor mask or an external mask Movie Clip",
        items=[("BLENDER", "Blender Mask", ""), ("EXTERNAL", "External Image", "")],
        default="BLENDER",
    )
    tracking_mask: PointerProperty(name="Mask", description="Blender Mask datablock used when Mask Source is Blender Mask", type=bpy.types.Mask)
    external_mask_clip: PointerProperty(name="Mask Clip", description="External mask loaded as a Movie Clip", type=bpy.types.MovieClip)
    external_mask_channel: EnumProperty(
        name="External Channel",
        description="Use luma or alpha from the external mask clip",
        items=[("LUMA", "White/Luma", ""), ("ALPHA", "Alpha", "")],
        default="LUMA",
    )
    auto_solve_refine: BoolProperty(name="Auto Solve & Refine", description="Run solve refinement after Full Auto Track", default=True)
    auto_solve_keyframes: BoolProperty(name="Auto Keyframe A/B", description="Choose stable solve keyframes automatically and disable Blender Keyframe Selection", default=True)
    bake_pattern_size: IntProperty(name="Bake Pattern Size", description="Pattern Area size used when baking generated Blender markers", default=15, min=1, max=512)
    bake_search_size: IntProperty(name="Bake Search Size", description="Search Area size used when baking generated Blender markers", default=30, min=1, max=1024)
    mask_mode: EnumProperty(
        name="Mask Meaning",
        description="Choose whether white mask pixels are excluded or used as the allowed tracking area",
        items=[("EXCLUDE_WHITE", "White Area to Exclude", ""), ("INCLUDE_WHITE", "White Area to Track", "")],
        default="EXCLUDE_WHITE",
    )
    mask_margin: IntProperty(name="Mask Edge Margin", description="Shrink the allowed tracking area near mask edges in full-resolution pixels", default=8, min=0, max=512)
    track_replace_mode: EnumProperty(
        name="AT Mode",
        description="Choose how Detect & Track and Full Auto Track handle existing CV Auto Track-generated tracks",
        items=[
            ("AUTO_REUSE", "Auto Reuse", "Detect & Track replaces existing AT tracks; Full Auto reuses them and skips detect/track"),
            ("REPLACE_AUTOTRACK", "Replace", "Delete existing AT tracks before detect/track"),
            ("ADD", "Add New", "Keep existing AT tracks and add newly generated tracks"),
        ],
        default="AUTO_REUSE",
    )
    preserve_existing_tracks: BoolProperty(name="Preserve Existing Tracks", description="Avoid cleaning or replacing non-CV Auto Track tracks", default=True)
    use_existing_tracks_as_exclusion_points: BoolProperty(name="Use Existing Tracks as Exclusion", description="Avoid placing new features on top of existing tracks", default=True)

    maximum_track_error: FloatProperty(name="Maximum Track Error", description="Absolute bundle-error threshold used by solve refinement", default=2.0, min=0.0)
    mad_multiplier: FloatProperty(name="MAD Multiplier", description="Median absolute deviation multiplier for adaptive outlier selection", default=3.0, min=0.0)
    outlier_percentage_per_iteration: FloatProperty(name="Outlier % Per Iteration", description="Maximum percentage of enabled tracks refined per iteration", default=5.0, min=0.1, max=100.0)
    maximum_disabled_per_iteration: IntProperty(name="Max Rejected Per Iteration", description="Maximum tracks rejected in one refinement iteration", default=25, min=1)
    minimum_remaining_tracks: IntProperty(name="Minimum Remaining Tracks", description="Minimum enabled track count preserved during refinement", default=20, min=1)
    delete_refined_tracks: BoolProperty(name="Delete Refined Tracks", description="Delete tracks rejected by solve refinement instead of only muting them", default=True)
    refine_motion_outliers: BoolProperty(name="Refine Motion Outliers", description="Reject tracks whose motion is inconsistent with the frame-to-frame median motion", default=True)
    motion_outlier_multiplier: FloatProperty(name="Motion MAD Multiplier", description="Median absolute deviation multiplier for motion outlier detection", default=4.0, min=0.0)
    motion_outlier_min_residual: FloatProperty(name="Motion Min Residual", description="Minimum pixel residual before motion outlier detection can reject a track", default=20.0, min=0.0)
    motion_outlier_min_ratio: FloatProperty(name="Motion Min Ratio", description="Minimum ratio of bad motion steps before a track is rejected", default=0.35, min=0.0, max=1.0)
    motion_outlier_local_radius: FloatProperty(name="Motion Local Radius", description="Pixel radius used to compare each track against nearby track motion", default=180.0, min=1.0)
    motion_outlier_local_min_tracks: IntProperty(name="Motion Local Min Tracks", description="Minimum nearby tracks needed for local motion outlier detection", default=6, min=3)
    full_auto_refine_iterations: IntProperty(name="Full Auto Refine Passes", description="Maximum solve-refine passes after Full Auto Track", default=2, min=1)
    maximum_refine_iterations: IntProperty(name="Maximum Iterations", description="Maximum iterations for manual Solve & Refine", default=3, min=1)
    target_solve_error: FloatProperty(name="Target Solve Error", description="Stop refining once the solve error reaches this value", default=0.5, min=0.0)
    minimum_solve_improvement: FloatProperty(name="Minimum Improvement", description="Stop refining when solve-error improvement falls below this value", default=0.02, min=0.0)
    protect_selected_tracks: BoolProperty(name="Protect Selected Tracks", description="Never refine tracks currently selected by the user", default=True)
    protect_existing_tracks: BoolProperty(name="Protect Existing Tracks", description="Never refine non-CV Auto Track tracks", default=True)
    protected_name_prefix: StringProperty(name="Protected Name Prefix", description="Track-name prefix that prevents solve refinement from changing a track", default=PROTECTED_PREFIX)
    analyze_solve_action: EnumProperty(
        name="Analyze Action",
        description="Choose what Analyze Solve does with detected outlier tracks",
        items=[("SELECT", "Select Outliers", ""), ("DISABLE", "Disable Outliers", ""), ("REPORT", "Report Only", "")],
        default="SELECT",
    )

    cache_size: IntProperty(name="Frame Cache Size", description="Number of decoded analysis frames kept in memory", default=8, min=2, max=128)
    cancel_requested: BoolProperty(name="Cancel Requested", description="Internal cancellation flag", default=False, options={"HIDDEN"})
    is_running: BoolProperty(name="Running", description="Internal running-state flag", default=False, options={"HIDDEN"})
    status_message: StringProperty(name="Status", description="Current CV Auto Track status message", default="Idle")
    results_text: StringProperty(name="Results", description="Latest CV Auto Track result summary", default="")


classes = (CV_AUTOTRACK_PG_Settings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cv_autotrack = PointerProperty(type=CV_AUTOTRACK_PG_Settings)


def unregister():
    if hasattr(bpy.types.Scene, "cv_autotrack"):
        del bpy.types.Scene.cv_autotrack
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
