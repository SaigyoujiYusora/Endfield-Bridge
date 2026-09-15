"""Apply decoded body-clip equipment events to this owner's imported slots."""
import json
from types import SimpleNamespace
import bpy
from bpy.app.handlers import persistent
from . import equipment as eq, tasks

ENABLED = 'sora_equipment_events_enabled'
STATUS = 'sora_equipment_events_status'
BASE = 'sora_equipment_event_baseline'
PARENT = 'sora_equipment_event_parent'
APPLIED = 'sora_equipment_event_applied'
ENTRY = 'sora_equipment_event_entry'
ENTRY_CLIP = 'sora_equipment_event_entry_clip'
_busy = False
_pending_scenes = []


def _flush_pending():
    if tasks.busy(): return 0.05
    pending = list(_pending_scenes)
    _pending_scenes.clear()
    for scene in pending: sync(scene)
    return None


def defer_sync(scene):
    if scene not in _pending_scenes: _pending_scenes.append(scene)
    if not bpy.app.timers.is_registered(_flush_pending):
        bpy.app.timers.register(_flush_pending, first_interval=0.0)


def status(owner, value):
    if owner.get(STATUS) != value: owner[STATUS] = value


def root_for(child):
    return next((obj for obj in child.objects if obj.get('sora_attachment_root')), None)


def capture(child):
    root = root_for(child)
    if root is None:
        raise ValueError('装备槽缺少挂点根对象：' + child.name)
    if BASE not in child:
        child[BASE] = json.dumps({'parentType': root.parent_type, 'parentBone': root.parent_bone,
            'basis': eq.flatten(root.matrix_basis), 'inverse': eq.flatten(root.matrix_parent_inverse),
            'viewport': child.hide_viewport, 'render': child.hide_render,
            'metadata': {key: root.get(key) for key in ('sora_attachment_node_id',
                'sora_attachment_source_path', 'sora_attachment_error')}})
        if root.parent is not None:
            child[PARENT] = root.parent
    return json.loads(child[BASE])


def restore(child, clear=False):
    if BASE not in child:
        return
    baseline = json.loads(child[BASE])
    root = root_for(child)
    if root is not None:
        root.parent = child.get(PARENT)
        root.parent_type = baseline['parentType']
        root.parent_bone = baseline['parentBone']
        root.matrix_parent_inverse = eq.mat(baseline['inverse'])
        root.matrix_basis = eq.mat(baseline['basis'])
        for key, value in baseline['metadata'].items():
            if value is None:
                if key in root: del root[key]
            else:
                root[key] = value
    child.hide_viewport, child.hide_render = baseline['viewport'], baseline['render']
    if root is not None and 'sora_attachment_policy' in root:
        del root['sora_attachment_policy']
    if APPLIED in child: del child[APPLIED]
    if clear:
        del child[BASE]
        if PARENT in child: del child[PARENT]
        for key in (ENTRY, ENTRY_CLIP):
            if key in child: del child[key]


def bound_clip(owner):
    """Stable identity of the body clip bound to this owner, None when nothing is bound.

    The identity is the clip's own persisted owner UUID. A datablock pointer does not survive save/reopen,
    so using it would make clip_entry treat the same clip as a brand-new one and re-freeze the entry from
    whatever state is applied at load time, losing the real clip-entry mount and visibility. Renaming or
    retiming the bound Action must not refreeze its entry, while a rebuilt or re-imported clip is a
    different datablock and does. An Action this add-on did not create carries no owner UUID and is not a
    body clip here."""
    rig = eq.owner_rig(owner)
    animation = rig.animation_data if rig is not None else None
    action = animation.action if animation is not None else None
    identity = action.get('sora_animation_owner') if action is not None else None
    return str(identity) if identity else None


def legacy_clip_marker(value):
    """True when a stored ENTRY_CLIP is exactly the pre-UUID form written by the old implementation.

    The old marker was str(action.as_pointer()): the decimal datablock address, a 64-bit value of at most
    20 digits. Only that shape is accepted, so an arbitrary or corrupt string is never migrated and a
    32-character owner UUID (which can itself be all digits) can never be mistaken for a legacy pointer.
    """
    text = str(value)
    return 0 < len(text) <= 20 and all('0' <= ch <= '9' for ch in text)


def clip_entry(child, clip, carry):
    """Freeze the slot state a native-event body clip is entered with, once per bound body clip.

    A clip that declares equipment events replays its authored events over that frozen entry state, so a
    frame before its first event rebuilds to the state the clip really started from instead of the manual
    baseline captured earlier, and scrubbing backwards cannot keep the last applied event state. A clip
    without authored events drops the entry and keeps using that manual baseline."""
    if clip is None:
        return False
    stored = child.get(ENTRY_CLIP)
    if stored == clip:
        return carry and ENTRY in child
    # A file written before the identity became the Action owner UUID holds the datablock pointer, which
    # cannot be recomputed after reopening. Its ENTRY was frozen at the real clip entry, so keep that entry
    # and upgrade the marker instead of re-freezing it from the state applied at load time.
    if carry and ENTRY in child and stored is not None and legacy_clip_marker(stored):
        child[ENTRY_CLIP] = clip
        return True
    child[ENTRY_CLIP] = clip
    if not carry:
        if ENTRY in child: del child[ENTRY]
        return False
    root = root_for(child)
    if root is None:
        return False
    child[ENTRY] = json.dumps({'parent': root.parent.name if root.parent is not None else '',
        'parentType': root.parent_type, 'parentBone': root.parent_bone,
        'basis': eq.flatten(root.matrix_basis), 'inverse': eq.flatten(root.matrix_parent_inverse),
        'policy': root.get('sora_attachment_policy'),
        'metadata': {key: root.get(key) for key in ('sora_attachment_node_id',
            'sora_attachment_source_path', 'sora_attachment_error')},
        'viewport': child.hide_viewport, 'render': child.hide_render})
    return True


def restore_entry(child):
    """Rebuild this slot from its frozen clip entry state; False when no entry is available."""
    if ENTRY not in child:
        return False
    entry = json.loads(child[ENTRY])
    root = root_for(child)
    if root is not None:
        root.parent = bpy.data.objects.get(entry['parent']) if entry['parent'] else None
        root.parent_type = entry['parentType']
        root.parent_bone = entry['parentBone']
        root.matrix_parent_inverse = eq.mat(entry['inverse'])
        root.matrix_basis = eq.mat(entry['basis'])
        if entry['policy'] is None:
            if 'sora_attachment_policy' in root: del root['sora_attachment_policy']
        else:
            root['sora_attachment_policy'] = entry['policy']
        for key, value in entry['metadata'].items():
            if value is None:
                if key in root: del root[key]
            else:
                root[key] = value
    child.hide_viewport, child.hide_render = entry['viewport'], entry['render']
    return True


def pause(owner, restore_baseline=True, keep_enabled=False):
    if not keep_enabled: owner[ENABLED] = False
    for child in eq.owned_children(owner):
        if child.get('sora_equipment_role') not in {'dedicated', 'generic'}:
            continue
        if restore_baseline:
            restore(child, clear=True)
        else:
            for key in (BASE, PARENT, APPLIED, ENTRY, ENTRY_CLIP):
                if key in child: del child[key]
    if keep_enabled:
        status(owner, '前端已卸载：已恢复静态基线；跟随意图已保留，重新加载后自动恢复')
    else:
        status(owner, '已暂停身体事件跟随；当前实例使用手动静态状态')


def body_events(scene, owner, assembly):
    rig = eq.owner_rig(owner)
    animation = rig.animation_data if rig else None
    action = animation.action if animation else None
    if action is None or rig.get('sora_pose_resume'):
        return None, '身体动作未绑定或已挂起；恢复静态基线', 0
    if action.get('sora_instance') != rig.get('sora_instance'):
        return None, '身体动作不属于当前实例；恢复静态基线', 0
    if any(not track.mute for track in animation.nla_tracks):
        return None, '身体 NLA 混合时间尚不支持事件跟随；恢复静态基线', 0
    metadata = json.loads(action.get('sora_clip_metadata', '{}'))
    source = (metadata.get('native') or {}).get('source') or {}
    identity = str(source.get('cab', '')) + ':' + str(source.get('pathId', ''))
    matches = [clip for clip in (assembly.get('animationConfig') or {}).get('clips') or []
               if clip.get('sourceId') == identity]
    if not matches:
        return None, ('身体片段不在角色原生控制器片段集内（montage/gameplay 驱动动作）：原生武器事件不可解析；'
              '保持手动静态基线'), 0
    if len(matches) != 1 or matches[0].get('decodedWeaponEvents') is None:
        return None, '身体源片段缺少唯一已解码武器事件记录；请重新加载装备关联', 0
    timeline = json.loads(action.get('sora_timeline_mapping', '{}'))
    fps = timeline.get('actionFps', metadata.get('fps'))
    origin = timeline.get('frameOrigin', 1)
    if not fps or fps <= 0:
        return None, '身体源动作缺少时间轴映射', 0
    seconds = (scene.frame_current_final - origin) / fps
    authored = matches[0]['decodedWeaponEvents']
    events = [event for event in authored if event['time'] <= seconds]
    return sorted(events, key=lambda event: (event['time'], event['sourceIndex'])), '', len(authored)


HIDE_ONLY = 'hide-only'
KEEP = 'keep-mounted'
ENTRY_STATE = 'clip-entry'


def skill_slot_map(owner, assembly):
    """Authored weapon index -> (role, slot id), in the real dedicated/generic namespaces.

    A weapon index that has no imported slot is reported as such; it is never rewritten into a dedicated
    slot id that would make a generic weapon look like a missing dedicated equipment."""
    mapping = {}
    for slot in assembly.get('slots') or []:
        if isinstance(slot.get('weaponIndex'), int):
            mapping[slot['weaponIndex']] = ('dedicated', slot['slotId'])
    from . import generic_weapons
    contract = json.loads(owner.get(generic_weapons.CONTRACT, '{}'))
    for slot in contract.get('genericSlots') or []:
        name = str(slot.get('slotId', ''))
        if name.rsplit(':', 1)[-1].isdigit():
            mapping.setdefault(int(name.rsplit(':', 1)[-1]), ('generic', name))
    return mapping


def skill_visibility(scene, owner):
    """Authored skill weapon-visibility windows of the loaded body action, in body-event shape.

    A window is live only inside [start, end): the native Execute requests an inner weapon state and OnEnd
    revokes that request layer. Visible requests the native ShowInFight appear (SetParent(fight node,false) +
    SetActive(true)); a hidden request only removes the model from view. The mount dimension is never
    invented for a weapon that is not imported."""
    rig = eq.owner_rig(owner)
    action = rig.animation_data.action if rig and rig.animation_data else None
    rows = json.loads(action.get('sora_skill_visibility', '[]')) if action else []
    if not rows:
        return None
    timeline = json.loads(action.get('sora_timeline_mapping', '{}'))
    fps = timeline.get('actionFps')
    origin = timeline.get('frameOrigin')
    if not fps or not isinstance(origin, (int, float)):
        return None
    assembly = json.loads(owner.get(eq.CONTRACT, '{}')) or {}
    mapping = skill_slot_map(owner, assembly)
    seconds = (scene.frame_current_final - origin) / fps
    columns = {}
    for index, row in enumerate(rows):
        visibility = row['visibility']
        key = None if visibility['includeAllWeapons'] else visibility['weaponIndex']
        columns.setdefault(key, []).append((index, visibility, row))
    events = []
    for weapon_index, column in columns.items():
        active = [entry for entry in column if entry[2]['startTime'] <= seconds < entry[2]['endTime']]
        if not active:
            continue
        index, visibility, row = active[-1]
        if weapon_index is None:
            role, slot_id = 'include-all', None
        else:
            role, slot_id = mapping.get(weapon_index, (None, None))
        events.append({'time': row['startTime'], 'sourceIndex': index, 'targetRole': role or 'unimported',
                       'slotId': slot_id, 'functionName': 'WeaponVisible',
                       'modelVisible': visibility['visible'],
                       'requestedMountState': 'fight' if visibility['visible'] else HIDE_ONLY,
                       'targetStatus': 'native-declaration-slot' if slot_id else 'skill-weapon-not-imported',
                       'sourceClipId': None, 'hideWithEffect': False, 'visible': visibility['visible'],
                       'weaponIndex': weapon_index})
    return sorted(events, key=lambda event: (event['time'], event['sourceIndex']))


def apply(context, owner):
    from . import generic_weapons
    assembly = json.loads(owner[eq.CONTRACT])
    children = {(child['sora_equipment_role'], child['sora_equipment_slot']): child
                for child in eq.owned_children(owner)
                if child.get('sora_equipment_role') in {'dedicated', 'generic'}}
    events, message, authored = body_events(context.scene, owner, assembly)
    skill = skill_visibility(context.scene, owner) if events is None else None
    if skill is not None:
        events, message, authored = skill, '技能原生显隐窗口跟随；作者恢复语义未确认', len(skill)
    if events is None:
        for child in children.values():
            if APPLIED in child: restore(child)
        status(owner, message)
        return
    clip = bound_clip(owner)
    carried = skill is None and bool(authored)
    planned = {}
    for key, child in children.items():
        baseline = capture(child)
        if clip_entry(child, clip, carried):
            entry = json.loads(child[ENTRY])
            planned[key] = {'mount': ENTRY_STATE, 'viewport': entry['viewport'], 'render': entry['render']}
            continue
        # Without a live skill window the slot keeps its captured baseline; a live window either appears on
        # the authored fight node or is hidden without touching the mount.
        planned[key] = {'mount': 'baseline' if skill is not None else None,
                        'viewport': baseline['viewport'], 'render': baseline['render']}
    unsupported = set()
    for event in events:
        key = event['targetRole'], event.get('slotId')
        if event.get('targetStatus') != 'native-declaration-slot':
            if event.get('targetStatus') == 'skill-weapon-not-imported':
                unsupported.add('技能显隐引用的武器索引未导入：' + str(event.get('weaponIndex')))
            else:
                unsupported.add('事件武器索引缺少原生槽映射')
            continue
        if key not in children:
            if key[0] != 'generic': unsupported.add('事件指定的专用装备尚未导入')
            continue
        if event['functionName'] == 'WeaponAnim':
            child_rig = eq.owner_rig(children[key])
            action = child_rig.animation_data.action if child_rig and child_rig.animation_data else None
            proof = json.loads(action.get('sora_equipment_timeline_proof', '{}')) if action else {}
            body_rig = eq.owner_rig(owner)
            body_action = body_rig.animation_data.action if body_rig.animation_data else None
            if not (action and action.get('sora_body_action') == body_action
                    and proof.get('bodyClipId') == event['sourceClipId']
                    and (proof.get('identity') or {}).get('slotId') == key[1]):
                unsupported.add('WeaponAnim 尚未加载对应身体片段的原生装备时间轴')
            continue
        if event.get('modelVisible') is True:
            planned[key].update(mount=event['requestedMountState'], viewport=False, render=False)
        elif event.get('modelVisible') is False:
            planned[key].update(mount=HIDE_ONLY, viewport=True, render=True)
        else:
            unsupported.add('事件缺少已解码模型显隐状态；请重新加载装备关联')
        if event.get('hideWithEffect') and event.get('visible') is False:
            unsupported.add('隐藏特效未模拟；模型显隐已按原生事件执行')
    dedicated = {slot['slotId']: slot for slot in assembly['slots']}
    generic = {slot['slotId']: slot for slot in json.loads(owner.get(generic_weapons.CONTRACT, '{}')).get('genericSlots', [])}
    declarations = {slot['slotId']: slot for slot in assembly['declaration']['dedicatedEquipment']}
    for key, child in children.items():
        desired = planned[key]
        signature = json.dumps(desired, sort_keys=True)
        if child.get(APPLIED) == signature:
            continue
        root = root_for(child)
        if desired['mount'] is None:
            restore(child)
        elif desired['mount'] == ENTRY_STATE:
            # Rebuild from the frozen clip entry so a frame before this clip's first event stays
            # deterministic and independent of which frame was applied last.
            if not restore_entry(child): restore(child)
        elif desired['mount'] == HIDE_ONLY:
            # A hidden request removes the model from view; the native hide path does not reparent it.
            child.hide_viewport, child.hide_render = desired['viewport'], desired['render']
        elif desired['mount'] == 'baseline':
            # The authored window ended and its request layer is revoked (native OnEnd DisablePriority), so the
            # slot returns to the state captured when the layer was requested, mount included. Restoring the
            # model visibility alone would leave a weapon that appeared on the fight node attached there even
            # when the lower-priority State asks for its own node.
            restore(child)
        else:
            target = (dedicated if key[0] == 'dedicated' else generic)[key[1]][desired['mount']]
            if not target.get('canBind'):
                unsupported.add('事件挂点缺少可绑定的原生数据：' + key[1])
                continue
            child.hide_viewport = False
            if key[0] == 'dedicated':
                eq.bind(context, eq.owner_rig(owner), root, target, declarations[key[1]]['scale'])
            else:
                generic_weapons.bind(context, owner, root, target)
        child.hide_viewport, child.hide_render = desired['viewport'], desired['render']
        child[APPLIED] = signature
    baseline = {'idle': 'Idle', 'fight': 'Fight'}.get(owner.get(eq.STATE), '未选择')
    if skill is not None:
        text = ('技能原生显隐窗口跟随：visible 按 ShowInFight 原生挂点出现（_AppearOnNodeUnsafe），'
                'hidden 仅隐藏，窗口结束撤销请求层回到基线（OnEnd DisablePriority）')
    elif events or carried:
        text = '身体原生事件跟随：逐槽 Idle/Fight 挂点与模型显隐'
    elif authored == 0:
        text = '该身体片段没有原生装备显隐事件；保持手动静态基线：' + baseline + '（可用 Idle/Fight 静态按钮显式选择）'
    else:
        text = '当前时间之前没有武器事件；保持手动静态基线：' + baseline
    status(owner, text + ('；未完成：' + '；'.join(sorted(unsupported)) if unsupported else ''))


def sync(scene):
    global _busy
    if _busy or tasks.busy(): return
    _busy = True
    try:
        for owner in bpy.data.collections:
            if not owner.get(ENABLED) or eq.CONTRACT not in owner:
                continue
            rig = eq.owner_rig(owner)
            if rig is None or rig.name not in scene.objects: continue
            view_layer = next((layer for layer in scene.view_layers if rig.name in layer.objects), None)
            if view_layer is None: continue
            try:
                # Frame callbacks also run on the render job thread. Changing
                # bpy.context here races the UI's reads of its Python context.
                # The binding helpers only need these explicit scene inputs.
                apply(SimpleNamespace(scene=scene, view_layer=view_layer), owner)
            except (ValueError, KeyError, TypeError, RuntimeError, OverflowError) as error:
                for child in eq.owned_children(owner):
                    if BASE in child: restore(child)
                status(owner, '身体事件未应用：' + str(error))
    finally:
        _busy = False


@persistent
def changed(scene, _depsgraph=None):
    sync(scene)


@persistent
def reloaded(_):
    eq.compact_stored_contracts(bpy.data.collections)
    for scene in bpy.data.scenes: sync(scene)


class SORA_OT_equipment_events(bpy.types.Operator):
    bl_idname = 'sora.equipment_events'
    bl_label = '跟随 / 暂停身体装备事件'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        owner = eq.owner_collection(context)
        if owner is None or eq.CONTRACT not in owner: return {'CANCELLED'}
        if owner.get(ENABLED):
            pause(owner)
        else:
            for child in eq.owned_children(owner):
                if child.get('sora_equipment_role') in {'dedicated', 'generic'}: capture(child)
            owner[ENABLED] = True
            sync(context.scene)
        return {'FINISHED'}


def draw(layout, context, owner):
    from . import wrapped_label
    layout.operator('sora.equipment_events', text='暂停身体事件跟随' if owner.get(ENABLED) else '恢复身体事件跟随')
    wrapped_label(layout, owner.get(STATUS, '身体事件跟随未开启'), context)


def _resume():
    eq.compact_stored_contracts(bpy.data.collections)
    for scene in bpy.data.scenes: defer_sync(scene)
    return None


def register():
    bpy.utils.register_class(SORA_OT_equipment_events)
    for handlers, callback in ((bpy.app.handlers.frame_change_post, changed),
            (bpy.app.handlers.depsgraph_update_post, changed), (bpy.app.handlers.load_post, reloaded),
            (bpy.app.handlers.undo_post, reloaded)):
        if callback not in handlers: handlers.append(callback)
    if not bpy.app.timers.is_registered(_resume): bpy.app.timers.register(_resume, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_flush_pending): bpy.app.timers.unregister(_flush_pending)
    if bpy.app.timers.is_registered(_resume): bpy.app.timers.unregister(_resume)
    _pending_scenes.clear()
    for handlers, callback in ((bpy.app.handlers.frame_change_post, changed),
            (bpy.app.handlers.depsgraph_update_post, changed), (bpy.app.handlers.load_post, reloaded),
            (bpy.app.handlers.undo_post, reloaded)):
        if callback in handlers: handlers.remove(callback)
    for owner in bpy.data.collections:
        if owner.get(ENABLED): pause(owner, keep_enabled=True)
    bpy.utils.unregister_class(SORA_OT_equipment_events)
