# CV Auto Track


try:
    from . import operators, panels, preferences, properties, translations
except ModuleNotFoundError as exc:
    if exc.name != "bpy":
        raise
    _MODULES = ()
else:
    _MODULES = (translations, preferences, properties, operators, panels)


def register():
    if not _MODULES:
        raise RuntimeError("CV Auto Track can only be registered inside Blender.")
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()
