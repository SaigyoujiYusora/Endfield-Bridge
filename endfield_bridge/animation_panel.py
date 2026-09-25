"""Native Animation Player UI through the existing Sora-Core RPC client."""
import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty, EnumProperty

from .client import CoreError, request
from .scene import apply_clip
from .animation_actions import apply_clip_steps
from . import tasks
from .tasks import TaskOperator


def target(context):
    obj = context.object
    if obj and obj.get('sora_display_source'):
        # A displayed aid carries only the instance token and the source rig
        # pointer; resolve through that stable pointer so selecting the
        # visible helper keeps the POSE/Animation entry working while the
        # source armature object is hidden.
        obj = obj['sora_display_source']
    if obj and obj.get('sora_owner_collection'):
        owner = obj['sora_owner_collection']
        obj = next((o for o in owner.objects if o.type == 'ARMATURE' and not o.get('sora_display_source')), None)
    rig = obj if obj and obj.type == 'ARMATURE' else obj.find_armature() if obj and obj.type == 'MESH' else None
    return rig if rig and rig.get('sora_instance') and rig.get('sora_asset') and rig.get('sora_database') else None


def call(context, method, **parameters):
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        raise CoreError('Endfield-Bridge preferences are unavailable')
    return request(bpy.path.abspath(addon.preferences.executable), method, **parameters)


def set_status(context, settings, message):
    settings.status = message
    context.scene.sora.status = message


class SORA_AnimationRow(bpy.types.PropertyGroup):
    identity: StringProperty()
    resource_path: StringProperty()
    classification: StringProperty()
    classification_source: StringProperty()


class SORA_AnimationClipRow(bpy.types.PropertyGroup):
    cab: StringProperty()
    path_id: StringProperty()


def invalidate_results(settings, context=None):
    settings.rows.clear();settings.clips.clear();settings.selected=-1;settings.selected_clip=-1
    settings.offset=0;settings.total=0;settings.result_owner=None;settings.result_root=''
    settings.clip_resource='';settings.clip_root=''


def select_skill(settings, context=None):
    if 0 <= settings.selected_skill < len(settings.skills):
        settings.skill = settings.skills[settings.selected_skill].resource_path


class SORA_AnimationSettings(bpy.types.PropertyGroup):
    game_root: StringProperty(name='Game Folder', subtype='DIR_PATH')
    query: StringProperty(name='Find animation',update=invalidate_results)
    skill: StringProperty(name='Skill resource')
    projectile_preview: BoolProperty(name='无目标弹体预览', default=True,
        description='使用原生发射时间、挂点、箭网格和速度；材质简化，不模拟战斗分支、碰撞和粒子拖尾')
    skill_query: StringProperty(name='技能名称筛选')
    skills: CollectionProperty(type=SORA_AnimationRow)
    selected_skill: IntProperty(default=-1, update=select_skill)
    category: EnumProperty(name='Category',items=[(v,v.title(),'') for v in ('all','idle','move','attack','skill','interaction','unclassified')],update=invalidate_results)
    offset: IntProperty(default=0,min=0)
    total: IntProperty(default=0)
    result_owner: PointerProperty(type=bpy.types.Object)
    result_root: StringProperty()
    rows: CollectionProperty(type=SORA_AnimationRow)
    selected: IntProperty(default=0)
    clips: CollectionProperty(type=SORA_AnimationClipRow)
    selected_clip: IntProperty(default=-1)
    clip_resource: StringProperty()
    clip_root: StringProperty()
    keep_face_controls: BoolProperty(name='Keep enabled manual Face controls', default=True,
        description='Retain all source keys in the new Action; explicitly mute only new curves overridden by owned Face drivers')
    status: StringProperty(default='Choose the game folder and search native animations')


class SORA_OT_animation_search(TaskOperator, bpy.types.Operator):
    bl_idname = 'sora.animation_search'
    bl_label = 'Search native animations'
    direction: IntProperty(default=0)

    def execute(self, context):
        settings = context.scene.sora_animation
        try:
            if not context.scene.sora.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            offset=max(0,settings.offset+self.direction*30) if self.direction else 0
            parameters = {'root': bpy.path.abspath(context.scene.sora.game_root), 'query': settings.query,'category':settings.category,'offset':offset,'limit':30}
            rig = target(context)
            if rig is not None:
                parameters.update(path=rig['sora_database'], asset=rig['sora_asset'])
            signature=(settings.query,settings.category,context.scene.sora.game_root)
            def complete(page):
                if signature!=(settings.query,settings.category,context.scene.sora.game_root) or target(context)!=rig:
                    raise CoreError('Animation owner or filter changed; search again')
                rows=page['rows']
                if not isinstance(rows, list):
                    raise CoreError('Animation search returned an invalid list')
                checked = [(str(row['id']), str(row['path']), str(row['label'])) for row in rows]
                settings.rows.clear()
                for (identity, path, label),source in zip(checked,rows):
                    row = settings.rows.add()
                    row.name, row.identity, row.resource_path = label, identity, path
                    classification=source.get('classification') or {}
                    row.classification=classification.get('category','unclassified')
                    row.classification_source=classification.get('confidence','unknown')+': '+classification.get('rule','')
                settings.offset=page['offset'];settings.total=page['total'];settings.result_owner=rig;settings.result_root=parameters['root']
                settings.selected = 0 if rows else -1
                settings.clips.clear()
                settings.selected_clip = -1
                settings.clip_resource = ''
                set_status(context, settings, f'{page["total"]} native animations; showing {offset+1 if rows else 0}-{offset+len(rows)}')
            return tasks.start(self, context, 'animation-search-page', parameters, complete)
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError) as error:
            set_status(context, settings, str(error))
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_animation_clips(TaskOperator, bpy.types.Operator):
    bl_idname = 'sora.animation_clips'
    bl_label = 'List clips in selected resource'

    def execute(self, context):
        settings = context.scene.sora_animation
        try:
            if settings.result_owner is not None and settings.result_owner!=target(context):
                raise ValueError('Search animations for the current instance first')
            if settings.result_root!=bpy.path.abspath(context.scene.sora.game_root) or settings.result_owner!=target(context):
                raise ValueError('Search animations for the current source and instance first')
            if not context.scene.sora.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            if not 0 <= settings.selected < len(settings.rows):
                raise ValueError('Select a native animation resource')
            resource = settings.rows[settings.selected].resource_path
            root = bpy.path.abspath(context.scene.sora.game_root)
            parameters = {'root': root, 'resource': resource}
            rig = target(context)
            if rig is not None:
                parameters.update(path=rig['sora_database'], asset=rig['sora_asset'])
            def complete(result):
                if target(context)!=rig or bpy.path.abspath(context.scene.sora.game_root)!=root:
                    raise ValueError('Animation source/instance changed; list clips again')
                if not isinstance(result, list) or not result:
                    raise CoreError('Animation resource has no clips')
                checked = []
                for row in result:
                    if row['resourcePath'] != resource or not isinstance(row['pathId'], str):
                        raise CoreError('Animation clip source identity mismatch')
                    checked.append((row['name'], row['cab'], row['pathId']))
                settings.clips.clear()
                for name, cab, path_id in checked:
                    row = settings.clips.add()
                    row.name, row.cab, row.path_id = name, cab, path_id
                settings.clip_resource, settings.clip_root = resource, root
                settings.selected_clip = 0 if len(checked) == 1 else -1
                set_status(context, settings, f'{len(checked)} clips found; select the clip to load')
            return tasks.start(self, context, 'animation-clips', parameters, complete)
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError) as error:
            set_status(context, settings, str(error))
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_animation_import(TaskOperator, bpy.types.Operator):
    bl_idname = 'sora.animation_import'
    bl_label = 'Load selected native animation'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'} and target(context) is not None

    def execute(self, context):
        settings = context.scene.sora_animation
        rig = target(context)
        try:
            if settings.result_root!=bpy.path.abspath(context.scene.sora.game_root) or settings.result_owner!=target(context):
                raise ValueError('Search animations for the current source and instance first')
            if not context.scene.sora.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            if not 0 <= settings.selected < len(settings.rows):
                raise ValueError('Select a native animation from the search results')
            row = settings.rows[settings.selected]
            if settings.clip_resource != row.resource_path or settings.clip_root != bpy.path.abspath(context.scene.sora.game_root):
                raise ValueError('List the clips in this resource before loading')
            if not 0 <= settings.selected_clip < len(settings.clips):
                raise ValueError('Select an exact clip from this resource')
            selected = settings.clips[settings.selected_clip]
            parameters = dict( root=bpy.path.abspath(context.scene.sora.game_root),
                          path=rig['sora_database'], asset=rig['sora_asset'], resource=row.resource_path,
                          selection={'cab': selected.cab, 'pathId': selected.path_id})
            from . import equipment_animation_load
            from . import skill_animation
            plan = None
            skills = skill_animation.clip_skill_index(context, rig).get((selected.cab, selected.path_id), [])
            if len(skills) == 1:
                owner, jobs, equipment_targets, plan = skill_animation.requests(context, rig, skills[0],
                    selection=parameters['selection'], refresh=False)
            else:
                jobs, owner, equipment_targets = equipment_animation_load.requests(context, rig, parameters)
            def complete(result):
                if target(context)!=rig:raise ValueError('Animation target changed before binding')
                if plan:
                    action, _, _ = yield from skill_animation.apply_steps(context, rig, owner, equipment_targets,
                        result, plan, settings.keep_face_controls)
                else:
                    action = yield from equipment_animation_load.apply_steps(context, rig, owner, equipment_targets,
                        result, settings.keep_face_controls)
                message = 'Loaded ' + result['body']['clip']['name']
                if plan: message += '; matched native skill with equipment and projectile timeline'
                elif len(skills) > 1: message += '; multiple skills reference this clip: load a specific skill for projectile playback'
                if equipment_targets: message += f'; {len(equipment_targets)} native equipment timelines loaded'
                if action.get('sora_face_mode') == 'MANUAL': message += '; manual Face controls override retained face keys'
                unbound=(result['body']['clip'].get('native') or {}).get('unboundTransformTracks') or []
                if unbound: message += f'; {len(unbound)} unbound native transform tracks preserved without a target bone'
                set_status(context, settings, message)
            return tasks.start_batch(self, context, jobs, complete, stage='Loading body and native equipment timelines')
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError, OverflowError) as error:
            set_status(context, settings, str(error))
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_animation_face(bpy.types.Operator):
    bl_idname = 'sora.animation_face'
    bl_label = 'Select animation Face mode'
    bl_options = {'REGISTER', 'UNDO'}
    manual: BoolProperty(default=False)

    def execute(self, context):
        from .animation_actions import set_manual_face
        rig = target(context)
        if rig is None:
            return {'CANCELLED'}
        try:
            set_manual_face(context, rig, self.manual)
            return {'FINISHED'}
        except (ValueError, KeyError, TypeError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_skill_search(TaskOperator, bpy.types.Operator):
    bl_idname = 'sora.skill_search'
    bl_label = '读取当前角色技能列表'

    def execute(self, context):
        settings = context.scene.sora_animation
        rig = target(context)
        try:
            if rig is None: raise ValueError('请选择已导入的角色')
            root = bpy.path.abspath(context.scene.sora.game_root)
            query = settings.skill_query
            def complete(rows):
                if target(context) != rig or root != bpy.path.abspath(context.scene.sora.game_root) or query != settings.skill_query:
                    raise ValueError('角色或筛选已改变，请重新读取技能')
                settings.skills.clear()
                for source in rows:
                    row = settings.skills.add()
                    row.name, row.resource_path = source['name'], source['resource']
                settings.selected_skill = 0 if rows else -1
                set_status(context, settings, f'{len(rows)} 个游戏内技能资源；选择后加载，无需另找 JSON 文件')
            return tasks.start(self, context, 'skill-search',
                dict(root=root, path=rig['sora_database'], asset=rig['sora_asset'], query=query), complete)
        except Exception as error:
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}


class SORA_OT_animation_skill(TaskOperator, bpy.types.Operator):
    bl_idname = 'sora.animation_skill'
    bl_label = 'Load skill animation (body + native slots)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'} and target(context) is not None

    def execute(self, context):
        from . import skill_animation
        settings = context.scene.sora_animation
        rig = target(context)
        try:
            if settings.result_owner is not None and settings.result_owner != rig:
                raise ValueError('Search animations for the current instance first')
            if not context.scene.sora.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            skill = settings.skill.strip()
            if not skill:
                raise ValueError('输入技能资源名，例如 Json/SkillData/chr_0032_lizhiyan_attack1.json')
            owner, jobs, targets, plan = skill_animation.requests(context, rig, skill)
            body = plan['body']
            def complete(result):
                if target(context) != rig:
                    raise ValueError('Animation target changed before binding')
                action, applied, released = yield from skill_animation.apply_steps(context, rig, owner, targets,
                    result, plan, settings.keep_face_controls)
                message = 'Loaded skill ' + result['body']['clip']['name']
                if action.get('sora_projectile_count'):
                    message += f"; {action['sora_projectile_count']} 支无目标弹体预览（复用箭矢材质）"
                if targets: message += f'; {len(targets)} native skill windows loaded'
                elif plan.get('equipmentScope') == 'body-only-no-native-equipment-animator':
                    message += '; body animation only; native equipment Animator is not declared'
                if released: message += f'; {len(released)} previous skill playback released'
                if applied:
                    hidden = [row['slotId'] for row in applied if not row['visible']]
                    message += f'; authored visibility applied to {len(applied)} slots' + (f' ({len(hidden)} hidden)' if hidden else '')
                set_status(context, settings, message)
            return tasks.start_batch(self, context, jobs, complete, stage='Loading skill body and native windows')
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError, OverflowError) as error:
            set_status(context, settings, str(error))
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def draw(layout, context):
    settings = context.scene.sora_animation
    layout.label(text='Uses the shared Game Folder')
    layout.prop(settings, 'query')
    layout.prop(settings, 'category')
    layout.operator('sora.animation_search')
    layout.template_list('UI_UL_list', 'native_animations', settings, 'rows', settings, 'selected', rows=5)
    row=layout.row(align=True)
    previous=row.row();previous.enabled=settings.offset>0
    previous.operator('sora.animation_search',text='',icon='TRIA_LEFT').direction=-1
    row.label(text=f'{settings.offset+1 if settings.rows else 0}-{settings.offset+len(settings.rows)} / {settings.total}')
    following=row.row();following.enabled=settings.offset+len(settings.rows)<settings.total
    following.operator('sora.animation_search',text='',icon='TRIA_RIGHT').direction=1
    if 0<=settings.selected<len(settings.rows):
        layout.label(text=settings.rows[settings.selected].classification_source)
    layout.operator('sora.animation_clips')
    if (0 <= settings.selected < len(settings.rows)
            and settings.clip_resource == settings.rows[settings.selected].resource_path
            and settings.clip_root == bpy.path.abspath(context.scene.sora.game_root)):
        layout.template_list('UI_UL_list', 'native_animation_clips', settings, 'clips', settings, 'selected_clip', rows=3)
    layout.prop(settings, 'keep_face_controls')
    layout.operator('sora.animation_import')
    from . import animation_queue
    animation_queue.draw(layout, context)
    column = layout.column(align=True)
    column.label(text='技能动作 · 身体与装备')
    column.prop(settings, 'skill_query')
    column.operator('sora.skill_search')
    column.template_list('UI_UL_list', 'native_skills', settings, 'skills', settings, 'selected_skill', rows=4)
    column.prop(settings, 'skill')
    column.prop(settings, 'projectile_preview')
    column.operator('sora.animation_skill')
    rig = target(context)
    action = rig.animation_data.action if rig and rig.animation_data else None
    from . import face_controls as face
    if rig is not None and face.DATA in rig:
        manual = bool(rig.get(face.ENABLED))
        has_face_curves = action is not None and action.get('sora_face_curves') not in (None, '[]')
        layout.label(text=('Face: manual controls' if manual else
                           'Face: imported animation' if has_face_curves else 'Face: controls disabled'))
        disable_text = 'Use animation face' if has_face_curves else 'Disable manual Face controls'
        layout.operator('sora.animation_face', text=disable_text if manual else 'Use manual Face controls').manual = not manual
    layout.label(text=settings.status)


CLASSES = (SORA_AnimationRow, SORA_AnimationClipRow, SORA_AnimationSettings, SORA_OT_animation_search,
           SORA_OT_animation_clips, SORA_OT_animation_import, SORA_OT_animation_face, SORA_OT_skill_search, SORA_OT_animation_skill)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sora_animation = PointerProperty(type=SORA_AnimationSettings)
    from . import animation_queue
    animation_queue.register()
    from . import projectile_preview
    projectile_preview.register()


def unregister():
    from . import projectile_preview
    projectile_preview.unregister()
    from . import animation_queue
    animation_queue.unregister()
    del bpy.types.Scene.sora_animation
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
