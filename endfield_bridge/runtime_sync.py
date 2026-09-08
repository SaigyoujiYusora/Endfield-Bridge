"""Scene-local synchronization without rewriting unchanged GPU resources.

The vendor remains the authority for light selection and packing. This module
only skips its refresh when every consumed source value and table identity match.
"""
import bpy
import mathutils

_light_state = None
_rig_bones = {}


def reset():
    global _light_state
    _light_state = None
    _rig_bones.clear()


def refresh_lights(runtime, scene):
    global _light_state
    if scene != bpy.context.scene:
        return 0  # Vendor selection/visibility uses the current view layer.
    sources = []
    for obj in scene.objects:
        if obj.type != 'LIGHT':
            continue
        data = obj.data
        sources.append((obj.as_pointer(), obj.name, runtime._light_visible(obj),
                        data.type, tuple(v for row in obj.matrix_world for v in row),
                        float(data.energy), tuple(data.color),
                        float(getattr(data, 'diffuse_factor', 1.0)),
                        float(getattr(data, 'angle', 0.0)),
                        float(getattr(data, 'spot_size', 0.0)),
                        float(getattr(data, 'spot_blend', 0.0))))
    # A recreated/deleted/localized table must never inherit a Python cache hit.
    def tables():
        return tuple((im.as_pointer(), tuple(im.size), im.is_float, im.has_data,
                      im.library.as_pointer() if im.library else 0)
                     for im in bpy.data.images
                     if runtime._table_base_name(im.name) == runtime._table_base_name(runtime.LIGHT_TABLE))
    source = (scene.as_pointer(), bpy.context.view_layer.as_pointer(), tuple(sources))
    state = (source, tables())
    if _light_state == state and state[1]:
        return 0
    result = runtime.refresh_light_tables()
    _light_state = (source, tables())
    return result


def _different(target, key, value):
    previous = target.get(key)
    return (previous is None or len(previous) != 3
            or any(abs(previous[k] - value[k]) > 1e-6 for k in range(3)))


def push_rig_basis(runtime, stacks, scene, depsgraph=None):
    if scene != bpy.context.scene:
        return
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    axes = tuple(mathutils.Vector(axis) for axis in ((1, 0, 0), (0, 0, 1), (0, 1, 0)))
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.data is None:
            continue
        match = next(((stack, mat) for stack in stacks if stack.post is None
                      for mat in obj.data.materials if mat is not None
                      and mat.get('ruri_uber_stack') == stack.PANEL_KEY
                      and mat.get('ruri_uber_part') in (stack.RIG.get('parts') or [])), None)
        if match is None:
            continue
        stack, mat = match
        arm = stack._rig_basis_armature(obj)
        if arm is None:
            continue
        source = stack.rig_bone_of(mat)
        key = (obj.as_pointer(), arm.as_pointer(), source)
        bone = _rig_bones.get(key)
        try:
            valid = bone is not None and arm.data.bones.get(bone.name) == bone
        except ReferenceError:
            valid = False
        if not valid:
            bone = arm.data.bones.get(stack.rig_resolve_bone(arm, source))
            if bone is None:
                continue
            _rig_bones[key] = bone
        pose = arm.evaluated_get(depsgraph).pose.bones.get(bone.name)
        if pose is None:
            continue
        delta = pose.matrix.to_3x3() @ pose.bone.matrix_local.to_3x3().inverted()
        evaluated = obj.evaluated_get(depsgraph)
        for index, axis in enumerate(axes):
            vector = (delta @ axis).normalized()
            value = (vector.x, vector.z, vector.y)
            prop = runtime.RIG_OBJECT_PROP + str(index)
            # The current frame reads the evaluated copy; the original seeds
            # future evaluations. Compare each independently to avoid a frame lag.
            if _different(evaluated, prop, value):
                evaluated[prop] = value
            if _different(obj, prop, value):
                obj[prop] = value
