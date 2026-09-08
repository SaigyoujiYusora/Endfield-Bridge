"""Instance-owned authored scalar face mappings through the ENDF material API."""
import json
import math
import uuid

import bpy
from bpy.app.handlers import persistent

ENABLED = 'sora_face_shader_enabled'
SNAPSHOT = 'sora_face_shader_restore'
TARGETS = 'sora_face_shader_targets'
ERROR = 'sora_face_shader_error'
_last = {}
_busy = False


def mappings(rig):
    from .face_controls import DATA, descriptor
    result = []
    for index, control in enumerate(descriptor(rig[DATA])['controls']):
        shader = control.get('shader')
        if shader is None:
            continue
        supported = (shader['parameter'] in {'_EmotionBlend', '_EmotionIndex'}
                     and shader['rendererMask'] == -1 and shader['defaultValue'] == 0
                     and shader['blendMode'] == 0 and shader['vectorIndex'] == 0
                     and control['partType'] == 32)
        if not supported:
            raise ValueError('Unsupported authored shader mapping: ' + control['name'])
        result.append((index, shader['parameter']))
    if not result:
        raise ValueError('This character has no authored shader controls')
    if len({parameter for _, parameter in result}) != len(result):
        raise ValueError('Multiple face controls target the same shader parameter')
    return result


def targets(rig, controls):
    from .material_panel import stack_for, groups_for, editable
    token = rig.get('sora_instance')
    if not token:
        raise ValueError('Shader controls require an owned import instance')
    result = []
    reached = set()
    for material in bpy.data.materials:
        if material.get('sora_instance') != token:
            continue
        stack = stack_for(material)
        if stack is None:
            continue
        rows = {r['name']: r for g in groups_for(stack, material) for r in g['rows']}
        matches = [(i, rows[p]) for i, p in controls if p in rows]
        if not matches:
            continue
        if not editable(material):
            raise ValueError('Shader target is not editable: ' + material.name)
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and material in list(obj.data.materials):
                if obj.get('sora_instance') != token and obj.get('sora_outline_owner') != token:
                    raise ValueError('Shader material is shared outside this import: ' + material.name)
        for index, row in matches:
            if row['kind'] not in {'VALUE', 'SLIDER', 'INT'}:
                raise ValueError('Authored scalar mapping has a non-scalar target')
            result.append((material, stack, row, index))
            reached.add(row['name'])
    missing = [name for _, name in controls if name not in reached]
    if missing:
        raise ValueError('No owned ENDF material target for ' + ', '.join(missing))
    return result


def _read(material, stack, row):
    from .material_panel import value_of
    return float(value_of(stack, material, row, stack.panel_read(material)))


def _same(first, second):
    return math.isclose(first, second, rel_tol=1e-7, abs_tol=1e-7)


def set_enabled(rig, enabled):
    if bool(rig.get(ENABLED)) == bool(enabled):
        return
    token = rig.get('sora_instance')
    if enabled:
        rows = targets(rig, mappings(rig))
        snapshots = {}
        for material, stack, row, _ in rows:
            if SNAPSHOT in material:
                raise ValueError('Material already has an active face shader owner')
            snapshots.setdefault(material, {})[row['name']] = _read(material, stack, row)
        identities = []
        for material, values in snapshots.items():
            identity = uuid.uuid4().hex
            identities.append(identity)
            material[SNAPSHOT] = json.dumps({'owner': token, 'identity': identity, 'values': values})
        rig[TARGETS] = json.dumps(identities)
        rig[ENABLED] = True
        try:
            sync(rig)
        except Exception:
            set_enabled(rig, False)
            raise
    else:
        plan = restore_plan(rig)
        applied = []
        try:
            for material, stack, row, value, current in plan:
                if not _same(current, value):
                    applied.append((material, stack, row, current))
                    stack.panel_write(material, row, value)
        except Exception:
            # Retain every snapshot and enabled flag if an unexpected writer
            # error occurs; restore already-written rows to their pre-call value.
            for material, stack, row, current in reversed(applied):
                stack.panel_write(material, row, current)
            raise
        for material in {item[0] for item in plan}:
            del material[SNAPSHOT]
        del rig[TARGETS]
        rig[ENABLED] = False
    _last.pop(rig.as_pointer(), None)
    if ERROR in rig:
        del rig[ERROR]


def restore_plan(rig):
    """Read-only preflight of the complete persistent restoration set."""
    from .material_panel import resolve, editable
    token = rig.get('sora_instance')
    identities = json.loads(rig.get(TARGETS, '[]'))
    if not identities or len(set(identities)) != len(identities):
        raise ValueError('Shader restoration registry is missing or invalid; state retained')
    found = {}
    for material in bpy.data.materials:
        raw = material.get(SNAPSHOT)
        if not raw:
            continue
        state = json.loads(raw)
        if state.get('owner') == token:
            identity = state.get('identity')
            if identity not in identities or identity in found:
                raise ValueError('Shader snapshot identity changed; state retained')
            found[identity] = (material, state)
    if set(found) != set(identities):
        raise ValueError('A shader restoration target is missing; state retained')
    plan = []
    for identity in identities:
        material, state = found[identity]
        if material.get('sora_instance') != token or not editable(material):
            raise ValueError('Shader restoration target is no longer owned and editable')
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and material in list(obj.data.materials):
                if obj.get('sora_instance') != token and obj.get('sora_outline_owner') != token:
                    raise ValueError('Shader restoration target is shared outside this import')
        for name, value in state['values'].items():
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Invalid saved shader value')
            resolved, stack, row = resolve(material.name, name)
            if resolved != material or row['kind'] not in {'VALUE', 'SLIDER', 'INT'}:
                raise ValueError('Shader restoration row changed')
            plan.append((material, stack, row, value, _read(material, stack, row)))
    return plan


def sync(rig, depsgraph=None):
    if not rig.get(ENABLED):
        return 0
    from .face_controls import property_name, curves
    controls = mappings(rig)
    # An evaluated Object can retain an older copy of an unanimated IDProperty
    # while a direct edit has already reached its original. Reading that stale
    # copy in depsgraph_update_post would undo the edit and cause alternating
    # table writes. Only animation-owned property paths need evaluated values.
    animation = getattr(rig, 'animation_data', None)
    animated = set()
    if animation is not None:
        animated.update(curve.data_path for curve in animation.drivers if not curve.mute)
        actions = [animation.action] + [strip.action for track in animation.nla_tracks
                    if not track.mute for strip in track.strips if not strip.mute]
        animated.update(curve.data_path for action in actions for curve in curves(action) if not curve.mute)
    animated.intersection_update('["' + property_name(i) + '"]' for i, _ in controls)
    evaluated = rig.evaluated_get(depsgraph or bpy.context.evaluated_depsgraph_get()) if animated else rig
    weights = tuple(float((evaluated if '["' + property_name(i) + '"]' in animated else rig).get(property_name(i), rig.get(property_name(i), 0))) for i, _ in controls)
    if not all(math.isfinite(v) for v in weights):
        raise ValueError('Shader face weight must be finite')
    values = {index: value for (index, _), value in zip(controls, weights)}
    changed = 0
    plan = []
    for material, stack, row, index in targets(rig, controls):
        state = json.loads(material.get(SNAPSHOT, '{}'))
        if state.get('owner') != rig.get('sora_instance') or row['name'] not in state.get('values', {}):
            raise ValueError('Shader targets changed; disable and enable shader controls again')
        value = values[index]
        plan.append((material, stack, row, value, _read(material, stack, row)))
    # Include current rows in the comparison: active Face ownership deliberately
    # reapplies its value after an external parameter edit, even at fixed weights.
    signature = (weights, tuple((m.as_pointer(), row['name'], current) for m, _, row, _, current in plan))
    if _last.get(rig.as_pointer()) == signature:
        return 0
    for material, stack, row, value, current in plan:
        if not _same(current, value):
            # EndfStack.panel_write updates the EEVEE CPU/GPU table and its
            # overridden _param_write projects the same row to Cycles nodes.
            stack.panel_write(material, row, value)
            changed += 1
    _last[rig.as_pointer()] = (weights, tuple((m.as_pointer(), row['name'], _read(m, stack, row)) for m, stack, row, _, _ in plan))
    return changed


@persistent
def update(scene, depsgraph=None):
    global _busy
    if _busy or scene != bpy.context.scene:
        return
    _busy = True
    try:
        for rig in scene.objects:
            if rig.type != 'ARMATURE' or not rig.get(ENABLED):
                continue
            try:
                sync(rig, depsgraph)
            except (ValueError, RuntimeError, KeyError, TypeError) as error:
                message = str(error)
                if rig.get(ERROR) != message:
                    rig[ERROR] = message
    finally:
        _busy = False


@persistent
def loaded(_):
    _last.clear()


class SORA_OT_face_shader_toggle(bpy.types.Operator):
    bl_idname = 'sora.face_shader_toggle'
    bl_label = 'Enable / Disable Shader Face Controls'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .face_controls import rig_for
        rig = rig_for(context.object)
        if rig is None:
            return {'CANCELLED'}
        try:
            set_enabled(rig, not rig.get(ENABLED, False))
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def register():
    bpy.utils.register_class(SORA_OT_face_shader_toggle)
    for handlers, function in ((bpy.app.handlers.depsgraph_update_post, update),
                               (bpy.app.handlers.frame_change_post, update),
                               (bpy.app.handlers.load_post, loaded)):
        if function not in handlers:
            handlers.append(function)


def unregister():
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE' and obj.get(ENABLED):
            set_enabled(obj, False)
    for handlers, function in ((bpy.app.handlers.depsgraph_update_post, update),
                               (bpy.app.handlers.frame_change_post, update),
                               (bpy.app.handlers.load_post, loaded)):
        if function in handlers:
            handlers.remove(function)
    _last.clear()
    bpy.utils.unregister_class(SORA_OT_face_shader_toggle)
