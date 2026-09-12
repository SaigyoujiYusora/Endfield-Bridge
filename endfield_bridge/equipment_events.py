"""Apply decoded body-clip equipment events to this owner's imported slots."""
import json
import bpy
from bpy.app.handlers import persistent
from . import equipment as eq, tasks

ENABLED = 'sora_equipment_events_enabled'
STATUS = 'sora_equipment_events_status'
BASE = 'sora_equipment_event_baseline'
PARENT = 'sora_equipment_event_parent'
APPLIED = 'sora_equipment_event_applied'
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
    if APPLIED in child: del child[APPLIED]
    if clear:
        del child[BASE]
        if PARENT in child: del child[PARENT]


def pause(owner, restore_baseline=True, keep_enabled=False):
    if not keep_enabled: owner[ENABLED] = False
    for child in eq.owned_children(owner):
        if child.get('sora_equipment_role') not in {'dedicated', 'generic'}:
            continue
        if restore_baseline:
            restore(child, clear=True)
        else:
            for key in (BASE, PARENT, APPLIED):
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


def apply(context, owner):
    from . import generic_weapons
    assembly = json.loads(owner[eq.CONTRACT])
    children = {(child['sora_equipment_role'], child['sora_equipment_slot']): child
                for child in eq.owned_children(owner)
                if child.get('sora_equipment_role') in {'dedicated', 'generic'}}
    events, message, authored = body_events(context.scene, owner, assembly)
    if events is None:
        for child in children.values():
            if APPLIED in child: restore(child)
        status(owner, message)
        return
    planned = {}
    for key, child in children.items():
        baseline = capture(child)
        planned[key] = {'mount': None, 'viewport': baseline['viewport'], 'render': baseline['render']}
    unsupported = set()
    for event in events:
        key = event['targetRole'], event.get('slotId')
        if event.get('targetStatus') != 'native-declaration-slot':
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
            planned[key].update(viewport=True, render=True)
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
    if events:
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
                with bpy.context.temp_override(scene=scene, view_layer=view_layer):
                    apply(bpy.context, owner)
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
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
