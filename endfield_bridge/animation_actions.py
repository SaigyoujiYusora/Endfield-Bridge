"""Bulk layered actions from Core DTOs; no game-format parsing or retargeting."""
from array import array
import hashlib
import json
import math
import uuid

import bpy


def resolve_bones(rig, names, sources=None):
    if sources is None:
        if any(name not in rig.pose.bones for name in names):
            raise ValueError('Animation skeleton names do not match this armature')
        return [rig.pose.bones[name] for name in names]
    if len(sources) != len(names):
        raise ValueError('Animation bone identity arrays disagree')
    by_path = {}
    for bone in rig.data.bones:
        if 'sora_source_path' in bone:
            by_path.setdefault(bone['sora_source_path'], []).append(bone)
    result = []
    for source in sources:
        path = source.get('sourcePath')
        matches = by_path.get(path, [])
        if path is None or len(matches) != 1:
            raise ValueError('Animation native bone path is absent or ambiguous: ' + str(path))
        bone = matches[0]
        if source.get('sourceHash') is not None and bone.get('sora_source_hash') is not None:
            if int(bone['sora_source_hash']) != int(source['sourceHash']):
                raise ValueError('Animation native bone hash differs: ' + path)
        result.append(rig.pose.bones[bone.name])
    return result


def samples(track, dimension, fps, duration, quaternion=False, frame_origin=1):
    frames, values = [], []
    previous_time = -math.inf
    previous_quaternion = None
    for key in track['keys']:
        time = float(key['time'])
        value = key['value']
        value = [float(value)] if dimension == 1 and isinstance(value, (int, float)) else [float(x) for x in value]
        if (not math.isfinite(time) or time < 0 or time <= previous_time or time > duration + 1e-5
                or len(value) != dimension or not all(math.isfinite(x) for x in value)):
            raise ValueError('Invalid animation key time or value')
        if quaternion:
            if abs(sum(x*x for x in value) - 1) > .01:
                raise ValueError('Animation quaternion is not normalized')
            value = [value[3], value[0], value[1], value[2]]
            if previous_quaternion is not None and sum(a*b for a,b in zip(value,previous_quaternion)) < 0:
                value = [-x for x in value]
            previous_quaternion = value
        frames.append(frame_origin + time * fps)
        values.append(value)
        previous_time = time
    if not frames:
        raise ValueError('Animation track has no keys')
    return frames, values


def owned_face_drivers(rig):
    from . import face_controls as face
    if not rig.get(face.ENABLED):
        return set()
    snapshot = json.loads(rig.get(face.STATE, '{}'))
    poses = face._snapshot_poses(rig, snapshot)
    current = {(c.data_path,c.array_index):c for c in rig.animation_data.drivers}
    result = set()
    for name, state in snapshot.items():
        pose = poses[name]
        for record in state.get('drivers', []):
            key = (pose.path_from_id(record['channel']), record['axis'])
            curve = current.get(key)
            if curve is not None and face._owned_driver(curve, record, rig):
                result.add(key)
    return result


def scalar_property(name):
    prop = 'sora_anim_scalar_' + name
    return prop if len(prop.encode('utf-8')) <= 63 else 'sora_anim_scalar_' + hashlib.sha256(name.encode()).hexdigest()[:24]


def scalar_tracks(clip):
    result = list(clip.get('scalarTracks') or [])
    for source in (clip.get('native') or {}).get('customScalars') or []:
        attribute = source['attribute']
        path, type_id, custom_type = source['path'], source['typeId'], source['customType']
        rate = float(source['sampleRate'])
        if (not isinstance(attribute, int) or isinstance(attribute, bool) or not 0 <= attribute <= 0xffffffff
                or not isinstance(path, int) or isinstance(path, bool) or not 0 <= path <= 0xffffffff
                or not isinstance(type_id, int) or isinstance(type_id, bool)
                or not isinstance(custom_type, int) or isinstance(custom_type, bool)
                or not math.isfinite(rate) or rate <= 0):
            raise ValueError('Invalid native Animator scalar identity or rate')
        identity = 'Animator_' + '_'.join(str(v) for v in (path,type_id,custom_type,attribute))
        result.append(dict(source, name=identity,
                           keys=[{'time': i/rate, 'value': [value]} for i,value in enumerate(source['values'])]))
    return result


def curve_signature(curve):
    from .face_controls import _modifier_signature
    points = array('f', [0]) * (2 * len(curve.keyframe_points))
    interpolation = array('i', [0]) * len(curve.keyframe_points)
    curve.keyframe_points.foreach_get('co', points)
    curve.keyframe_points.foreach_get('interpolation', interpolation)
    return hashlib.sha256(points.tobytes() + interpolation.tobytes() + json.dumps(_modifier_signature(curve), sort_keys=True).encode()).hexdigest()


def face_curves(rig, action, records):
    from .face_controls import curves
    if action is None or action.get('sora_instance') != rig.get('sora_instance') or not action.get('sora_animation_owner'):
        raise ValueError('Face mode requires an owned imported animation Action')
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError('Face curve ownership metadata is invalid')
    by_path = {}
    for bone in rig.data.bones:
        if 'sora_source_path' in bone:
            by_path.setdefault(bone['sora_source_path'], []).append(bone)
    lookup = {}
    for curve in curves(action):
        lookup.setdefault((curve.data_path, curve.array_index), []).append(curve)
    result, seen = [], set()
    for record in records:
        channel = record.get('channel')
        if (not isinstance(record.get('nativePath'), str)
                or channel not in {'location', 'rotation_quaternion', 'scale'}
                or type(record.get('axis')) is not int
                or not 0 <= record['axis'] < (4 if channel == 'rotation_quaternion' else 3)
                or not isinstance(record.get('signature'), str)):
            raise ValueError('Face curve ownership metadata is invalid')
        matches = by_path.get(record['nativePath'], [])
        if len(matches) != 1:
            raise ValueError('Face animation bone identity is missing or ambiguous')
        pose = rig.pose.bones[matches[0].name]
        key = (pose.path_from_id(record['channel']), record['axis'])
        if key in seen:
            raise ValueError('Duplicate Face curve ownership record')
        seen.add(key)
        matches = lookup.get(key, [])
        curve = matches[0] if len(matches) == 1 else None
        if curve is None or curve_signature(curve) != record['signature']:
            raise ValueError('Imported face curve was replaced or edited; Face mode left unchanged')
        result.append((record,curve))
    return result


def _owned_action(rig, action):
    return (action is not None and action.get('sora_animation_owner')
            and action.get('sora_instance') == rig.get('sora_instance'))


def _properties(owner, names):
    return {name: (name in owner, owner.get(name)) for name in names}


def _restore_properties(owner, state):
    for name, (present, value) in state.items():
        if present:
            owner[name] = value
        elif name in owner:
            del owner[name]


def _private_action(rig, action):
    """F-curve mutes are global to the Action, including inactive NLA uses."""
    if action.library is not None or not action.is_editable:
        raise ValueError('Face mask requires a local editable Action')
    users = set(bpy.data.user_map(subset={action}).get(action, ())) | {rig}
    def nla_uses(strips):
        return any(getattr(strip, 'action', None) == action
                   or nla_uses(getattr(strip, 'strips', ())) for strip in strips)
    for user in users:
        animation = getattr(user, 'animation_data', None)
        if animation is None:
            continue
        if user != rig and animation.action == action:
            raise ValueError('Face mask Action is assigned to another object; no shared curves were changed')
        if any(nla_uses(track.strips) for track in animation.nla_tracks):
            raise ValueError('Face mask Action is used by NLA; no shared curves were changed')


def _face_mask(rig):
    animation = rig.animation_data
    current = animation.action if animation else None
    action = rig.get('sora_face_override_action')
    if action is None and _owned_action(rig, current) and current.get('sora_face_mode') == 'MANUAL':
        action = current
    if action is None:
        return None
    if not isinstance(action, bpy.types.Action) or not _owned_action(rig, action):
        raise ValueError('Face mask Action identity is not owned by this armature')
    records = json.loads(action.get('sora_face_override', '[]'))
    if not isinstance(records, list) or any(not isinstance(record, dict) or type(record.get('priorMute')) is not bool for record in records):
        raise ValueError('Face mask ownership metadata is invalid')
    resolved = face_curves(rig, action, records)
    _private_action(rig, action)
    if any(not curve.mute for _, curve in resolved):
        raise ValueError('Imported Face mute state was changed; no curves were restored')
    return {'action': action, 'resolved': resolved,
            'mutes': [(curve, curve.mute) for _, curve in resolved],
            'metadata': _properties(action, ('sora_face_override', 'sora_face_mode', 'sora_face_reset_defaults'))}


def _release_face_mask(state):
    if state is None:
        return
    for record, curve in state['resolved']:
        curve.mute = record['priorMute']
    state['action']['sora_face_override'] = '[]'
    state['action']['sora_face_mode'] = 'ANIMATION'
    state['action']['sora_face_reset_defaults'] = False


def _restore_face_mask(state):
    if state is not None:
        for curve, mute in state['mutes']:
            curve.mute = mute
        _restore_properties(state['action'], state['metadata'])


def _associate_face(rig, action, manual):
    if action is not None:
        action['sora_face_mode'] = 'MANUAL' if manual else 'ANIMATION'
    if manual and action is not None:
        rig['sora_face_override_action'] = action
    elif 'sora_face_override_action' in rig:
        del rig['sora_face_override_action']


def _refresh_frame(context, rig, frame=None):
    rig.update_tag()
    context.scene.frame_set(context.scene.frame_current if frame is None else frame,
                            subframe=context.scene.frame_subframe if frame is None else 0.0)


def _face_defaults_after_release(rig, action):
    """A clip loaded during manual Face establishes a new sparse-pose baseline."""
    if action is None or not action.get('sora_face_reset_defaults'):
        return []
    from . import face_controls as face
    if not rig.is_editable:
        raise ValueError('Face mode requires an editable imported armature')
    paths = json.loads(action.get('sora_source_bone_paths', '[]'))
    if not paths and 'sora_bone_sources' in action:
        paths = [source['sourcePath'] for source in json.loads(action['sora_bone_sources'])]
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ValueError('Animation source-bone scope is invalid')
    scope = set(paths)
    animation = rig.animation_data
    keyed = {(curve.data_path, curve.array_index) for curve in face.curves(action)}
    def nla_curves(strips):
        for strip in strips:
            yield from face.curves(getattr(strip, 'action', None))
            yield from nla_curves(getattr(strip, 'strips', ()))
    if animation:
        keyed.update((curve.data_path, curve.array_index) for track in animation.nla_tracks
                     for curve in nla_curves(track.strips))
        keyed.update({(curve.data_path, curve.array_index) for curve in animation.drivers}
                     - owned_face_drivers(rig))
    changes = []
    for bone in face._native_bones(rig, face.descriptor(rig[face.DATA])):
        if bone.get('sora_source_path') not in scope:
            continue
        pose = rig.pose.bones[bone.name]
        for channel, values in (('location', (0, 0, 0)), ('scale', (1, 1, 1)),
                                ('rotation_euler', (0, 0, 0)),
                                ('rotation_quaternion', (1, 0, 0, 0)),
                                ('rotation_axis_angle', (0, 0, 1, 0))):
            path = pose.path_from_id(channel)
            changes.extend((pose, channel, axis, value) for axis, value in enumerate(values)
                           if (path, axis) not in keyed)
    return changes


def set_manual_face(context, rig, enabled):
    """Explicitly mask only our imported Face curves, retaining every source key."""
    from . import face_controls as face
    animation = rig.animation_data
    current = animation.action if animation else None
    managed = current if _owned_action(rig, current) else None
    old = _face_mask(rig)
    pointer = _properties(rig, ('sora_face_override_action',))
    metadata = _properties(managed, ('sora_face_override', 'sora_face_mode', 'sora_face_reset_defaults')) if managed else None
    reset_defaults = bool(managed.get('sora_face_reset_defaults')) if managed else False
    defaults = _face_defaults_after_release(rig, managed) if not enabled else []
    resolved = face_curves(rig, managed, json.loads(managed.get('sora_face_curves', '[]'))) if enabled and managed else []
    if managed is not None:
        _private_action(rig, managed)
    was_enabled = bool(rig.get(face.ENABLED))
    if enabled and was_enabled:
        owned = owned_face_drivers(rig)
        existing = {(c.data_path, c.array_index) for c in animation.drivers} if animation else set()
        if any((curve.data_path, curve.array_index) in existing - owned for _, curve in resolved):
            raise ValueError('A user driver conflicts with the animation face; mode left unchanged')
        resolved = [(record, curve) for record, curve in resolved if (curve.data_path, curve.array_index) in owned]
    prior = [(curve, curve.mute) for _, curve in resolved]
    previous_mutes = {curve.as_pointer(): record['priorMute'] for record, curve in old['resolved']} if old else {}
    overrides = [dict(record, priorMute=previous_mutes.get(curve.as_pointer(), curve.mute)) for record, curve in resolved]
    if not enabled and old:
        owned = owned_face_drivers(rig)
        existing = {(c.data_path,c.array_index) for c in animation.drivers} if animation else set()
        if any((curve.data_path,curve.array_index) in existing - owned for _,curve in old['resolved']):
            raise ValueError('A user driver conflicts with the animation face; mode left unchanged')
    from .action_binding import bind_action, slot_identity
    slot = slot_identity(animation.action_slot) if animation else None
    detached = False
    try:
        _release_face_mask(old)
        if enabled and managed is not None:
            for _, curve in resolved:
                curve.mute = True
            managed['sora_face_override'] = json.dumps(overrides, separators=(',', ':'))
            managed['sora_face_reset_defaults'] = reset_defaults
        _associate_face(rig, managed, enabled)
        if not enabled and managed is not None:
            managed['sora_face_reset_defaults'] = False
        if enabled and resolved and not was_enabled:
            # Only detach this verified local Action; NLA remains visible to Face validation.
            bind_action(rig,None)
            detached = True
        face.set_enabled(rig, enabled)
    except Exception:
        for curve, mute in prior:
            curve.mute = mute
        if metadata is not None:
            _restore_properties(managed, metadata)
        _restore_face_mask(old)
        _restore_properties(rig, pointer)
        raise
    finally:
        if detached:
            bind_action(rig,current,slot,select_slot=True)
    # Face's activation snapshot predates the newer sparse Action. Disabling its
    # drivers must not resurrect that old pose on unkeyed source components.
    for pose, channel, axis, value in defaults:
        getattr(pose, channel)[axis] = value
    _refresh_frame(context, rig)


def apply_clip(context, rig, clip, bone_names, bone_sources=None, keep_face_controls=False):
    work = apply_clip_steps(context, rig, clip, bone_names, bone_sources, keep_face_controls)
    while True:
        try:
            next(work)
        except StopIteration as finished:
            return finished.value


def apply_clip_steps(context, rig, clip, bone_names, bone_sources=None, keep_face_controls=False, *, timeline=None,
                     transaction=None):
    if rig is not None and rig.get('sora_pose_resume'):
        raise ValueError('Restore suspended animation before loading another clip')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Switch to Object or Pose Mode before loading an animation')
    if rig is None or rig.type != 'ARMATURE' or not rig.get('sora_instance'):
        raise ValueError('Select a Sora-Core imported armature')
    fps, duration = float(clip['fps']), float(clip['duration'])
    if not math.isfinite(fps) or not math.isfinite(duration) or fps <= 0 or duration < 0 or 1 + duration*fps > 1048574:
        raise ValueError('Animation timing exceeds Blender limits')
    source_fps=fps
    frame_origin=1.0
    if timeline is not None:
        fps,frame_origin=float(timeline['fps']),float(timeline['origin'])
        if not math.isfinite(fps) or fps<=0 or not math.isfinite(frame_origin) or frame_origin < -1048574 or frame_origin+duration*fps>1048574:
            raise ValueError('Equipment timeline mapping exceeds Blender limits')
    bones = resolve_bones(rig, bone_names, bone_sources)
    existing_drivers = {(c.data_path,c.array_index) for c in rig.animation_data.drivers} if rig.animation_data else set()
    face_drivers = owned_face_drivers(rig) if keep_face_controls else set()
    from . import face_controls as face
    face_paths = {b['nativePath'] for b in face.descriptor(rig[face.DATA])['bones']} if face.DATA in rig else set()
    prepared, destinations, overrides, rotation_bones = [], set(), [], set()
    scalar_registry = json.loads(rig.get('sora_anim_scalar_map', '{}'))
    scalar_properties = {}
    for track_index, track in enumerate(clip['tracks']):
        if track_index % 16 == 0:
            yield {'stage': 'Preparing animation tracks', 'completed': track_index, 'total': len(clip['tracks'])}
        index = track['bone']
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(bones):
            raise ValueError('Animation bone index is out of range')
        bone = bones[index]
        channel = track['channel']
        if channel not in {'location', 'rotation', 'scale'}:
            raise ValueError('Unsupported animation bone channel: ' + str(channel))
        attribute = 'rotation_quaternion' if channel == 'rotation' else channel
        dimension = 4 if channel == 'rotation' else 3
        if channel == 'rotation':
            rotation_bones.add(bone.name)
            if any(path == bone.path_from_id(other) for path, _ in existing_drivers for other in ('rotation_euler','rotation_axis_angle')):
                raise ValueError('Existing non-quaternion rotation driver conflicts with animation: ' + bone.name)
        frames, values = samples(track, dimension, fps, duration, channel == 'rotation', frame_origin)
        path = bone.path_from_id(attribute)
        for axis in range(dimension):
            destination = (path, axis)
            if destination in destinations:
                raise ValueError('Duplicate animation destination')
            destinations.add(destination)
            muted = destination in existing_drivers
            if muted and destination not in face_drivers:
                raise ValueError('Existing driver conflicts with animation: ' + bone.name)
            face_record = {'nativePath': bone.bone.get('sora_source_path'), 'boneName': bone.name, 'channel': attribute, 'axis': axis, 'priorMute': False} if bone.bone.get('sora_source_path') in face_paths else None
            prepared.append((path, axis, bone.name, frames, values, axis, muted, face_record))
    custom_tracks = scalar_tracks(clip)
    for track in custom_tracks:
        name = track['name']
        if not isinstance(name, str) or not name:
            raise ValueError('Native scalar track requires an explicit name')
        prop = scalar_property(name)
        if prop in scalar_properties or (prop in rig and scalar_registry.get(prop) != name):
            raise ValueError('Native scalar property is duplicated or not owned: ' + name)
        path = '[' + json.dumps(prop, ensure_ascii=False) + ']'
        if any(p == path for p, _ in existing_drivers):
            raise ValueError('Existing scalar driver conflicts with animation: ' + name)
        frames, values = samples(track, 1, fps, duration, frame_origin=frame_origin)
        scalar_properties[prop] = name
        prepared.append((path, 0, 'Native Animator Scalars', frames, values, 0, False, None))
    if not prepared:
        raise ValueError('Animation has no playable tracks')

    # Preflight before creating any Action, resetting pose components, or touching masks.
    old_face_mask = _face_mask(rig)

    animation = rig.animation_data
    previous_action = animation.action if animation else None
    from .action_binding import bind_action, slot_identity
    previous_slot = slot_identity(animation.action_slot) if animation else None
    timing = (context.scene.render.fps, context.scene.render.fps_base, context.scene.frame_start,
              context.scene.frame_end, context.scene.frame_current, context.scene.frame_subframe)
    pose_state = [(bone, bone.rotation_mode, bone.matrix_basis.copy()) for bone in rig.pose.bones]
    property_state = {prop: (prop in rig, rig.get(prop)) for prop in scalar_properties}
    registry_state = rig.get('sora_anim_scalar_map')
    previous_override = _properties(rig, ('sora_face_override_action',))
    from .animation_queue import owned_track
    queue_tracks = [(track, track.mute) for track in animation.nla_tracks if owned_track(track, rig)] if animation else []
    action = bpy.data.actions.new(clip['name'])
    # Transaction ownership is recorded the moment the datablock exists, so a failure later in this same
    # import still identifies this action as this transaction's own data even if it is never bound.
    if transaction is not None:
        action['sora_skill_transaction'] = transaction
    try:
        action['sora_instance'] = rig['sora_instance']
        # The mapping recorded here is the mapping the keys were written with, so readers never have to
        # assume a frame origin or frame rate.
        action['sora_timeline_mapping']=json.dumps({'sourceFps':source_fps,'actionFps':fps,'frameOrigin':frame_origin,
            'frameEnd':frame_origin+duration*fps,'durationSeconds':duration,'sceneTimingPreserved':True})
        action['sora_animation_owner'] = uuid.uuid4().hex
        action['sora_clip_metadata'] = json.dumps({k:v for k,v in clip.items() if k not in {'tracks','scalarTracks'}}, separators=(',', ':'))
        action['sora_track_metadata'] = json.dumps([{k:v for k,v in track.items() if k != 'keys'} for track in clip['tracks']], separators=(',', ':'))
        action['sora_scalar_metadata'] = json.dumps([{k:v for k,v in track.items() if k != 'keys'} for track in custom_tracks], separators=(',', ':'))
        action['sora_scalar_properties'] = json.dumps(scalar_properties, separators=(',', ':'))
        if bone_sources is not None:
            action['sora_bone_sources'] = json.dumps(bone_sources, separators=(',', ':'))
        action['sora_source_bone_paths'] = json.dumps([bone.bone['sora_source_path'] for bone in bones
                                                     if 'sora_source_path' in bone.bone])
        action['sora_face_reset_defaults'] = bool(rig.get(face.ENABLED))
        if previous_action is not None:
            action['sora_previous_action'] = previous_action
            if previous_slot is not None:
                action['sora_previous_slot'] = previous_slot['identifier']
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        created_slot_identity = slot_identity(slot)
        layer = action.layers.new('Animation')
        strip = layer.strips.new(type='KEYFRAME')
        bag = strip.channelbags.new(slot)
        interpolation = {}
        candidate_faces = []
        for curve_index, (path, axis, group, frames, values, component, muted, face_record) in enumerate(prepared):
            if curve_index % 16 == 0:
                yield {'stage': 'Creating animation curves', 'completed': curve_index, 'total': len(prepared)}
            curve = bag.fcurves.new(data_path=path, index=axis, group_name=group)
            count = len(frames)
            curve.keyframe_points.add(count)
            points = array('f', (value for frame, sample in zip(frames, values) for value in (frame, sample[component])))
            curve.keyframe_points.foreach_set('co', points)
            linear = interpolation.setdefault(count, array('i', [1]) * count)
            curve.keyframe_points.foreach_set('interpolation', linear)
            curve.mute = muted
            curve.update()
            if face_record is not None:
                face_record['signature'] = curve_signature(curve)
                candidate_faces.append(face_record)
                if muted:
                    overrides.append(face_record)
        yield {'stage': 'Binding animation and Face state'}
        action['sora_face_curves'] = json.dumps(candidate_faces, separators=(',', ':'))
        action['sora_face_override'] = json.dumps(overrides, separators=(',', ':'))
        action['sora_face_mode'] = 'MANUAL' if rig.get(face.ENABLED) else 'ANIMATION'
        for prop, name in scalar_properties.items():
            if prop not in rig:
                rig[prop] = 0.0
                rig.id_properties_ui(prop).update(description='Native Animator scalar: ' + name)
            scalar_registry[prop] = name
        if scalar_properties:
            rig['sora_anim_scalar_map'] = json.dumps(scalar_registry)
        # Unkeyed channels otherwise retain the preceding Action's evaluated pose.
        # Limit defaults to the source skeleton, preserving user-driven components.
        for bone in bones:
            for attribute, defaults in (('location', (0, 0, 0)), ('scale', (1, 1, 1))):
                path = bone.path_from_id(attribute)
                for axis, value in enumerate(defaults):
                    if (path, axis) not in existing_drivers:
                        getattr(bone, attribute)[axis] = value
            for attribute, defaults in (('rotation_euler', (0, 0, 0)),
                                        ('rotation_quaternion', (1, 0, 0, 0)),
                                        ('rotation_axis_angle', (0, 0, 1, 0))):
                path = bone.path_from_id(attribute)
                for axis, value in enumerate(defaults):
                    if (path, axis) not in existing_drivers:
                        getattr(bone, attribute)[axis] = value
        rig.animation_data_create()
        for track, _ in queue_tracks:
            track.mute = True
        bind_action(rig,action,created_slot_identity,select_slot=True)
        _release_face_mask(old_face_mask)
        _associate_face(rig, action, bool(rig.get(face.ENABLED)))
        for name in rotation_bones:
            rig.pose.bones[name].rotation_mode = 'QUATERNION'
        action.use_fake_user = True
        # The authored native end can sit past the last decoded sample (walk loop: 65 samples at 60 Hz,
        # grid 1.0666667 s, authored 1.0709809 s). Declare the effective Action range from the authored
        # interval so consumers (Action range, NLA strip action_frame_end) keep the whole tail. Key times
        # stay exactly as decoded: no sample key is fabricated and no authored duration is shortened.
        native_end = frame_origin + duration * fps
        snapped_end = math.floor(native_end + .5)
        if abs(native_end - snapped_end) <= 1e-6:
            native_end = snapped_end
        if hasattr(action, 'use_frame_range'):
            action.use_frame_range = True
            action.frame_start = frame_origin
            action.frame_end = native_end
        if timeline is None:
            context.scene.render.fps = max(1, round(fps))
            context.scene.render.fps_base = context.scene.render.fps / fps
            context.scene.frame_start = 1
            # A sample-grid duration can round a few ULPs above the exact grid end (8.333333333333334 * 60 =
            # 500.00000000000006), which would append a whole extra frame past the last authored key. Absorb that
            # boundary noise before ceil; a real sub-sample tail (walk loop 1.0709809) still keeps its extra frame.
            context.scene.frame_end = max(1, math.ceil(duration*fps - 1e-9) + 1)
            _refresh_frame(context, rig, 1)
        else:
            rig.update_tag()
            context.view_layer.update()
        return action
    except BaseException:
        # Restore the previous mask before evaluating its Action again.
        for track, mute in queue_tracks:
            track.mute = mute
        _restore_face_mask(old_face_mask)
        _restore_properties(rig, previous_override)
        if animation is not None:
            bind_action(rig,previous_action,previous_slot,select_slot=True)
        else:
            rig.animation_data_clear()
        bpy.data.actions.remove(action)
        if timeline is None:
            context.scene.render.fps, context.scene.render.fps_base, context.scene.frame_start, context.scene.frame_end = timing[:4]
            context.scene.frame_set(timing[4], subframe=timing[5])
        for bone, mode, matrix in pose_state:
            bone.rotation_mode = mode
            bone.matrix_basis = matrix
        for prop, (existed, value) in property_state.items():
            if existed:
                rig[prop] = value
            elif prop in rig:
                del rig[prop]
        if registry_state is not None:
            rig['sora_anim_scalar_map'] = registry_state
        elif 'sora_anim_scalar_map' in rig:
            del rig['sora_anim_scalar_map']
        raise
