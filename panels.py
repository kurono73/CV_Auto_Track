from __future__ import annotations

import bpy


def _available_icon(name: str, fallback: str = "SHADERFX") -> str:
    try:
        icons = bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items
        if name in icons:
            return name
    except Exception:
        pass
    return fallback


def _active_clip(context):
    space = getattr(context, "space_data", None)
    if space and space.type == "CLIP_EDITOR" and space.clip:
        return space.clip
    return None


def _has_active_clip(context) -> bool:
    return _active_clip(context) is not None


def _active_mask(context):
    space = getattr(context, "space_data", None)
    if space and space.type == "CLIP_EDITOR":
        return getattr(space, "mask", None)
    return None


def _draw_refine_intrinsics(layout, clip):
    if clip is None:
        return
    settings = clip.tracking.settings
    row = layout.row(align=True)
    row.prop(settings, "refine_intrinsics_focal_length", text="Focal")
    row.prop(settings, "refine_intrinsics_principal_point", text="Optical Center")
    row = layout.row(align=True)
    row.prop(settings, "refine_intrinsics_radial_distortion", text="Radial")
    row.prop(settings, "refine_intrinsics_tangential_distortion", text="Tangential")


def _labeled_prop(layout, data, prop_name: str, label: str, factor: float = 0.34, icon: str = "NONE"):
    row = layout.split(factor=factor, align=True)
    row.label(text=label, icon=icon)
    row.prop(data, prop_name, text="")


class CV_AUTOTRACK_MT_presets(bpy.types.Menu):
    bl_label = "CV Auto Track Presets"
    bl_idname = "CV_AUTOTRACK_MT_presets"
    preset_subdir = "cv_autotrack"
    preset_operator = "script.execute_preset"
    draw = bpy.types.Menu.draw_preset


class CV_AUTOTRACK_PT_presets(bpy.types.Panel):
    bl_label = "CV Auto Track Presets"
    bl_idname = "CV_AUTOTRACK_PT_presets"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "HEADER"
    preset_subdir = "cv_autotrack"
    preset_operator = "script.execute_preset"
    preset_add_operator = "clip.cv_autotrack_preset_add"
    path_menu = bpy.types.Menu.path_menu

    def draw(self, context):
        layout = self.layout
        layout.emboss = "PULLDOWN_MENU"
        layout.operator_context = "EXEC_DEFAULT"
        bpy.types.Menu.draw_preset(self, context)


class CV_AUTOTRACK_PT_main(bpy.types.Panel):
    bl_label = "CV Auto Track"
    bl_idname = "CV_AUTOTRACK_PT_main"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context)

    def draw_header_preset(self, _context):
        layout = self.layout
        layout.emboss = "NONE"
        layout.popover(panel="CV_AUTOTRACK_PT_presets", icon="PRESET", text="")

    def draw(self, context):
        layout = self.layout
        props = context.scene.cv_autotrack
        _labeled_prop(layout, props, "preset", "Preset", factor=0.28, icon="SEQUENCE")
        layout.prop(props, "advanced_mode", text="Advanced")
        layout.operator("clip.cv_autotrack_solve_setup_dialog", text="Solve Setup", icon="SETTINGS", translate=False)
        layout.prop(props, "track_density_percent", text="Density", slider=True)
        layout.separator()
        box = layout.box()
        box.label(text="Track", icon="TRACKING")
        row = box.row(align=True)
        if props.is_running:
            row.operator("clip.cv_autotrack_cancel", text="Cancel", icon="CANCEL")
        else:
            row.operator("clip.cv_autotrack_detect_track", text="Generate Tracks", icon="LIGHTPROBE_VOLUME")
        row.operator("clip.cv_autotrack_add_cached_tracks", text="", icon="ADD")
        row = box.row(align=True)
        row.scale_y = 1.8
        row.operator("clip.cv_autotrack_full_auto_track", text="Run Auto Track", icon="PLAY")
        refine_icon = _available_icon("GESTURE_ROTATE") if props.auto_solve_refine else "RADIOBUT_OFF"
        row.operator("clip.cv_autotrack_toggle_auto_refine", text="", icon=refine_icon)
        _labeled_prop(box, props, "track_replace_mode", "Mode", factor=0.28)
        box = layout.box()
        box.label(text="Refine", icon="MODIFIER")
        row = box.row(align=True)
        row.operator("clip.cv_autotrack_solve_camera", text="Solve", icon="TRACKER", translate=False)
        if not props.advanced_mode:
            _labeled_prop(box, props, "filter_preset_compact", "Filter", factor=0.28)
        row = box.row(align=True)
        row.operator("clip.cv_autotrack_solve_refine", text="Solve & Refine", icon=_available_icon("GESTURE_ROTATE"), translate=False)
        row = box.row(align=True)
        row.operator("clip.cv_autotrack_analyze_solve", text="Analyze Solve", icon="VIEWZOOM", translate=False)
        _draw_refine_intrinsics(box, _active_clip(context))
        layout.operator("clip.cv_autotrack_restore_previous_state", icon="LOOP_BACK")
        layout.label(text=props.status_message)


class CV_AUTOTRACK_PT_refine_settings(bpy.types.Panel):
    bl_label = "Refine Settings"
    bl_idname = "CV_AUTOTRACK_PT_refine_settings"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 60

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context) and bool(context.scene.cv_autotrack.advanced_mode)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        layout.prop(props, "delete_refined_tracks", text="Delete Refined")
        layout.prop(props, "analyze_solve_action", text="Analyze")
        layout.prop(props, "maximum_refine_iterations", text="Max Iter")
        layout.prop(props, "target_solve_error", text="Target Error")
        layout.prop(props, "minimum_solve_improvement", text="Min Improve")
        layout.separator()
        layout.prop(props, "protect_selected_tracks", text="Protect Selected")
        layout.prop(props, "protect_existing_tracks", text="Protect Existing")
        layout.prop(props, "protected_name_prefix", text="Protect Prefix")


class CV_AUTOTRACK_PT_input(bpy.types.Panel):
    bl_label = "Track Setup"
    bl_idname = "CV_AUTOTRACK_PT_input"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context)

    def draw(self, context):
        layout = self.layout
        props = context.scene.cv_autotrack
        _labeled_prop(layout, props, "frame_range_mode", "Range", factor=0.34)
        if props.frame_range_mode == "CUSTOM":
            row = layout.row(align=True)
            row.prop(props, "custom_start_frame", text="Start")
            row.prop(props, "custom_end_frame", text="End")
        _labeled_prop(layout, props, "tracking_direction", "Direction", factor=0.34)
        _labeled_prop(layout, props, "analysis_scale", "Scale", factor=0.34)
        if props.advanced_mode:
            layout.prop(props, "auto_scale_pixel_parameters", text="Auto Scale Px")
            row = layout.row(align=True)
            row.prop(props, "minimum_analysis_width", text="Min W")
            row.prop(props, "minimum_analysis_height", text="Min H")
            layout.prop(props, "cache_size", text="Frame Cache")
        layout.prop(props, "use_mask", text="Use Mask", icon="MOD_MASK", toggle=True)
        if props.use_mask:
            layout.prop(props, "mask_source", text="Source")
            if props.mask_source == "EXTERNAL":
                row = layout.row(align=True)
                row.prop(props, "external_mask_clip", text="Mask Clip")
                row.operator("clip.cv_autotrack_load_external_mask_clip", text="", icon="FILE_FOLDER")
                row.operator("clip.cv_autotrack_sync_external_mask_clip", text="", icon="FILE_REFRESH")
                layout.prop(props, "external_mask_channel", text="Channel")
                clip = _active_clip(context)
                if clip is not None and props.external_mask_clip is not None:
                    clip_duration = int(getattr(clip, "frame_duration", 0))
                    mask_duration = int(getattr(props.external_mask_clip, "frame_duration", 0))
                    if clip_duration and mask_duration and clip_duration != mask_duration:
                        layout.label(text=f"Mask length differs: {mask_duration} / {clip_duration}", icon="ERROR")
            else:
                layout.prop(props, "tracking_mask")
                if props.tracking_mask is None:
                    mask = _active_mask(context)
                    layout.label(text=f"Editor Mask: {mask.name if mask else 'None'}")
            layout.prop(props, "mask_mode", text="Mask")
            layout.prop(props, "mask_margin", text="Mask Margin")


class CV_AUTOTRACK_PT_detection(bpy.types.Panel):
    bl_label = "Detection"
    bl_idname = "CV_AUTOTRACK_PT_detection"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 30

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context) and bool(context.scene.cv_autotrack.advanced_mode)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        layout.prop(props, "detector_type", text="Detector")
        layout.prop(props, "maximum_features", text="Max Features")
        layout.prop(props, "quality_level", text="Quality")
        layout.prop(props, "minimum_distance", text="Min Distance")
        layout.prop(props, "block_size", text="Block")
        layout.prop(props, "edge_margin", text="Edge Margin")
        if props.detector_type == "SHI_TOMASI":
            layout.prop(props, "use_harris_detector", text="Harris")
        if props.detector_type == "SHI_TOMASI" and props.use_harris_detector:
            layout.prop(props, "harris_k")


class CV_AUTOTRACK_PT_tracking(bpy.types.Panel):
    bl_label = "Tracking"
    bl_idname = "CV_AUTOTRACK_PT_tracking"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 40

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context) and bool(context.scene.cv_autotrack.advanced_mode)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        layout.prop(props, "window_size", text="Window")
        layout.prop(props, "pyramid_levels", text="Pyramid")
        layout.prop(props, "termination_count", text="Iterations")
        layout.prop(props, "termination_epsilon", text="Epsilon")
        layout.prop(props, "minimum_eigen_threshold", text="Eigen Thresh")
        layout.prop(props, "maximum_lk_error", text="Max LK Error")
        layout.prop(props, "maximum_motion", text="Max Motion")
        layout.prop(props, "enable_forward_backward", text="FB Check")
        if props.enable_forward_backward:
            layout.prop(props, "maximum_fb_error", text="Max FB Error")


class CV_AUTOTRACK_PT_filtering(bpy.types.Panel):
    bl_label = "Filtering"
    bl_idname = "CV_AUTOTRACK_PT_filtering"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 50

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context) and bool(context.scene.cv_autotrack.advanced_mode)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        layout.operator("clip.cv_autotrack_clean_tracks", icon="FILTER")
        layout.prop(props, "filter_preset", text="Preset")
        layout.prop(props, "minimum_track_length", text="Min Length")
        layout.prop(props, "preferred_track_length", text="Preferred Len")
        layout.prop(props, "minimum_valid_ratio", text="Valid Ratio")
        layout.prop(props, "duplicate_distance", text="Duplicate Dist")
        layout.prop(props, "maximum_track_error", text="Max Track Error")
        layout.prop(props, "mad_multiplier", text="MAD")
        layout.prop(props, "outlier_percentage_per_iteration", text="Outlier %")
        layout.prop(props, "maximum_disabled_per_iteration", text="Max Reject")
        layout.prop(props, "minimum_remaining_tracks", text="Min Remain")
        layout.prop(props, "refine_motion_outliers", text="Motion Filter")
        if props.refine_motion_outliers:
            layout.prop(props, "motion_outlier_multiplier", text="Motion MAD")
            layout.prop(props, "motion_outlier_min_residual", text="Motion Residual")
            layout.prop(props, "motion_outlier_min_ratio", text="Motion Ratio")
            layout.prop(props, "motion_outlier_local_radius", text="Local Radius")
            layout.prop(props, "motion_outlier_local_min_tracks", text="Local Min")
        layout.prop(props, "enable_ransac")
        if props.enable_ransac:
            layout.prop(props, "ransac_model", text="Model")
            layout.prop(props, "ransac_threshold", text="Threshold")
            layout.prop(props, "ransac_confidence", text="Confidence")
            layout.prop(props, "ransac_minimum_points", text="Min Points")


class CV_AUTOTRACK_PT_distribution(bpy.types.Panel):
    bl_label = "Distribution"
    bl_idname = "CV_AUTOTRACK_PT_distribution"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 20

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context) and bool(context.scene.cv_autotrack.advanced_mode)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        row = layout.row(align=True)
        row.prop(props, "grid_columns", text="Cols")
        row.prop(props, "grid_rows", text="Rows")
        layout.prop(props, "maximum_tracks_per_cell", text="Max / Cell")
        layout.prop(props, "minimum_tracks_per_cell", text="Min / Cell")
        layout.prop(props, "distribution_strength", text="Strength")
        layout.prop(props, "enable_redetect", text="Redetect")
        if props.enable_redetect:
            layout.prop(props, "adaptive_redetect", text="Adaptive")
            layout.prop(props, "redetect_interval", text="Interval")
            layout.prop(props, "auto_target_track_count", text="Auto Target")
            target_row = layout.row(align=True)
            target_row.enabled = not bool(props.auto_target_track_count)
            target_row.prop(props, "target_track_count", text="Target")
            target_row.prop(props, "minimum_active_tracks", text="Min Active")
            layout.prop(props, "auto_track_budget", text="Auto Budget")
            if not props.auto_track_budget:
                layout.prop(props, "maximum_total_tracks", text="Max Budget")
            layout.prop(props, "auto_bake_track_limit", text="Auto Bake Limit")
            bake_row = layout.row(align=True)
            bake_row.enabled = not bool(props.auto_bake_track_limit)
            bake_row.prop(props, "maximum_baked_tracks", text="Max Baked")
            layout.prop(props, "lead_edge_redetect", text="Lead Edge")
            layout.prop(props, "detect_only_when_needed", text="Only When Needed")


class CV_AUTOTRACK_PT_output(bpy.types.Panel):
    bl_label = "Existing Tracks"
    bl_idname = "CV_AUTOTRACK_PT_output"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 70

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context) and bool(context.scene.cv_autotrack.advanced_mode)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        layout.label(text=f"Mode: {props.bl_rna.properties['track_replace_mode'].enum_items[props.track_replace_mode].name}")
        layout.prop(props, "preserve_existing_tracks", text="Preserve User")
        layout.prop(props, "use_existing_tracks_as_exclusion_points", text="Use User Exclusion")


class CV_AUTOTRACK_PT_results(bpy.types.Panel):
    bl_label = "Results"
    bl_idname = "CV_AUTOTRACK_PT_results"
    bl_space_type = "CLIP_EDITOR"
    bl_region_type = "TOOLS"
    bl_category = "CV  Auto Track"
    bl_parent_id = "CV_AUTOTRACK_PT_main"
    bl_order = 80

    @classmethod
    def poll(cls, context):
        return _has_active_clip(context)

    def draw(self, context):
        props = context.scene.cv_autotrack
        layout = self.layout
        if props.results_text:
            for line in props.results_text.splitlines():
                layout.label(text=line)
        else:
            layout.label(text="No results yet.")


classes = (
    CV_AUTOTRACK_MT_presets,
    CV_AUTOTRACK_PT_presets,
    CV_AUTOTRACK_PT_main,
    CV_AUTOTRACK_PT_input,
    CV_AUTOTRACK_PT_distribution,
    CV_AUTOTRACK_PT_detection,
    CV_AUTOTRACK_PT_tracking,
    CV_AUTOTRACK_PT_filtering,
    CV_AUTOTRACK_PT_refine_settings,
    CV_AUTOTRACK_PT_output,
    CV_AUTOTRACK_PT_results,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
