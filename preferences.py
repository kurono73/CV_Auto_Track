from __future__ import annotations

import bpy

from .constants import ADDON_ID


def _opencv_status() -> tuple[bool, str]:
    try:
        import cv2
    except Exception as exc:
        return False, f"OpenCV import failed: {type(exc).__name__}: {exc}"
    return True, f"OpenCV {cv2.__version__}"


class CV_AUTOTRACK_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    def draw(self, _context):
        layout = self.layout
        ok, message = _opencv_status()
        icon = "CHECKMARK" if ok else "ERROR"
        layout.label(text=message, icon=icon)
        layout.label(text="Python wheels are registered by Blender from the extension manifest.")


classes = (CV_AUTOTRACK_AddonPreferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
