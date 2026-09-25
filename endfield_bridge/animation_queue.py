"""User-ordered native clips, committed as independent NLA strips in one transaction."""
import json
import math
import uuid
import bpy
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty
from . import tasks, equipment as eq, equipment_animation as manual, animation_actions as animation
from . import equipment_animation_load as loader
from .tasks import TaskOperator


class SORA_QueuedClip(bpy.types.PropertyGroup):
    resource: StringProperty()
    cab: StringProperty()
    path_id: StringProperty()


class SORA_AnimationQueue(bpy.types.PropertyGroup):
    owner: PointerProperty(type=bpy.types.Object)
    root: StringProperty()
    database: StringProperty()
    asset: StringProperty()
    items: CollectionProperty(type=SORA_QueuedClip)
    selected: IntProperty(default=-1)
    start: IntProperty(name='起始帧', default=1, min=1, max=1048573)
    status: StringProperty(default='逐条加入，可重复添加；上移/下移决定播放顺序')


def identity(context, rig):
    return (rig, bpy.path.abspath(context.scene.sora.game_root), rig['sora_database'], rig['sora_asset'])


def signature(settings):
    return (settings.owner, settings.root, settings.database, settings.asset, settings.start,
            tuple((r.name, r.resource, r.cab, r.path_id) for r in settings.items))


def owned_track(track, rig):
    return bool(track.strips) and all(s.type == 'CLIP' and s.action and s.action.get('sora_sequence')
        and s.action.get('sora_instance') == rig.get('sora_instance') for s in track.strips)


def playback(rig, frame):
    """Resolve our non-blended strips to source Action time; arbitrary NLA stays unsupported."""
    data = rig.animation_data if rig else None
    if data is None:
        return None, frame
    tracks = [t for t in data.nla_tracks if not t.mute] if data.use_nla else []
    if data.action is not None:
        return (None, frame) if tracks else (data.action, frame)
    if len(tracks) != 1 or not owned_track(tracks[0], rig):
        return None, frame
    strips = list(tracks[0].strips)
    if strips and frame < strips[0].frame_start and strips[0].extrapolation == 'HOLD':
        frame = strips[0].frame_start
    for index, strip in enumerate(strips):
        if strip.mute or strip.blend_type != 'REPLACE' or strip.use_animated_time or strip.use_animated_influence:
            continue
        if strip.repeat != 1 or strip.influence != 1 or strip.blend_in or strip.blend_out:
            continue
        if strip.frame_start <= frame < strip.frame_end or (index == len(strips)-1 and frame == strip.frame_end):
            return strip.action, strip.action_frame_start + (frame-strip.frame_start)/strip.scale
    return None, frame


class SORA_OT_queue_add(bpy.types.Operator):
    bl_idname = 'sora.animation_queue_add'
    bl_label = '加入动画队列'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .animation_panel import target
        try:
            if tasks.busy(): raise ValueError('请等待当前任务完成')
            rig = target(context)
            if rig is None: raise ValueError('请选择角色')
            source, queue = context.scene.sora_animation, context.scene.sora_animation_queue
            if source.result_owner != rig or source.result_root != identity(context, rig)[1]:
                raise ValueError('请为当前角色重新搜索动画')
            if not 0 <= source.selected < len(source.rows) or not 0 <= source.selected_clip < len(source.clips):
                raise ValueError('请先读取资源片段并选择要加入的片段')
            resource = source.rows[source.selected].resource_path
            if source.clip_resource != resource or source.clip_root != source.result_root:
                raise ValueError('请先读取当前资源的片段')
            current = identity(context, rig)
            if queue.items and current != (queue.owner, queue.root, queue.database, queue.asset):
                raise ValueError('队列属于其他角色或数据源，请先清空队列')
            queue.owner, queue.root, queue.database, queue.asset = current
            clip = source.clips[source.selected_clip]
            row = queue.items.add()
            row.name, row.resource, row.cab, row.path_id = clip.name, resource, clip.cab, clip.path_id
            queue.selected = len(queue.items)-1
            queue.status = f'队列共 {len(queue.items)} 条；按列表顺序播放，可继续添加或排序'
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}


class SORA_OT_queue_edit(bpy.types.Operator):
    bl_idname = 'sora.animation_queue_edit'
    bl_label = '编辑动画队列'
    bl_options = {'REGISTER', 'UNDO'}
    operation: StringProperty()

    def execute(self, context):
        if tasks.busy(): return {'CANCELLED'}
        settings = context.scene.sora_animation_queue
        at = settings.selected
        if self.operation == 'CLEAR':
            settings.items.clear(); settings.selected = -1; settings.owner = None
        elif 0 <= at < len(settings.items):
            if self.operation == 'REMOVE':
                settings.items.remove(at); settings.selected = min(at, len(settings.items)-1)
            else:
                to = at + (-1 if self.operation == 'UP' else 1)
                if 0 <= to < len(settings.items):
                    settings.items.move(at, to); settings.selected = to
        settings.status = f'队列共 {len(settings.items)} 条'
        return {'FINISHED'}


def apply_steps(context, rig, owner, entries, results, start, keep_face):
    from . import skill_animation, equipment_events
    from .action_binding import bind_action
    targets = [t for entry in entries for t in entry['targets']]
    rigs = skill_animation.affected_rigs(owner, rig, targets)
    for obj in rigs:
        if obj.get('sora_pose_resume'): raise ValueError('请先恢复挂起的姿势')
        if obj.animation_data and any(t.is_solo for t in obj.animation_data.nla_tracks):
            raise ValueError('请先退出 NLA 独奏模式')
        if obj.animation_data and any(not t.mute and not owned_track(t, obj) for t in obj.animation_data.nla_tracks):
            raise ValueError('请先静音当前骨架的自定义 NLA 轨道，避免与导入队列混合')
    saved = skill_animation.capture_state(context, owner, rigs)
    timing = manual.timeline_state(context)
    fps = timing[0]/timing[1]
    old_masks = [animation._face_mask(obj) for obj in rigs]
    track_states = [(obj, t, t.mute) for obj in rigs if obj.animation_data for t in obj.animation_data.nla_tracks]
    old_nla = [(obj, obj.animation_data.use_nla) for obj in rigs if obj.animation_data]
    created, tracks, masks = [], [], []
    sequence = uuid.uuid4().hex
    end = float(start)
    preview_objects = []
    try:
        for _, track, _ in track_states: track.mute = True
        from . import projectile_preview, projectile_mount
        projectile_preview.restore_visibility(owner)
        projectile_mount.restore(owner)
        # Preserve the resolved current state as the queue entry baseline. Restoring an
        # old manual baseline here can resurrect an alternate bow from a previous skill.
        equipment_events.pause(owner, restore_baseline=False)
        scheduled = []
        for index, entry in enumerate(entries):
            result = {key: results[prefix] for key, prefix in entry['keys'].items()}
            duration = float(result['body']['clip']['duration'])*fps
            if not math.isfinite(duration) or duration <= 0 or end+duration > 1048574:
                raise ValueError('动画队列时长超出时间线范围')
            yield {'stage': f'创建队列片段 {index+1}/{len(entries)}', 'detail': result['body']['clip']['name']}
            if entry.get('skill'):
                body, _, _ = yield from skill_animation.apply_steps(context, rig, owner, entry['targets'], result,
                    entry['skill'], keep_face, timeline={'fps': fps, 'origin': 1}, preview=False)
            else:
                body = yield from loader.apply_steps(context, rig, owner, entry['targets'], result, keep_face,
                                                     timeline={'fps': fps, 'origin': 1})
                if any(e.get('skill') for e in entries):
                    # A transition clip with no fully decoded SkillData still needs a
                    # deterministic equipment baseline, not the preceding skill's masks.
                    assembly = json.loads(owner[eq.CONTRACT])
                    slots = assembly['declaration']['dedicatedEquipment']
                    body['sora_projectile_preview'] = True
                    body['sora_projectile_scope'] = 'body-only native fight baseline; no resolved SkillData'
                    body['sora_projectile_hidden_slots'] = json.dumps([s['slotId'] for s in slots if not s['showWhenFight']])
                    body['sora_projectile_mount_weapons'] = json.dumps([s['weaponIndex'] for s in slots if s['showWhenFight']])
            pairs = [(rig, body)] + [(t['rig'], t['rig'].animation_data.action) for t in entry['targets']]
            for obj, action in pairs:
                created.append(action)
                action['sora_sequence'] = sequence
                masks.append(animation._face_mask(obj))
                scheduled.append((obj, action, end, duration, index))
            end += duration
        # All decoding and Action construction succeeds before NLA playback is committed.
        for mask in masks: animation._restore_face_mask(mask)
        for obj in dict.fromkeys(entry[0] for entry in scheduled):
            obj.animation_data_create()
            bind_action(obj, None)
            animation._associate_face(obj, None, False)
            obj.animation_data.use_nla = True
        by_rig = {}
        for obj, action, frame, duration, index in scheduled:
            track = by_rig.get(obj)
            if track is None:
                track = obj.animation_data.nla_tracks.new()
                tracks.append((obj, track)); by_rig[obj] = track
                track.name = 'ENDF 动画队列'
            # Blender's constructor takes an integer and rejects overlap; create after the fractional
            # boundary first, then restore the exact authored boundary through the float RNA properties.
            strip = track.strips.new(f'{index+1:02d} · {action.name}', math.ceil(frame), action)
            strip.action_slot = action.slots[0]
            strip.action_frame_start = 1
            strip.action_frame_end = 1+duration
            strip.frame_start = frame
            strip.frame_end = frame+duration
            strip.blend_type = 'REPLACE'
            strip.extrapolation = 'HOLD' if index == 0 else 'NOTHING'
            strip.use_auto_blend = False; strip.blend_in = 0; strip.blend_out = 0
            strip.influence = 1
            obj.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
        scene = context.scene
        scene.frame_start = start
        # NLA intervals are half-open. Do not leave a blank A-pose frame (or the previous longer
        # playback range) after the last strip when looping the newly imported sequence.
        scene.frame_end = max(start, math.ceil(end-1e-6)-1)
        owner[equipment_events.ENABLED] = saved['enabled'] or any(entry.get('skill') for entry in entries)
        if getattr(scene.sora_animation, 'projectile_preview', False):
            from . import projectile_preview
            for obj, action, frame, duration, index in scheduled:
                if obj == rig and entries[index].get('skill'):
                    preview_objects.extend(projectile_preview.build(context, owner, rig, action,
                        entries[index]['skill']['plan'], scene_origin=frame))
            projectile_preview.commit(owner, preview_objects)
        context.view_layer.update()
        scene.frame_set(start)
        equipment_events.defer_sync(scene)
        return end
    except BaseException:
        if preview_objects:
            from . import projectile_preview
            projectile_preview.remove_objects(preview_objects)
        for obj, track in reversed(tracks): obj.animation_data.nla_tracks.remove(track)
        skill_animation.restore_state(saved)
        for _, track, mute in track_states: track.mute = mute
        for obj, use_nla in old_nla: obj.animation_data.use_nla = use_nla
        for mask in old_masks: animation._restore_face_mask(mask)
        for action in reversed(created):
            if action.name in bpy.data.actions: bpy.data.actions.remove(action, do_unlink=True)
        context.scene.render.fps, context.scene.render.fps_base, context.scene.frame_start, context.scene.frame_end = timing[:4]
        context.scene.frame_set(timing[4], subframe=timing[5])
        raise


class SORA_OT_queue_load(TaskOperator, bpy.types.Operator):
    bl_idname = 'sora.animation_queue_load'
    bl_label = '按队列顺序加载到时间线'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .animation_panel import target
        try:
            settings = context.scene.sora_animation_queue
            rig = target(context)
            if not settings.items or rig is None or identity(context, rig) != (settings.owner, settings.root, settings.database, settings.asset):
                raise ValueError('请为当前角色添加动画队列')
            if context.screen.is_animation_playing: raise ValueError('请暂停播放后加载队列')
            initial = signature(settings)
            entries, jobs = [], []
            owner = eq.owner_collection(context)
            from . import skill_animation
            skill_index = skill_animation.clip_skill_index(context, rig)
            for index, row in enumerate(settings.items):
                selection = {'cab': row.cab, 'pathId': row.path_id}
                skills = skill_index.get((row.cab, row.path_id), [])
                if len(skills) > 1:
                    raise ValueError('队列片段对应多个原生技能，请单独选择技能加载：' + row.name)
                plan = None
                if skills:
                    current_owner, requests, targets, plan = skill_animation.requests(context, rig, skills[0],
                        selection=selection, refresh=False)
                else:
                    requests, current_owner, targets = loader.requests(context, rig, {
                        'root': settings.root, 'path': settings.database, 'asset': settings.asset,
                        'resource': row.resource, 'selection': {'cab': row.cab, 'pathId': row.path_id}})
                if current_owner != owner: raise ValueError('队列实例已改变')
                keys = {}
                for job in requests:
                    key = f'{index}:{job["key"]}'; keys[job['key']] = key
                    jobs.append(dict(job, key=key))
                entries.append({'targets': targets, 'keys': keys, 'skill': plan})
            def complete(results):
                if target(context) != rig or signature(settings) != initial or identity(context, rig) != initial[:4]:
                    raise ValueError('队列或角色来源已改变；请重新加载')
                end = yield from apply_steps(context, rig, owner, entries, results, settings.start,
                                              context.scene.sora_animation.keep_face_controls)
                settings.status = f'已加载 {len(entries)} 条：帧 {settings.start}–{end:g}；可在 NLA 编辑器独立编辑'
                context.scene.sora.status = settings.status
            return tasks.start_batch(self, context, jobs, complete, stage='加载动画队列')
        except Exception as error:
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}


def draw(layout, context):
    settings = context.scene.sora_animation_queue
    box = layout.box(); box.enabled = not tasks.busy()
    box.label(text='自选动画队列 · 按顺序拼接')
    box.operator('sora.animation_queue_add')
    box.template_list('UI_UL_list', 'animation_queue', settings, 'items', settings, 'selected', rows=4)
    row = box.row(align=True)
    for operation, text in [('UP', '上移'), ('DOWN', '下移'), ('REMOVE', '移除'), ('CLEAR', '清空')]:
        row.operator('sora.animation_queue_edit', text=text).operation = operation
    box.prop(settings, 'start')
    load = box.row(); load.enabled = bool(settings.items)
    load.operator('sora.animation_queue_load')
    from . import wrapped_label
    wrapped_label(box, settings.status, context)


CLASSES = (SORA_QueuedClip, SORA_AnimationQueue, SORA_OT_queue_add, SORA_OT_queue_edit, SORA_OT_queue_load)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.Scene.sora_animation_queue = PointerProperty(type=SORA_AnimationQueue)


def unregister():
    del bpy.types.Scene.sora_animation_queue
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
