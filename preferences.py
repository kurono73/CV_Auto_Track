from __future__ import annotations

import bpy

from .constants import ADDON_ID
from .dependencies import dependency_status


class CV_AUTOTRACK_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    def draw(self, _context):
        layout = self.layout
        ok, message = dependency_status()
        icon = "CHECKMARK" if ok else "ERROR"
        layout.label(text=message, icon=icon)
        layout.label(text="Bundled wheels are loaded from the add-on wheels folder when present.")


classes = (CV_AUTOTRACK_AddonPreferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
