"""Load a SkillData authored skill inside the existing animation and equipment flows."""
import json
import uuid
import bpy
from . import equipment as eq
from . import equipment_animation_contract as contract
from . import equipment_animation_load as load
from .client import CoreError, request

FRAME_RATE = 30.0


def sources(context):
    owner = eq.owner_collection(context)
    if owner is None or eq.CONTRACT not in owner:
        raise ValueError('实例缺少原生装备装配信息，请先导入角色')
    assembly = json.loads(owner[eq.CONTRACT])
    rows = []
    for source in contract.sources(assembly):
        if len(source['controllers']) > 1:
            raise ValueError('该装备有多个原生控制器，技能窗口尚不支持：' + source['slotId'])
        if not source['controllers']:
            # A dedicated resource without an Animator still participates in the owner body/visibility
            # workflow. There is no native equipment clip to bake, so keep the slot out of the window list
            # instead of inventing an equipment Action or controller identity.
            continue
        selector = {key: source[key] for key in ('slotId', 'resourceId')}
        selector.update(source['controllers'][0])
        rows.append({'equipment': selector, 'resource': source['resourcePath'], 'slotId': source['slotId']})
    return owner, rows


def clip_resource(asset_path):
    """A montage names a manifest asset; the clip importer takes the resource path that holds it."""
    return asset_path.split('##', 1)[0]


def refresh_assembly(context, rig, owner):
    """Re-read the owner's native equipment assembly so the stored contract matches the running Core.

    Only this owner's contract is replaced; objects, actions and materials are untouched."""
    from . import equipment as eq
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        raise CoreError('Endfield-Bridge preferences are unavailable')
    assembly = request(bpy.path.abspath(addon.preferences.executable), 'equipment-assembly',
                       root=bpy.path.abspath(context.scene.sora.game_root), path=rig['sora_database'],
                       asset=rig['sora_asset'])
    if not isinstance(assembly, dict) or not assembly.get('characterId') or 'slots' not in assembly:
        raise CoreError('原生装备装配重读失败')
    owner[eq.CONTRACT] = json.dumps(assembly, separators=(',', ':'))
    return assembly


def resolve(context, rig, skill):
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        raise CoreError('Endfield-Bridge preferences are unavailable')
    root = bpy.path.abspath(context.scene.sora.game_root)
    if not root.strip():
        raise ValueError('Choose the native Game Folder')
    initial = eq.owner_collection(context)
    if initial is None or eq.CONTRACT not in initial:
        raise ValueError('实例缺少原生装备装配信息，请先导入角色')
    refresh_assembly(context, rig, initial)
    owner, rows = sources(context)
    assembly = json.loads(owner[eq.CONTRACT])
    resource = rows[0]['resource'] if rows else next((row['resourcePath'] for row in assembly.get('resources', [])
                                                       if isinstance(row.get('resourcePath'), str) and row['resourcePath']), '')
    if not resource:
        raise ValueError('技能缺少可追溯的专用装备资源身份')
    parameters = {'root': root, 'path': rig['sora_database'], 'asset': rig['sora_asset'],
                  'resource': resource, 'skill': skill, 'equipment': rows[0]['equipment'] if rows else {},
                  'parameters': [],
                  'equipments': [{'equipment': row['equipment'], 'resource': row['resource']} for row in rows]}
    plan = request(bpy.path.abspath(addon.preferences.executable), 'skill-equipment-windows', **parameters)
    if plan.get('complete') is not True:
        raise CoreError('技能时间轴未完整消费：' + str(plan.get('incompleteReason')))
    body = [row for row in (plan.get('bodyClips') or {}).get('resolved') or [] if row.get('status') == 'resolved']
    if len(body) != 1:
        raise ValueError('技能没有唯一已校验的身体片段来源（montage→manifest→clip）')
    plan = dict(plan)
    plan['equipmentScope'] = ('native-equipment-controllers' if rows
                              else 'body-only-no-native-equipment-animator')
    return owner, rows, plan, body[0]


def requests(context, rig, skill):
    owner, rows, plan, body = resolve(context, rig, skill)
    parameters = {'root': bpy.path.abspath(context.scene.sora.game_root), 'path': rig['sora_database'],
                  'asset': rig['sora_asset'], 'resource': clip_resource(body['resourcePath']),
                  'selection': {'cab': body['cab'], 'pathId': body['pathId']}}
    jobs = [{'key': 'body', 'method': 'animation-import', 'params': parameters}]
    targets = []
    by_slot = {row['slotId']: row for row in rows}
    body_length = body.get('length')
    if not isinstance(body_length, (int, float)) or isinstance(body_length, bool) or body_length <= 0:
        raise ValueError('技能身体片段缺少原生时长，无法定位装备状态退出区间')
    body_rate = body.get('sampleRate')
    if not isinstance(body_rate, (int, float)) or isinstance(body_rate, bool) or body_rate <= 0:
        raise ValueError('技能身体片段缺少原生采样率')
    body_clip_id = '%s:%s' % (body['cab'], body['pathId'])
    for slot in plan.get('slots') or []:
        source = by_slot.get(slot['slotId'])
        if source is None or not slot.get('windows'):
            continue
        children = [child for child in eq.owned_children(owner, 'dedicated')
                    if child.get('sora_equipment_slot') == slot['slotId']]
        if len(children) != 1:
            raise ValueError('该技能所需专用装备槽尚未完整导入：' + slot['slotId'])
        child_rig = eq.owner_rig(children[0])
        if child_rig is None:
            raise ValueError('原生装备控制器没有已导入的骨架：' + slot['slotId'])
        for index, window in enumerate(slot['windows']):
            if not window.get('triggerName') or window.get('endTriggerName') is None:
                raise ValueError('技能窗口缺少原生进入/结束触发：' + slot['slotId'])
            key = 'skill:%s:%s' % (slot['slotId'], index)
            jobs.append({'key': key, 'method': 'equipment-skill-window-bake', 'params': {
                'root': parameters['root'], 'path': parameters['path'], 'asset': parameters['asset'],
                'resource': source['resource'], 'equipment': source['equipment'], 'window': window,
                'duration': body_length, 'sampleRate': body_rate, 'bodyClipId': body_clip_id}})
            targets.append({'key': key, 'rig': child_rig, 'child': children[0], 'slotId': slot['slotId'],
                            'selector': source['equipment'], 'resourcePath': source['resource'], 'window': window})
    return owner, jobs, targets, {'body': body, 'plan': plan, 'parameters': parameters}


def visibility_state(rows, weapon_index, seconds):
    """Authored request layer of one weapon at the given time.

    A window is live only inside [start, end): the native Execute requests an inner weapon state and OnEnd
    revokes that request layer, so no inverse visibility is guessed after the window ends."""
    active = [row for row in rows
              if (row['visibility']['includeAllWeapons'] or row['visibility']['weaponIndex'] == weapon_index)
              and row['startTime'] <= seconds < row['endTime']]
    if not active:
        return None
    return active[-1]['visibility']['visible']


def referenced_actions():
    """Every Action an ID still points at: object slots, NLA strips and Action ID properties."""
    bound = set()
    for obj in bpy.data.objects:
        data = obj.animation_data
        if data is None:
            continue
        if data.action is not None:
            bound.add(data.action)
        for track in data.nla_tracks:
            for strip in track.strips:
                if strip.action is not None:
                    bound.add(strip.action)
    for action in bpy.data.actions:
        previous = action.get('sora_previous_action')
        if previous is not None:
            bound.add(previous)
    return bound


def capture_state(context, owner, rigs):
    """Complete pre-call snapshot of everything this flow may change.

    Rig playback/pose/properties come from the existing equipment_animation snapshot (pose bones, action slot,
    last slot identifier, properties, constraints, root channels). This owner's equipment slots are Collections
    and are snapshotted as Collections; attachment roots are snapshotted as the real objects they are."""
    from . import equipment as eq
    from . import equipment_animation as manual
    from . import equipment_events
    state = {'owner': owner.name, 'enabled': bool(owner.get(equipment_events.ENABLED)),
             'rigs': [[rig.name, manual.capture(rig)] for rig in rigs],
             'collections': [], 'roots': []}
    for collection in eq.owned_children(owner):
        state['collections'].append({'name': collection.name,
                                     'hide': [collection.hide_viewport, collection.hide_render],
                                     'applied': collection.get(equipment_events.APPLIED),
                                     'entry': collection.get(equipment_events.ENTRY),
                                     'entry_clip': collection.get(equipment_events.ENTRY_CLIP)})
        root = next((obj for obj in collection.objects if obj.get('sora_attachment_root')), None)
        if root is None:
            continue
        state['roots'].append({'object': root, 'parent': root.parent, 'parent_type': root.parent_type,
                               'parent_bone': root.parent_bone,
                               'inverse': root.matrix_parent_inverse.copy(),
                               'basis': root.matrix_basis.copy(),
                               'policy': root.get('sora_attachment_policy')})
    return state


def restore_state(state):
    """Restore a capture_state snapshot immediately; nothing is deleted here."""
    from . import equipment_animation as manual
    from . import equipment_events
    for name, saved in state['rigs']:
        rig = bpy.data.objects.get(name)
        if rig is not None:
            manual.restore(rig, saved)
    for row in state['collections']:
        collection = bpy.data.collections.get(row['name'])
        if collection is None:
            continue
        collection.hide_viewport, collection.hide_render = row['hide']
        if row['applied'] is None:
            if equipment_events.APPLIED in collection:
                del collection[equipment_events.APPLIED]
        else:
            collection[equipment_events.APPLIED] = row['applied']
        if row['entry'] is None:
            if equipment_events.ENTRY in collection:
                del collection[equipment_events.ENTRY]
        else:
            collection[equipment_events.ENTRY] = row['entry']
        if row['entry_clip'] is None:
            if equipment_events.ENTRY_CLIP in collection:
                del collection[equipment_events.ENTRY_CLIP]
        else:
            collection[equipment_events.ENTRY_CLIP] = row['entry_clip']
    for row in state['roots']:
        root = row['object']
        try:
            if root.name not in bpy.data.objects:
                continue
        except ReferenceError:
            continue
        root.parent = row['parent']
        root.parent_type = row['parent_type']
        root.parent_bone = row['parent_bone']
        root.matrix_parent_inverse = row['inverse'].copy()
        root.matrix_basis = row['basis'].copy()
        if row['policy'] is None:
            if 'sora_attachment_policy' in root:
                del root['sora_attachment_policy']
        else:
            root['sora_attachment_policy'] = row['policy']
    owner = bpy.data.collections.get(state['owner'])
    if owner is not None:
        owner[equipment_events.ENABLED] = state['enabled']
    bpy.context.view_layer.update()


def reclaim_transaction(transaction):
    """Remove only the datablocks this transaction created and nothing references any more.

    Ownership is the transaction id recorded at creation time, so a transaction that fails before its metadata
    is complete is still reclaimed; imported or user data never carries this id and is never touched."""
    keep = referenced_actions()
    removed = []
    for action in list(bpy.data.actions):
        if action.get('sora_skill_transaction') != transaction:
            continue
        if action in keep:
            continue
        name = action.name
        bpy.data.actions.remove(action, do_unlink=True)
        removed.append(name)
    return removed


def owner_equipment_collections(owner):
    """The owner's equipment slots whose playback this flow may release (dedicated and generic).

    Snapshot scope and release scope are derived from this one function so the two can never drift."""
    from . import equipment as eq
    return [collection for collection in eq.owned_children(owner)
            if collection.get('sora_equipment_role') in {'dedicated', 'generic'}]


def affected_rigs(owner, rig, targets):
    """Every rig this load can modify: the body rig, the new targets, and the equipment rigs whose previous
    playback release may touch them. Other owners are never included."""
    from . import equipment as eq
    rigs = [rig] + [target['rig'] for target in targets]
    for collection in owner_equipment_collections(owner):
        child_rig = eq.owner_rig(collection)
        if child_rig is not None and child_rig not in rigs:
            rigs.append(child_rig)
    return rigs


def release_stale_playback(owner, keep):
    """End this flow's previous skill playback for the current owner before the new request is applied.

    An Action datablock is data and stays in the file; an active binding is playback and must not keep driving
    a new skill. Only the active slot of a rig whose Action was created by this flow and that is not part of the
    current transaction is released, and that owner's slot returns to its captured baseline."""
    from . import equipment as eq
    from . import equipment_events
    released = []
    for collection in owner_equipment_collections(owner):
        rig = eq.owner_rig(collection)
        data = rig.animation_data if rig is not None else None
        action = data.action if data is not None else None
        if action is None or action in keep or action.get('sora_skill_window') is None:
            continue
        data.action = None
        if equipment_events.BASE in collection:
            equipment_events.restore(collection)
        released.append({'rig': rig.name, 'slot': collection.get('sora_equipment_slot'), 'action': action.name})
    return released


def apply_visibility(context, owner, targets, plan, fps, origin):
    """Apply the authored skill visibility through the existing equipment event state mechanism.

    Every dedicated slot of the instance is reported, including slots the skill does not cover. A failure
    restores this owner's captured attachment/visibility state and leaves the shared follow flag as it was,
    so a failed load cannot leave the current owner in a half-applied state; other owners are never touched."""
    from . import equipment as eq
    from . import equipment_events
    rows = plan['plan'].get('weaponVisibility') or []
    if not rows:
        return []
    collections = [c for c in eq.owned_children(owner) if c.get('sora_equipment_role') == 'dedicated']
    for collection in collections:
        equipment_events.capture(collection)
    previous_enabled = bool(owner.get(equipment_events.ENABLED))
    try:
        equipment_events.apply(context, owner)
    except BaseException:
        for collection in collections:
            if equipment_events.BASE in collection:
                equipment_events.restore(collection)
        owner[equipment_events.ENABLED] = previous_enabled
        raise
    owner[equipment_events.ENABLED] = True
    equipment_events.defer_sync(context.scene)
    seconds = (context.scene.frame_current_final - origin) / fps
    applied = []
    for collection in collections:
        slot = collection.get('sora_equipment_slot')
        if not slot:
            continue
        index = int(slot.rsplit(':', 1)[1])
        applied.append({'slotId': slot, 'authored': visibility_state(rows, index, seconds),
                        'visible': not collection.hide_viewport, 'skillFrame': round(seconds * FRAME_RATE, 4)})
    return applied


def apply_steps(context, rig, owner, targets, results, plan, keep_face_controls):
    from . import animation_actions as animation
    from . import equipment_animation as manual
    from . import equipment_events
    clip = load.body_clip(results['body'])
    bones = results['body']['bones']
    for target in targets:
        result = results[target['key']]
        proof = result.get('equipment') or {}
        # The window job is a native controller timeline for the whole covered interval: the entered state's
        # own clip plus the authored exit rule back to the controller's default state. A single-clip proof no
        # longer describes it, so the entered state and clip are matched by exact identity instead.
        if proof.get('proofContract') != 'native-equipment-timeline-v1':
            raise ValueError('技能装备状态转换不是原生控制器时间轴导入结果')
        identity = proof.get('identity') or {}
        if any(identity.get(key) != value for key, value in target['selector'].items()):
            raise ValueError('技能装备槽不属于当前原生槽与控制器')
        entered = [segment for segment in proof.get('segments') or []
                   if segment.get('stateName') == target['window']['stateName']
                   and segment.get('clipId') == target['window']['stateClipId']]
        if not entered:
            raise ValueError('技能装备窗口状态与已解析窗口不一致')
    timing = manual.timeline_state(context)
    old_face_mask = animation._face_mask(rig)
    # The snapshot must cover every rig this load can modify, including equipment rigs whose previous
    # playback the release step will unbind, so a failure restores their original bindings too.
    rigs = affected_rigs(owner, rig, targets)
    transaction = uuid.uuid4().hex
    snapshot = capture_state(context, owner, rigs)
    try:
        body_action = yield from animation.apply_clip_steps(context, rig, clip, [bone['name'] for bone in bones],
            bone_sources=bones, keep_face_controls=keep_face_controls, transaction=transaction)
        # Ownership is recorded at creation time: even a failure before sora_skill/sora_skill_window metadata
        # still identifies this transaction's own data for cleanup.
        body_action['sora_skill_transaction'] = transaction
        mapping = json.loads(body_action.get('sora_timeline_mapping', '{}'))
        if 'actionFps' not in mapping or 'frameOrigin' not in mapping:
            raise ValueError('导入的身体动作缺少时间轴映射，无法定位技能窗口')
        fps = float(mapping['actionFps'])
        origin = float(mapping['frameOrigin'])
        # The previous skill workflow's playback ends before the new request is applied, so an old window can
        # no longer drive this skill and the new visibility below is the last writer.
        released = release_stale_playback(owner, {body_action})
        for target in targets:
            window = target['window']
            yield {'stage': 'Applying skill equipment window', 'detail': target['slotId']}
            result = results[target['key']]
            child_rig = target['rig']
            # The native controller timeline spans the whole covered interval (default state -> entered
            # state -> its native exit), so it is mapped from the body clip's own origin, not shifted.
            action = yield from animation.apply_clip_steps(context, child_rig, result['clip'],
                [bone['name'] for bone in result['bones']], bone_sources=result['bones'],
                keep_face_controls=True, timeline={'fps': fps, 'origin': origin}, transaction=transaction)
            action['sora_skill_transaction'] = transaction
            action['sora_skill_window'] = json.dumps(window, separators=(',', ':'))
            action['sora_equipment_timeline_proof'] = json.dumps(result['equipment'], separators=(',', ':'))
            action['sora_body_action'] = body_action
            action['sora_equipment_slot'] = target['slotId']
        body_action['sora_skill'] = json.dumps(plan['plan'].get('timeline') or {}, separators=(',', ':'))
        body_action['sora_skill_windows'] = json.dumps([target['window'] for target in targets], separators=(',', ':'))
        body_action['sora_skill_visibility'] = json.dumps(plan['plan'].get('weaponVisibility') or [], separators=(',', ':'))
        applied = apply_visibility(context, owner, targets, plan, fps, origin)
        body_action['sora_skill_visibility_applied'] = json.dumps(applied, separators=(',', ':'))
        body_action['sora_skill_released_actions'] = json.dumps(released, separators=(',', ':'))
        return body_action, applied, released
    except BaseException:
        animation._restore_face_mask(old_face_mask)
        restore_state(snapshot)
        reclaim_transaction(transaction)
        scene = context.scene
        scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end = timing[:4]
        scene.frame_set(timing[4], subframe=timing[5])
        raise
