"""Authored face DTO consumer. Native resources are decoded only by Sora-Core."""
import functools
import json
import math
import uuid
from collections import OrderedDict

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Quaternion, Vector

# A queued timer from the previous module instance must not survive hot reload.
_previous_rebind = globals().get('_load_rebind_timer')
if _previous_rebind is not None and bpy.app.timers.is_registered(_previous_rebind):
    bpy.app.timers.unregister(_previous_rebind)

DATA = 'sora_authored_face'
ENABLED = 'sora_authored_face_enabled'
STATE = 'sora_authored_face_restore'
REVISION = 'sora_authored_face_revision'
_rig_cache = OrderedDict()
_reflection = Matrix.Diagonal((-1, 1, 1, 1))


def rig_for(obj):
    if obj is None:
        return None
    if obj.type == 'ARMATURE' and DATA in obj:
        return obj
    if obj.parent and obj.parent.type == 'ARMATURE' and DATA in obj.parent:
        return obj.parent
    return None


def property_name(index):
    return 'sora_ctrl_' + str(index)


def install(rig, descriptor, bones):
    if DATA in rig:
        return
    payload = dict(descriptor)
    payload['sceneBoneNames'] = [b['name'] for b in bones]
    rig[DATA] = json.dumps(payload, separators=(',', ':'))
    rig[REVISION] = int(rig.get(REVISION, 0)) + 1
    invalidate(rig)
    rig[ENABLED] = False
    for i, control in enumerate(descriptor['controls']):
        prop = property_name(i)
        rig[prop] = 0.0
        rig.id_properties_ui(prop).update(soft_min=0.0, soft_max=1.0,
                                        description=control['name'])
    rig.update_tag()


@functools.lru_cache(maxsize=32)
def descriptor(text):
    return json.loads(text)


def affected(data, index):
    return [(i, delta) for i, control in enumerate(data['controls'])
            for delta in control['bones'] if delta['bone'] == index]


def _native_bones(rig, data):
    by_path = {}
    for bone in rig.data.bones:
        path = bone.get('sora_source_path')
        if path:
            by_path.setdefault(path, []).append(bone)
    result = []
    for record in data['bones']:
        matches = by_path.get(record['nativePath'], [])
        if len(matches) != 1:
            raise ValueError('Face bone source path is missing or ambiguous: ' + record['nativePath'])
        result.append(matches[0])
    return result


def _snapshot_poses(rig, snapshot):
    data = descriptor(rig[DATA])
    resolved = _native_bones(rig, data)
    by_path = {record['nativePath']: bone for record, bone in zip(data['bones'], resolved)}
    # Earlier snapshots predate the explicit path field; their immutable DTO
    # still maps the name used at activation to its original native path.
    legacy_paths = {data['sceneBoneNames'][record['sceneBone']]: record['nativePath'] for record in data['bones']}
    result = {}
    for name, state in snapshot.items():
        path = state.get('nativePath') or legacy_paths.get(name)
        bone = by_path.get(path)
        if bone is None:
            raise ValueError('Face ownership bone cannot be resolved; state retained')
        result[name] = rig.pose.bones[bone.name]
    return result


def native_local(data, index, weights, links=None):
    base = data['bones'][index]
    values = [list(base[key]) for key in ('position', 'rotation', 'scale')]
    for weight, (_, delta) in zip(weights, affected(data, index) if links is None else links):
        for output, key in zip(values, ('position', 'rotation', 'scale')):
            for axis in range(3):
                output[axis] += weight * delta[key][axis]
    rotation = Quaternion((1, 0, 0, 0))
    for axis in data['rotationOrder']:
        i = 'xyz'.index(axis)
        direction = tuple(1 if j == i else 0 for j in range(3))
        rotation = rotation @ Quaternion(direction, math.radians(values[1][i] * data['rotationSigns'][i]))
    return Matrix.LocRotScale(Vector(values[0]), rotation, Vector(values[2]))


def basis_at(rig, data, index, weights):
    # Explicit-name fallback is only for the read-only original-addon oracle;
    # imported bridge rigs carry stable native path metadata.
    if any(b.get('sora_source_path') for b in rig.data.bones):
        bone = _native_bones(rig, data)[index]
    else:
        bone = rig.data.bones[data['sceneBoneNames'][data['bones'][index]['sceneBone']]]
    rest_local = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
    reflection = Matrix.Diagonal((-1, 1, 1, 1))
    # Native local -> frontend local is F*local*F; the Ruri whole-object frame
    # cancels between parent and child. Facial bones are required to have parents.
    if bone.parent is None:
        raise ValueError('A face control cannot target the scene root')
    return rest_local.inverted() @ reflection @ native_local(data, index, weights) @ reflection


def original_rig(owner):
    if getattr(owner, 'type', None) != 'ARMATURE':
        owner = owner.id_data
    return getattr(owner, 'original', owner)


def invalidate(rig):
    """Call after explicitly replacing a rig DTO outside install()."""
    _rig_cache.pop(original_rig(rig).as_pointer(), None)


def _compiled(rig):
    key = rig.as_pointer()
    revision = int(rig.get(REVISION, 0))
    state = _rig_cache.get(key)
    if state is not None and state['owner'] == rig and state['revision'] == revision and state['armature'] == rig.data.as_pointer():
        _rig_cache.move_to_end(key)
        return state
    text = rig[DATA]
    data = descriptor(text)
    links = [[] for _ in data['bones']]
    for control_index, control in enumerate(data['controls']):
        for delta in control['bones']:
            links[delta['bone']].append((control_index, delta))
    native_bones = _native_bones(rig, data)
    rows = [{'bone': native_bones[index], 'links': links[index],
             'rest': None, 'parentRest': None, 'weights': None, 'value': None}
            for index, bone in enumerate(data['bones'])]
    state = {'owner': rig, 'armature': rig.data.as_pointer(), 'revision': revision, 'text': text, 'data': data, 'rows': rows}
    _rig_cache[key] = state
    _rig_cache.move_to_end(key)
    while len(_rig_cache) > 16:
        _rig_cache.popitem(last=False)
    return state


def component(rig, index, channel, *weights):
    # Blender's self is the driven RNA struct (PoseBone for these channels),
    # while the instance DTO and custom controls belong to its owning Object.
    rig = original_rig(rig)
    state = _compiled(rig)
    row = state['rows'][index]
    try:
        bone = row['bone']
        bone.name  # Detect an invalidated RNA reference after an edit-mode rebuild.
    except ReferenceError:
        invalidate(rig)
        state = _compiled(rig)
        row = state['rows'][index]
        bone = row['bone']
    if bone.parent is None:
        raise ValueError('A face control cannot target the scene root')
    rest, parent_rest = bone.matrix_local, bone.parent.matrix_local
    # Compare current rest matrices, even when weights are unchanged: edit-mode
    # skeleton changes must not reuse a previous inverse. Pose does not enter here.
    if rest != row['rest'] or parent_rest != row['parentRest']:
        row['rest'], row['parentRest'] = rest.copy(), parent_rest.copy()
        row['inverse'] = (parent_rest.inverted() @ rest).inverted()
        row['weights'] = None
    if weights != row['weights']:
        basis = row['inverse'] @ _reflection @ native_local(state['data'], index, weights, row['links']) @ _reflection
        location, rotation, scale = basis.decompose()
        row['value'] = tuple(location) + tuple(rotation) + tuple(scale)
        row['weights'] = weights
    return row['value'][channel]


@persistent
def refresh_cached(scene, _depsgraph=None):
    # At most one large-property comparison per cached rig/depsgraph update,
    # rather than two reads and hashes for each of its 870 driver components.
    for key, state in list(_rig_cache.items()):
        try:
            rig = state['owner']
            if not rig.as_pointer():
                _rig_cache.pop(key, None)
            elif rig.name in scene.objects and rig.get(DATA) != state['text']:
                _rig_cache.pop(key, None)
        except ReferenceError:
            _rig_cache.pop(key, None)


def curves(action):
    if action is None:
        return []
    result = list(getattr(action, 'fcurves', ()))
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                result.extend(bag.fcurves)
    return result


def set_enabled(rig, value):
    if bool(rig.get(ENABLED)) == bool(value):
        return
    data = descriptor(rig[DATA])
    names = [bone.name for bone in _native_bones(rig, data)]
    channels = [('location', 3, 0), ('rotation_quaternion', 4, 3), ('scale', 3, 7)]
    if value:
        if any(rig.data.bones[name].parent is None for name in names):
            raise ValueError('A face control cannot target the scene root')
        paths = {rig.pose.bones[name].path_from_id(channel) for name in names
                 for channel in ('location', 'rotation_quaternion', 'rotation_euler', 'rotation_axis_angle', 'scale')}
        animation = rig.animation_data
        actions = [] if animation is None else [animation.action] + [strip.action for track in animation.nla_tracks for strip in track.strips]
        existing = ([] if animation is None else list(animation.drivers)) + [curve for action in actions for curve in curves(action)]
        if any(curve.data_path in paths for curve in existing):
            raise ValueError('Existing facial animation or drivers use these bones; disable that facial layer before enabling Face Driver')
        nonce = uuid.uuid4().int & 0xffffff
        snapshot = {name: {'mode': rig.pose.bones[name].rotation_mode, 'drivers': [],
                           'nativePath': data['bones'][index]['nativePath'],
                           'basis': [float(v) for row in rig.pose.bones[name].matrix_basis for v in row]} for index, name in enumerate(names)}
        rig[STATE] = json.dumps(snapshot)
        created = []
        try:
            for index, name in enumerate(names):
                pose = rig.pose.bones[name]
                pose.rotation_mode = 'QUATERNION'
                links = affected(data, index)
                for channel, count, offset in channels:
                    for axis in range(count):
                        curve = pose.driver_add(channel, axis)
                        created.append((pose, channel, axis))
                        driver = curve.driver
                        driver.type = 'SCRIPTED'
                        driver.use_self = True
                        variables = []
                        for j, (control_index, _) in enumerate(links):
                            variable = driver.variables.new()
                            variable.name = chr(97 + j) if j < 26 else 'v' + str(j)
                            variable.type = 'SINGLE_PROP'
                            variable.targets[0].id = rig
                            variable.targets[0].data_path = '["' + property_name(control_index) + '"]'
                            variables.append(variable.name)
                        expression = 'sora_f(self,' + str(index) + ',' + str(offset + axis) + ''.join(',' + v for v in variables) + ')+0*' + str(nonce)
                        if len(expression) > 255:
                            raise ValueError('Face control exceeds Blender driver expression capacity')
                        driver.expression = expression
                        snapshot[name]['drivers'].append({'channel': channel, 'axis': axis, 'expression': expression,
                            'modifiers': _modifier_signature(curve),
                            'variables': [(v.name, v.targets[0].data_path) for v in driver.variables]})
            rig[STATE] = json.dumps(snapshot)
            rig[ENABLED] = True
        except Exception:
            for pose, channel, axis in created:
                pose.driver_remove(channel, axis)
            _restore(rig)
            raise
    else:
        snapshot = json.loads(rig.get(STATE, '{}'))
        if not snapshot or any('drivers' not in state for state in snapshot.values()):
            raise ValueError('Face Driver ownership snapshot is missing or legacy; no drivers were removed')
        snapshot_poses = _snapshot_poses(rig, snapshot)
        drivers = list(rig.animation_data.drivers) if rig.animation_data else []
        current_actions = ([] if not rig.animation_data else [rig.animation_data.action] +
                           [strip.action for track in rig.animation_data.nla_tracks for strip in track.strips])
        current_channels = drivers + [curve for action in current_actions for curve in curves(action)]
        owned = []
        protected = set()
        for name, state in snapshot.items():
            pose = snapshot_poses[name]
            for record in state['drivers']:
                path = pose.path_from_id(record['channel'])
                curve = next((c for c in drivers if c.data_path == path and c.array_index == record['axis']), None)
                if curve is not None and _owned_driver(curve, record, rig):
                    owned.append((pose, record['channel'], record['axis'], curve))
            prefix = pose.path_from_id() + '.'
            ours = {curve.as_pointer() for p, _, _, curve in owned if p == pose}
            if any(c.data_path.startswith(prefix) and c.as_pointer() not in ours for c in current_channels):
                protected.add(name)
        from . import face_shader
        if rig.get(face_shader.ENABLED):
            face_shader.set_enabled(rig, False)
        for pose, channel, axis, _curve in owned:
            pose.driver_remove(channel, axis)
        _restore(rig, protected)
        rig[ENABLED] = False
    rig.update_tag()


def _owned_driver(curve, record, rig):
    driver = curve.driver
    return (driver.type == 'SCRIPTED' and driver.use_self and driver.expression == record['expression']
            and _modifier_signature(curve) == record['modifiers'] and len(driver.variables) == len(record['variables'])
            and all(v.type == 'SINGLE_PROP' and v.name == expected[0] and v.targets[0].id == rig
                    and v.targets[0].data_path == expected[1] for v, expected in zip(driver.variables, record['variables'])))


def _modifier_signature(curve):
    result = []
    for modifier in curve.modifiers:
        values = {'type': modifier.type}
        for prop in modifier.bl_rna.properties:
            if prop.is_readonly or prop.type in {'POINTER', 'COLLECTION'}:
                continue
            value = getattr(modifier, prop.identifier)
            values[prop.identifier] = list(value) if getattr(prop, 'is_array', False) else value
        result.append(values)
    return result


def _restore(rig, protected=()):
    snapshot = json.loads(rig.get(STATE, '{}'))
    poses = _snapshot_poses(rig, snapshot)
    for name, state in snapshot.items():
        pose = poses[name]
        if pose and name not in protected:
            pose.rotation_mode = state['mode']
            values = state['basis']
            pose.matrix_basis = Matrix([values[i:i + 4] for i in range(0, 16, 4)])
    if STATE in rig:
        del rig[STATE]


class SORA_OT_face_toggle(bpy.types.Operator):
    bl_idname = 'sora.face_toggle'
    bl_label = 'Enable / Disable Face Driver'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rig = rig_for(context.object)
        if rig is None:
            return {'CANCELLED'}
        try:
            set_enabled(rig, not rig.get(ENABLED, False))
            return {'FINISHED'}
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_face_preset(bpy.types.Operator):
    bl_idname = 'sora.face_preset'
    bl_label = 'Apply Face Preset'
    bl_options = {'REGISTER', 'UNDO'}
    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        rig = rig_for(context.object)
        if rig is None:
            return {'CANCELLED'}
        data = descriptor(rig[DATA])
        preset = data['presets'][self.index] if self.index >= 0 else None
        if preset and preset['missingControls']:
            self.report({'ERROR'}, 'Preset has controls absent from this character')
            return {'CANCELLED'}
        weights = preset['weights'] if preset else {}
        for i, control in enumerate(data['controls']):
            prop = property_name(i)
            rig[prop] = (rig[prop] if preset and preset['additive'] else 0.0) + weights.get(control['name'], 0.0)
        rig.update_tag()
        return {'FINISHED'}


def draw(layout, context):
    rig = rig_for(context.object)
    if rig is None:
        return False
    data = descriptor(rig[DATA])
    layout.label(text=f"{len(data['controls'])} authored controls / {len(data['bones'])} bones")
    layout.operator('sora.face_toggle', text='Disable Face Driver' if rig.get(ENABLED) else 'Enable Face Driver')
    layout.operator('sora.face_preset', text='Reset controls').index = -1
    from . import face_shader
    if any(c.get('shader') for c in data['controls']):
        layout.operator('sora.face_shader_toggle', text='Disable shader controls' if rig.get(face_shader.ENABLED) else 'Enable shader controls')
    if rig.get(face_shader.ERROR):
        layout.label(text=rig[face_shader.ERROR], icon='ERROR')
    layout.prop(context.scene.sora, 'face_filter', text='Filter')
    query = context.scene.sora.face_filter.lower()
    for i, control in enumerate(data['controls']):
        if query and query not in control['name'].lower():
            continue
        row = layout.row()
        row.enabled = not control.get('shader') or bool(rig.get(face_shader.ENABLED))
        row.prop(rig, '["' + property_name(i) + '"]', text=control['name'], slider=True)
    if query:
        for i, preset in enumerate(data['presets']):
            if query in preset['name'].lower():
                row = layout.row()
                row.enabled = not preset['missingControls']
                row.operator('sora.face_preset', text=preset['name']).index = i
    return True


@persistent
def loaded(_):
    descriptor.cache_clear()
    _rig_cache.clear()
    bpy.app.driver_namespace['sora_f'] = component
    if bpy.app.timers.is_registered(_load_rebind_timer):
        bpy.app.timers.unregister(_load_rebind_timer)
    bpy.app.timers.register(_load_rebind_timer, first_interval=0.0)


def rebind_owned_after_load():
    """Recompile signatures we own after Blender evaluated early load drivers."""
    rebound = 0
    for rig in bpy.data.objects:
        if rig.type != 'ARMATURE' or not rig.get(ENABLED) or rig.animation_data is None:
            continue
        try:
            snapshot = json.loads(rig.get(STATE, '{}'))
        except (TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        try:
            snapshot_poses = _snapshot_poses(rig, snapshot)
        except (ValueError, KeyError, TypeError):
            continue
        drivers = {(curve.data_path, curve.array_index): curve for curve in rig.animation_data.drivers}
        changed = False
        for name, state in snapshot.items():
            pose = snapshot_poses.get(name)
            if pose is None or not isinstance(state, dict):
                continue
            for record in state.get('drivers', []):
                try:
                    curve = drivers.get((pose.path_from_id(record['channel']), record['axis']))
                    owned = curve is not None and _owned_driver(curve, record, rig)
                except (KeyError, TypeError, AttributeError):
                    continue
                if owned:
                    # Assignment invalidates Blender's early-load compiled error
                    # without altering the authored expression or foreign curves.
                    curve.driver.expression = curve.driver.expression
                    changed = True
                    rebound += 1
        if changed:
            rig.update_tag()
    return rebound


def _load_rebind_timer():
    # One shot, after all load_post callbacks have installed their namespaces.
    if bpy.app.driver_namespace.get('sora_f') is component:
        if rebind_owned_after_load() and bpy.context.view_layer is not None:
            bpy.context.view_layer.update()
    return None


def register():
    from . import face_shader
    face_shader.register()
    for cls in (SORA_OT_face_toggle, SORA_OT_face_preset):
        bpy.utils.register_class(cls)
    if loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(loaded)
    if refresh_cached not in bpy.app.handlers.depsgraph_update_pre:
        bpy.app.handlers.depsgraph_update_pre.append(refresh_cached)
    loaded(None)


def unregister():
    if bpy.app.timers.is_registered(_load_rebind_timer):
        bpy.app.timers.unregister(_load_rebind_timer)
    from . import face_shader
    face_shader.unregister()
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE' and obj.get(ENABLED):
            set_enabled(obj, False)
    if loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(loaded)
    if refresh_cached in bpy.app.handlers.depsgraph_update_pre:
        bpy.app.handlers.depsgraph_update_pre.remove(refresh_cached)
    bpy.app.driver_namespace.pop('sora_f', None)
    descriptor.cache_clear()
    _rig_cache.clear()
    for cls in reversed((SORA_OT_face_toggle, SORA_OT_face_preset)):
        bpy.utils.unregister_class(cls)
