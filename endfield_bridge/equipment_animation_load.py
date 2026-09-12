"""Load body and native equipment timelines within one existing import task."""
import json
import bpy
from . import equipment as eq
from . import equipment_animation as manual
from . import equipment_animation_contract as contract
from . import animation_actions as animation


def capture_slot_state(child, root):
    """Physical state of one equipment slot's attachment root and collection visibility.

    The event baseline (BASE) is a manual-state record and is deliberately not reused here: restoring a
    failed import must return the slot to the exact parent/transform/metadata/visibility it had when this
    import started, including a visible fight mount, not to a previously captured static baseline."""
    from . import equipment_events
    return {
        'parent': root.parent,
        'parent_type': root.parent_type,
        'parent_bone': root.parent_bone,
        'inverse': root.matrix_parent_inverse.copy(),
        'basis': root.matrix_basis.copy(),
        'metadata': {key: root.get(key) for key in ('sora_attachment_node_id', 'sora_attachment_source_path',
                                                     'sora_attachment_error', 'sora_attachment_policy')},
        'applied': (equipment_events.APPLIED in child, child.get(equipment_events.APPLIED)),
        'hide_viewport': child.hide_viewport,
        'hide_render': child.hide_render,
    }


def restore_slot_state(child, root, state):
    """Write back exactly what capture_slot_state read; no baseline inference."""
    from . import equipment_events
    root.parent = state['parent']
    root.parent_type = state['parent_type']
    root.parent_bone = state['parent_bone']
    root.matrix_parent_inverse = state['inverse']
    root.matrix_basis = state['basis']
    for key, value in state['metadata'].items():
        if value is None:
            if key in root:
                del root[key]
        else:
            root[key] = value
    child.hide_viewport = state['hide_viewport']
    child.hide_render = state['hide_render']
    present, value = state['applied']
    if present:
        child[equipment_events.APPLIED] = value
    elif equipment_events.APPLIED in child:
        del child[equipment_events.APPLIED]


def release_stale_playback(context, owner, keep, completed):
    """End this flow's previous equipment-timeline playback for the current owner.

    An Action datablock is data and stays in the file; an active binding is playback and must not keep
    driving a new body clip. Only the active slot of a rig whose Action was created by this equipment
    timeline flow is released, and that slot returns to its captured baseline. Each slot's physical state
    is captured and registered into the caller's rollback stack *before* any mutation, so a failure or
    cancellation part-way through the loop still rolls back the slots already released."""
    from . import equipment_events
    released = []
    for child in eq.owned_children(owner, 'dedicated'):
        rig = eq.owner_rig(child)
        data = rig.animation_data if rig is not None else None
        action = data.action if data is not None else None
        if rig is None or rig in keep or action is None:
            continue
        if action.get('sora_equipment_timeline_proof') is None:
            continue
        root = equipment_events.root_for(child)
        if root is None:
            raise ValueError('Equipment slot attachment root is missing: ' + child.name)
        saved = manual.capture(rig)
        entry = {'rig': rig, 'saved': saved, 'child': child, 'root': root,
                 'slot_state': capture_slot_state(child, root), 'action': action}
        completed.append((rig, saved, None, entry))
        released.append(entry)
        data.action = None
        if equipment_events.BASE in child:
            equipment_events.restore(child)
    return released


def rollback_completed(completed):
    """Undo everything an in-progress import changed, newest first.

    Shared by the import's failure path so rollback restores both the rig binding snapshot and the slot's
    physical attachment-root/visibility state."""
    for changed_rig, saved, action, slot in reversed(completed):
        if slot is not None:
            restore_slot_state(slot['child'], slot['root'], slot['slot_state'])
        manual.restore(changed_rig, saved)
        if action is not None:
            bpy.data.actions.remove(action)


def requests(context, rig, parameters):
    jobs = [{'key': 'body', 'method': 'animation-import', 'params': parameters}]
    owner = eq.owner_collection(context)
    targets = []
    if owner is None or eq.CONTRACT not in owner:
        return jobs, owner, targets
    assembly = json.loads(owner[eq.CONTRACT])
    body_id = parameters['selection']['cab'] + ':' + parameters['selection']['pathId']
    body_clips = [clip for clip in (assembly.get('animationConfig') or {}).get('clips') or []
                  if clip.get('sourceId') == body_id]
    if not body_clips:
        # Core's native equipment timeline planner requires the selected body clip inside the owner
        # character controller. A montage/gameplay driven body clip is absent there, so no equipment
        # timeline is requested: the body clip still loads and every slot keeps its authored manual
        # baseline instead of a fabricated equipment animation.
        return jobs, owner, []
    needed = {event.get('slotId') for clip in body_clips for event in clip.get('decodedWeaponEvents') or []
              if event['functionName'] == 'WeaponAnim' and event['targetRole'] == 'dedicated'}
    for source in contract.sources(assembly):
        slot = source['slotId']
        children = [child for child in eq.owned_children(owner, 'dedicated') if child.get('sora_equipment_slot') == slot]
        if len(children) != 1:
            raise ValueError('身体动作所需专用装备槽尚未完整导入：' + slot)
        controllers = source['controllers']
        if not controllers:
            if slot in needed: raise ValueError('身体事件指定装备缺少原生控制器：' + slot)
            continue
        if len(controllers) != 1:
            raise ValueError('该装备有多个原生控制器，自动时间轴尚不支持：' + slot)
        child = children[0]
        child_rig = eq.owner_rig(child)
        if child_rig is None:
            raise ValueError('原生装备控制器没有已导入的骨架：' + slot)
        selector = {key: source[key] for key in ('slotId', 'resourceId')}
        selector.update(controllers[0])
        key = 'equipment:' + slot
        jobs.append({'key': key, 'method': 'equipment-animation-bake', 'params': {
            'path': parameters['path'], 'root': parameters['root'], 'asset': parameters['asset'],
            'resource': source['resourcePath'], 'equipment': selector, 'bodySelection': parameters['selection']}})
        targets.append({'key': key, 'rig': child_rig, 'child': child, 'selector': selector,
                        'resourcePath': source['resourcePath'], 'bodyClipId': body_id})
    return jobs, owner, targets


def body_clip(result):
    clip = result['clip']
    metadata = {key: value for key, value in result.items() if key not in {'clip', 'bones'}}
    if metadata:
        clip = dict(clip)
        native = dict(clip.get('native') or {})
        for key, value in metadata.items():
            if key in native and native[key] != value:
                raise ValueError('Conflicting native animation metadata: ' + key)
            native[key] = value
        clip['native'] = native
    return clip


def apply_steps(context, rig, owner, targets, results, keep_face_controls):
    clip = body_clip(results['body'])
    bones = results['body']['bones']
    for target in targets:
        result = results[target['key']]
        proof = result['equipment']
        if proof.get('proofContract') != 'native-equipment-timeline-v1' or proof.get('bodyClipId') != target['bodyClipId']:
            raise ValueError('装备烘焙时间轴与所选身体源片段不一致')
        if any(proof['identity'].get(key) != value for key, value in target['selector'].items()):
            raise ValueError('装备烘焙结果不属于当前原生槽与控制器')
    timing = manual.timeline_state(context)
    old_face_mask = animation._face_mask(rig)
    completed = []
    try:
        saved = manual.capture(rig)
        body_action = yield from animation.apply_clip_steps(context, rig, clip, [bone['name'] for bone in bones],
            bone_sources=bones, keep_face_controls=keep_face_controls)
        completed.append((rig, saved, body_action, None))
        mapping = json.loads(body_action.get('sora_timeline_mapping', '{}'))
        timeline = {'fps': mapping.get('actionFps', context.scene.render.fps / context.scene.render.fps_base),
                    'origin': mapping.get('frameOrigin', context.scene.frame_start)}
        released = release_stale_playback(context, owner, {target['rig'] for target in targets}, completed)
        for target in targets:
            yield {'stage': 'Applying native equipment timeline', 'detail': target['selector']['slotId']}
            result = results[target['key']]
            child_rig = target['rig']
            saved = manual.capture(child_rig)
            action = yield from animation.apply_clip_steps(context, child_rig, result['clip'],
                [bone['name'] for bone in result['bones']], bone_sources=result['bones'],
                keep_face_controls=True, timeline=timeline)
            completed.append((child_rig, saved, action, None))
            action['sora_equipment_timeline_proof'] = json.dumps(result['equipment'], separators=(',', ':'))
            action['sora_body_action'] = body_action
            action['sora_equipment_slot'] = target['selector']['slotId']
        body_action['sora_equipment_timeline_slots'] = json.dumps([target['selector']['slotId'] for target in targets])
        body_action['sora_equipment_timeline_released'] = json.dumps(
            [{'rig': entry['rig'].name, 'slot': entry['child'].get('sora_equipment_slot'),
              'action': entry['action'].name} for entry in released], separators=(',', ':'))
        from . import equipment_events
        equipment_events.defer_sync(context.scene)
        return body_action
    except BaseException:
        animation._restore_face_mask(old_face_mask)
        rollback_completed(completed)
        scene = context.scene
        scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end = timing[:4]
        scene.frame_set(timing[4], subframe=timing[5])
        raise
