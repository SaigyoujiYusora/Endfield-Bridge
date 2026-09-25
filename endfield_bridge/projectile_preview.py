"""Instance-owned, reversible native mesh/trajectory preview for fixed-point projectile branches."""
import json
import math
import bpy
from mathutils import Matrix, Vector
from bpy.app.handlers import persistent
from . import equipment as eq, projectile_math

OWNER = 'sora_projectile_owner'
ACTION = 'sora_projectile_action'
MASK = 'sora_projectile_visibility_restore'
MASK_ACTION = 'sora_projectile_visibility_action'
_busy = False


def ensure_visuals():
    from . import tasks, projectile_material
    if tasks.busy(): return .1
    for obj in bpy.data.objects:
        if obj.get(OWNER) and obj.type == 'MESH':
            projectile_material.restore_material(obj)
    return None


def remove_objects(objects):
    materials = {m for o in objects for m in o.data.materials if m and m.get('sora_projectile_preview_material')}
    for obj in objects:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            bpy.data.meshes.remove(data)
    for material in materials:
        if material.users == 0: bpy.data.materials.remove(material)


def restore_visibility(owner):
    saved = json.loads(owner.get(MASK, '[]'))
    for row in saved:
        child = bpy.data.collections.get(row['name'])
        if child and child.get('sora_owner_collection') == owner:
            child.hide_viewport, child.hide_render = row['hide']
    if MASK in owner:
        del owner[MASK]
    if MASK_ACTION in owner:
        del owner[MASK_ACTION]


def build(context, owner, rig, action, plan, *, scene_origin=None):
    """Sample native muzzle after body/equipment binding; create only our own unparented objects."""
    from . import equipment_events, projectile_mount, projectile_material
    flight = plan.get('projectilePreview') or {}
    mapping = json.loads(action['sora_timeline_mapping'])
    rows = projectile_math.select_launches(flight, mapping['durationSeconds'])
    # Equipment state belongs to the skill even when no projectile can be built.
    # Otherwise an unsupported shot keeps the previous skill's manually visible bow.
    assembly = json.loads(owner[eq.CONTRACT])
    shown = {r['visibility']['weaponIndex'] for r in plan.get('weaponVisibility') or []
             if r['visibility']['visible']}
    if any(r['visibility']['visible'] and r['visibility']['includeAllWeapons'] for r in plan.get('weaponVisibility') or []):
        shown.update(s['weaponIndex'] for s in assembly['declaration']['dedicatedEquipment'])
    action['sora_projectile_preview'] = True
    action['sora_projectile_scope'] = flight.get('scope', '')
    action['sora_projectile_hidden_slots'] = json.dumps([s['slotId']
        for s in assembly['declaration']['dedicatedEquipment']
        if not s['showWhenIdle'] and not s['showWhenFight'] and s['weaponIndex'] not in shown])
    action['sora_projectile_held_arrow_events'] = json.dumps(flight.get('heldArrowEvents') or [], separators=(',', ':'))
    action['sora_projectile_mount_weapons'] = json.dumps(sorted(shown))
    action['sora_projectile_count'] = len(rows)
    action['sora_projectile_diagnostics'] = json.dumps(flight.get('diagnostics') or [])
    if not rows:
        return []
    visuals = flight.get('visuals') or {}
    # Validate complete visual/mount identity before changing frames or creating data.
    targets = []
    for row in rows:
        slot = next((c for c in eq.owned_children(owner, 'dedicated')
                     if c.get('sora_equipment_slot', '').endswith(':'+str(row['weaponIndex']))), None)
        candidates = [o for o in slot.objects if o.get('sora_source_path') == row['mountSourcePath']] if slot else []
        if len(candidates) != 1:
            raise ValueError('弹体预览缺少唯一原生发射挂点：'+str(row['mountSourcePath']))
        meshes = [mesh for effect in row['motion']['effects'] for mesh in visuals.get(effect, [])]
        if not meshes:
            raise ValueError('弹体特效没有可预览的原生网格：'+row['projectileId'])
        donor = projectile_material.material_source(owner, row['projectileId'], flight.get('heldArrowEvents') or [])
        targets.append((row, candidates[0], meshes, donor))
    scene = context.scene
    frame, subframe = scene.frame_current, scene.frame_subframe
    created = []
    succeeded = False
    try:
        # NPR imports rotate the object and counter-rotate its datablock. Recover the
        # Core-to-object frame from the recorded rest matrices instead of applying that
        # presentation rotation a second time to native fixed-point offsets.
        sources = json.loads(action.get('sora_bone_sources', '[]'))
        source = next((s for s in sources if s.get('restMatrix') and rig.data.bones.get(s['name'])), None)
        if source is None:
            raise ValueError('弹体预览缺少角色原生坐标基准')
        correction = rig.data.bones[source['name']].matrix_local @ eq.mat(source['restMatrix']).inverted()
        for row, muzzle, meshes, donor in targets:
            launch_frame = (mapping['frameOrigin'] if scene_origin is None else scene_origin) + row['time']*mapping['actionFps']
            scene.frame_set(math.floor(launch_frame), subframe=launch_frame % 1)
            equipment_events.apply(context, owner)
            projectile_mount.apply(context, owner, rig, action, {r['weaponIndex'] for r in rows})
            context.view_layer.update()
            start = muzzle.matrix_world.translation.copy()
            x, y, z = row.get('fixedPoint') or (0, 0, row['motion']['distance'])
            # A preview has no target entity. Interpret the authored fixed offset in the owner's
            # scene orientation; native Unity (-x,-z,y) conversion matches Core geometry.
            delta = (rig.matrix_world @ correction).to_quaternion() @ Vector((-x, -z, y))
            end = start + delta
            motion = row['motion']
            shot = {'time': row['time'], 'start': list(start), 'end': list(end),
                    'speed': motion['speed'], 'duration': motion['duration'], 'distance': motion['distance'],
                    'finishOnReach': motion['finishOnReach'], 'projectileId': row['projectileId'],
                    'speedCurve': motion.get('speedCurve'),
                    'sourceOffset': row['offset'], 'branch': row['branch']}
            for source in meshes:
                extents = [max(p[i] for p in source['positions'])-min(p[i] for p in source['positions']) for i in range(3)]
                axis = max(range(3), key=lambda i: extents[i])
                # Mesh-particle arrows are tip-pivoted: geometry extends backwards from
                # the emitter. Orient the tip, not the long trailing end, along travel.
                center = (max(p[axis] for p in source['positions'])+min(p[axis] for p in source['positions']))/2
                sign = -1 if center > 0 else 1
                forward = Vector(tuple(sign if i == axis else 0 for i in range(3)))
                rotation = forward.rotation_difference(delta.normalized())
                mesh = bpy.data.meshes.new('ENDF Projectile · '+source['name'])
                obj = bpy.data.objects.new('ENDF Projectile · '+row['projectileId'], mesh)
                created.append(obj)
                owner.objects.link(obj)
                compensation = projectile_material.populate(obj, source, donor)
                obj.rotation_mode = 'QUATERNION'
                obj.rotation_quaternion = rotation @ compensation
                obj.location = start
                obj[OWNER], obj[ACTION] = owner, action
                obj['sora_projectile_trajectory'] = json.dumps(shot, separators=(',', ':'))
                obj.hide_viewport = obj.hide_render = True
        succeeded = True
        return created
    except BaseException:
        remove_objects(created)
        projectile_mount.restore(owner)
        raise
    finally:
        scene.frame_set(frame, subframe=subframe)
        equipment_events.apply(context, owner)
        if succeeded:
            projectile_mount.apply(context, owner, rig, action, {r['weaponIndex'] for r in rows})
        context.view_layer.update()


def commit(owner, created):
    restore_visibility(owner)
    referenced = set()
    for obj in bpy.data.objects:
        data = obj.animation_data
        if data:
            if data.action: referenced.add(data.action)
            referenced.update(s.action for t in data.nla_tracks for s in t.strips if s.action)
    # Muted NLA strips remain user-editable and can be resumed. Retain their flight
    # objects; sync hides them until that exact Action becomes active again.
    remove_objects([o for o in list(bpy.data.objects) if o.get(OWNER) == owner and o not in created
                    and o.get(ACTION) not in referenced])


@persistent
def sync(scene, _depsgraph=None):
    global _busy
    from . import tasks
    if _busy or tasks.busy():
        return
    from .animation_queue import playback
    from . import projectile_mount
    from types import SimpleNamespace
    _busy = True
    try:
        owners = {o.get(OWNER) for o in bpy.data.objects if o.get(OWNER)}
        owners.update(c for c in bpy.data.collections if MASK in c)
        owners.update(c for c in bpy.data.collections if eq.CONTRACT in c)
        for owner in owners:
            rig = eq.owner_rig(owner)
            action, frame = playback(rig, scene.frame_current_final)
            objects = [o for o in bpy.data.objects if o.get(OWNER) == owner]
            active = bool(action and action.get('sora_projectile_preview'))
            if MASK in owner and owner.get(MASK_ACTION) != action:
                restore_visibility(owner)
                projectile_mount.restore(owner)
            if not active:
                restore_visibility(owner)
                projectile_mount.restore(owner)
            else:
                mapping = json.loads(action['sora_timeline_mapping'])
                seconds = (frame-mapping['frameOrigin'])/mapping['actionFps']
                held_events = json.loads(action.get('sora_projectile_held_arrow_events', '[]'))
                layer = next((v for v in scene.view_layers if rig.name in v.objects), None)
                if layer:
                    projectile_mount.apply(SimpleNamespace(scene=scene, view_layer=layer), owner, rig, action,
                        set(json.loads(action.get('sora_projectile_mount_weapons', '[]'))))
                hidden = set(json.loads(action.get('sora_projectile_hidden_slots', '[]')))
                if MASK not in owner:
                    owner[MASK] = json.dumps([{'name': c.name, 'hide': [c.hide_viewport, c.hide_render]}
                        for c in eq.owned_children(owner, 'dedicated') if c.get('sora_equipment_slot') in hidden])
                    owner[MASK_ACTION] = action
                for c in eq.owned_children(owner, 'dedicated'):
                    if c.get('sora_equipment_slot') in hidden:
                        index = int(c['sora_equipment_slot'].rsplit(':', 1)[1])
                        held = projectile_math.held_visibility(held_events, seconds, index)
                        hide = held is not True
                        if c.hide_viewport != hide: c.hide_viewport = hide
                        if c.hide_render != hide: c.hide_render = hide
            for obj in objects:
                visible = False
                if active and obj.get(ACTION) == action:
                    mapping = json.loads(action['sora_timeline_mapping'])
                    seconds = (frame-mapping['frameOrigin'])/mapping['actionFps']
                    visible, position = projectile_math.sample(json.loads(obj['sora_projectile_trajectory']), seconds)
                    obj.location = position
                if obj.hide_viewport == visible:
                    obj.hide_viewport = not visible
                if obj.hide_render == visible:
                    obj.hide_render = not visible
    finally:
        _busy = False


@persistent
def reload(_):
    ensure_visuals()
    for scene in bpy.data.scenes:
        sync(scene)


def register():
    if not bpy.app.timers.is_registered(ensure_visuals):
        bpy.app.timers.register(ensure_visuals, first_interval=0)
    for handlers, fn in ((bpy.app.handlers.frame_change_post, sync), (bpy.app.handlers.load_post, reload),
                         (bpy.app.handlers.undo_post, reload)):
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    from . import projectile_mount
    if bpy.app.timers.is_registered(ensure_visuals):
        bpy.app.timers.unregister(ensure_visuals)
    for handlers, fn in ((bpy.app.handlers.frame_change_post, sync), (bpy.app.handlers.load_post, reload),
                         (bpy.app.handlers.undo_post, reload)):
        if fn in handlers:
            handlers.remove(fn)
    for owner in bpy.data.collections:
        restore_visibility(owner)
        projectile_mount.restore(owner)
    for obj in bpy.data.objects:
        if obj.get(OWNER):
            obj.hide_viewport = obj.hide_render = True
