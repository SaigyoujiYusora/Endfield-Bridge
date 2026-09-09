bl_info = {
    "name": "Endfield-Bridge", "author": "OMGKawaiiYusora", "version": (0, 2, 0),
    "blender": (5, 1, 0), "location": "View3D > Sidebar > ENDF2Blend",
    "description": "Import Sora-Core scenes, drive faces and play animations", "category": "Import-Export",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty, EnumProperty

from .client import CoreError, request
from .scene import apply_clip, create_scene, create_scene_steps, remove_scene
from . import tasks
from .tasks import TaskOperator


class SORA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    executable: StringProperty(name="Sora-Core executable", subtype="FILE_PATH")

    def draw(self, context):
        self.layout.prop(self, "executable")


class SORA_OT_remove(bpy.types.Operator):
    bl_idname = "sora.remove_import"
    bl_label = "Remove selected import"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        matches = [c for c in obj.users_collection if c.get("sora_instance") == obj.get("sora_instance")] if obj and obj.get("sora_instance") else []
        if len(matches) != 1:
            self.report({"ERROR"}, "Select an object in one imported collection")
            return {"CANCELLED"}
        try:
            remove_scene(context, matches[0])
            return {"FINISHED"}
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SORA_AssetRow(bpy.types.PropertyGroup):
    identity: StringProperty()
    detail: StringProperty()
    has_scene: BoolProperty(default=False)
    can_import: BoolProperty(default=False)
    reason: StringProperty()
    kind: StringProperty()


def update_npr_post(self, context):
    from . import post
    scene = self.id_data
    try:
        if self.npr_post_processing:
            post.enable(scene, context)
        else:
            post.disable(scene)
    except (ValueError, RuntimeError, TypeError, KeyError) as error:
        self['npr_post_processing'] = post.installed(scene)
        self.status = 'ENDF NPR-Shader post: ' + str(error)


def invalidate(self, context):
    self.assets.clear()
    self.selected = -1
    self.result_database = ''
    self.offset = 0


class SORA_Settings(bpy.types.PropertyGroup):
    face_filter: StringProperty(name="Face controls and presets")
    face_index: IntProperty(name="Control", default=0, min=0)
    face_modified: BoolProperty(name="Modified only", default=False)
    face_preset_index: IntProperty(name="Preset", default=0, min=0)
    material_mode: EnumProperty(name="Import materials", items=[("RURI", "ENDF NPR-Shader", "ENDF NPR-Shader shader and vertex stages"), ("BASIC", "Basic PBR", "Principled material fallback")], default="RURI")
    npr_post_processing: BoolProperty(
        name="ENDF NPR-Shader post-processing", default=True, update=update_npr_post,
        description="Apply game tone mapping to the entire scene after its existing compositor, including viewport and final render; uses Standard color management and neutral exposure/gamma, and restores previous settings when disabled")

    game_root: StringProperty(name="Game Folder", subtype="DIR_PATH", update=invalidate)
    database: StringProperty(name="Sora Endfield Database", subtype="FILE_PATH", update=invalidate)
    source_details: BoolProperty(name="Data source settings", default=True)
    category: EnumProperty(name="Library", items=[('PEOPLE','人物',''),('ITEMS','物品',''),('SCENES','场景','')], update=invalidate)
    kind: EnumProperty(name="Type", items=[('character','角色',''),('npc','NPC','')], update=invalidate)
    function_page: EnumProperty(name="Instance", items=[('POSE','姿势',''),('ANIMATION','动画',''),('FACE','表情',''),('EQUIPMENT','装备',''),('MATERIAL','材质','')])
    offset: IntProperty(default=0, min=0)
    total: IntProperty(default=0)
    task_running: BoolProperty(default=False)
    task_stage: StringProperty()
    task_tick: IntProperty(default=0)
    task_detail: StringProperty()
    task_completed: IntProperty(default=0)
    task_total: IntProperty(default=0)
    task_error: StringProperty()
    diagnostics: BoolProperty(name="Error details", default=False)
    query: StringProperty(name="Search", update=invalidate)
    result_database: StringProperty()
    assets: CollectionProperty(type=SORA_AssetRow)
    selected: IntProperty(default=0)
    clip: StringProperty(name="Animation", default="")
    status: StringProperty(default="Select Sora-Core in Add-on Preferences")


def call(context, method, **parameters):
    preferences = context.preferences.addons[__package__].preferences
    return request(bpy.path.abspath(preferences.executable), method, **parameters)


class SORA_OT_check(bpy.types.Operator):
    bl_idname = "sora.check"
    bl_label = "Check Sora-Core"

    def execute(self, context):
        try:
            result = call(context, "capabilities")
            if result.get("product") != "Sora-Core":
                raise CoreError("The selected executable is not Sora-Core")
            context.scene.sora.status = "Sora-Core " + result["version"] + " connected"
            return {"FINISHED"}
        except (CoreError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SORA_OT_cancel(bpy.types.Operator):
    bl_idname = "sora.cancel_task"
    bl_label = "Cancel task"
    def execute(self, context):
        tasks.cancel()
        return {'FINISHED'}


class SORA_OT_database(TaskOperator, bpy.types.Operator):
    bl_idname = "sora.database_task"
    bl_label = "Database"
    operation: StringProperty(default='game-validate')
    def execute(self, context):
        settings = context.scene.sora
        try:
            parameters = {'root': bpy.path.abspath(settings.game_root), 'path': bpy.path.abspath(settings.database)}
            def complete(result):
                settings.status = str(result.get('message') or 'Data source ready')
                settings.source_details = False
                invalidate(settings, context)
            return tasks.start(self, context, self.operation, parameters, complete)
        except (CoreError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_search(TaskOperator, bpy.types.Operator):
    bl_idname = "sora.search"
    bl_label = "Load / Search"
    direction: IntProperty(default=0)
    def execute(self, context):
        settings = context.scene.sora
        database = bpy.path.abspath(settings.database)
        root = bpy.path.abspath(settings.game_root) if settings.game_root else ''
        signature = (settings.database, settings.game_root, settings.query, settings.category, settings.kind)
        offset = max(0, settings.offset + self.direction * 30) if self.direction else 0
        try:
            def complete(result):
                if signature != (settings.database, settings.game_root, settings.query, settings.category, settings.kind):
                    raise CoreError('Data source or filter changed; search again')
                settings.assets.clear()
                for source in result['rows']:
                    row = settings.assets.add()
                    row.name = source['label']
                    row.identity = source['id']
                    row.detail = source.get('detail', '')
                    row.has_scene = source.get('hasScene', False)
                    capability = source.get('capability') or {}
                    row.can_import = capability.get('canImport', row.has_scene)
                    row.reason = capability.get('reason', 'Cached scene' if row.has_scene else 'No supported scene parser')
                    row.kind = source.get('kind', '')
                settings.result_database = database
                settings.selected = 0 if settings.assets else -1
                settings.offset = offset
                settings.total = result['total']
                settings.status = f"{result['total']} matches"
            return tasks.start(self, context, 'search', {'path':database, 'root':root,
                'query':settings.query, 'kind':settings.kind if settings.category == 'PEOPLE' else 'weapon',
                'offset':offset, 'limit':30}, complete)
        except (CoreError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def import_reason(context):
    settings = context.scene.sora
    if tasks.busy(): return 'Wait for or cancel the current task'
    if context.mode != 'OBJECT': return 'Switch to Object Mode'
    if settings.result_database != bpy.path.abspath(settings.database): return 'Load / search the selected database'
    if not 0 <= settings.selected < len(settings.assets): return 'Select an asset'
    row = settings.assets[settings.selected]
    return '' if row.can_import else row.reason


class SORA_OT_import(TaskOperator, bpy.types.Operator):
    bl_idname = "sora.import_asset"
    bl_label = "Import Selected Asset"
    bl_options = {"REGISTER", "UNDO"}
    @classmethod
    def poll(cls, context):
        return getattr(context.scene, 'sora', None) is not None and not import_reason(context)
    def execute(self, context):
        settings = context.scene.sora
        identity = settings.assets[settings.selected].identity
        database = bpy.path.abspath(settings.database)
        mode = settings.material_mode
        def complete(document):
            collection, rig = yield from create_scene_steps(context, document, mode)
            for obj in collection.objects:
                obj['sora_asset'] = identity
                obj['sora_database'] = database
            settings.status = 'Imported ' + document['name']
            settings.clip = document['clips'][0]['name'] if document['clips'] else ''
        try:
            return tasks.start(self, context, 'scene', {'path':database,'asset':identity,
                'root':bpy.path.abspath(settings.game_root) if settings.game_root else ''}, complete)
        except (CoreError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_instance(bpy.types.Operator):
    bl_idname = 'sora.select_instance'
    bl_label = 'Next imported instance'
    def execute(self, context):
        roots = [c for c in bpy.data.collections if c.get('sora_instance') and c.objects]
        if not roots:
            return {'CANCELLED'}
        current = context.object.get('sora_instance') if context.object else None
        index = next((i for i, c in enumerate(roots) if c.get('sora_instance') == current), -1)
        collection = roots[(index + 1) % len(roots)]
        obj = next((o for o in collection.objects if o.type == 'ARMATURE'), collection.objects[0])
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class SORA_OT_stick(bpy.types.Operator):
    bl_idname = 'sora.stick_display'
    bl_label = 'Apply STICK to current instance'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        from .animation_panel import target
        rig = target(context)
        if rig is None:
            self.report({'ERROR'}, 'Select an imported armature or mesh')
            return {'CANCELLED'}
        rig.data.display_type = 'STICK'
        return {'FINISHED'}


class SORA_OT_clip(TaskOperator, bpy.types.Operator):
    bl_idname = "sora.load_clip"
    bl_label = "Load Animation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "POSE"} and context.object is not None and context.object.type == "ARMATURE" and bool(context.object.get("sora_asset"))

    def execute(self, context):
        rig = context.object
        try:
            from .animation_actions import apply_clip_steps
            def complete(document):
                clip = next((clip for clip in document["clips"] if clip["name"] == context.scene.sora.clip), None)
                if clip is None:
                    raise ValueError("Animation name is not present in this asset")
                yield from apply_clip_steps(context, rig, clip, [bone["name"] for bone in document["bones"]],
                           bone_sources=document['bones'] if all(b.get('sourcePath') is not None for b in document['bones']) else None,
                           keep_face_controls=context.scene.sora_animation.keep_face_controls)
            return tasks.start(self, context, 'scene', {'path':rig['sora_database'],
                'asset':rig['sora_asset'], 'root':bpy.path.abspath(context.scene.sora.game_root)}, complete)
        except (CoreError, ValueError, KeyError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SORA_PT_panel(bpy.types.Panel):
    bl_label = "ENDF2Blend"
    bl_idname = "SORA_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ENDF2Blend"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sora
        if settings.task_running:
            box = layout.box()
            if settings.task_total:
                factor = settings.task_completed / settings.task_total
                box.progress(factor=min(1.0, factor), type='BAR', text=settings.task_stage)
                box.label(text=f'{settings.task_completed} / {settings.task_total}')
            else:
                box.progress(factor=(settings.task_tick % 20) / 20.0, type='RING', text='Activity - total unknown')
                box.label(text=settings.task_stage)
            if settings.task_detail:
                box.label(text=settings.task_detail[:48])
            box.operator('sora.cancel_task')
        box = layout.box()
        box.enabled = not settings.task_running
        box.prop(settings, 'source_details', icon='DISCLOSURE_TRI_DOWN' if settings.source_details else 'DISCLOSURE_TRI_RIGHT')
        if settings.source_details:
            preferences = context.preferences.addons.get(__package__)
            if preferences: box.prop(preferences.preferences, 'executable')
            box.operator('sora.check')
            box.prop(settings, 'game_root')
            box.prop(settings, 'database')
            row = box.row(align=True)
            row.operator('sora.database_task', text='Validate').operation = 'game-validate'
            row.operator('sora.database_task', text='Build / Update').operation = 'database-build'
        box = layout.box()
        box.enabled = not settings.task_running
        box.label(text='资源库')
        box.prop(settings, 'category', expand=True)
        if settings.category == 'SCENES':
            box.label(text='区域 / 过场：占位，尚未支持', icon='INFO')
        else:
            if settings.category == 'PEOPLE': box.prop(settings, 'kind', expand=True)
            else: box.label(text='通用武器（按后端支持范围）')
            box.prop(settings, 'query')
            box.operator('sora.search')
            box.template_list('UI_UL_list', 'sora_assets', settings, 'assets', settings, 'selected', rows=4)
            row = box.row(align=True)
            previous = row.row(); previous.enabled = settings.offset > 0
            previous.operator('sora.search', text='', icon='TRIA_LEFT').direction = -1
            row.label(text=f'{settings.offset + 1 if settings.total else 0}–{settings.offset + len(settings.assets)} / {settings.total}')
            following = row.row(); following.enabled = settings.offset + len(settings.assets) < settings.total
            following.operator('sora.search', text='', icon='TRIA_RIGHT').direction = 1
            if 0 <= settings.selected < len(settings.assets):
                asset = settings.assets[settings.selected]
                box.label(text=asset.detail)
                box.label(text=asset.reason)
            box.operator('sora.import_asset')
            reason = import_reason(context)
            if reason: box.label(text=reason, icon='INFO')
        box = layout.box()
        box.label(text='当前实例: ' + (context.object.name if context.object and context.object.get('sora_instance') else '未选择'))
        buttons = box.column(align=True)
        buttons.enabled = not settings.task_running
        buttons.operator('sora.select_instance')
        buttons.operator('sora.remove_import')
        box.prop(settings, 'function_page', expand=True)
        box = box.column()
        box.enabled = not settings.task_running
        if settings.function_page == 'ANIMATION':
            from . import animation_panel
            animation_panel.draw(box, context)
            box.operator('screen.animation_play', text='Play / Pause', icon='PLAY')
        elif settings.function_page == 'FACE':
            from . import face_controls
            face_controls.draw(box, context)
        elif settings.function_page == 'POSE':
            box.operator('sora.stick_display')
            box.label(text='Pose tools require verified native mapping', icon='INFO')
        elif settings.function_page == 'MATERIAL':
            box.prop(settings, 'material_mode', text='New imports')
            box.prop(settings, 'npr_post_processing')
            box.label(text='Post-processing affects the entire scene', icon='INFO')
        else:
            box.label(text='专用装备归属于角色；通用武器独立管理')
            box.label(text='Equipment binding support pending', icon='INFO')
        layout.label(text=settings.status)
        if settings.task_error:
            layout.prop(settings, 'diagnostics')
            if settings.diagnostics:
                layout.label(text=settings.task_error)


CLASSES = (SORA_Preferences, SORA_AssetRow, SORA_Settings, SORA_OT_check, SORA_OT_cancel, SORA_OT_database, SORA_OT_instance, SORA_OT_stick, SORA_OT_search, SORA_OT_import, SORA_OT_remove, SORA_OT_clip, SORA_PT_panel)


@persistent
def migrate_saved_sources(_=None):
    from pathlib import Path
    from .migration import migrate_scene
    for scene in bpy.data.scenes:
        migrate_scene(scene, bpy.path.abspath, lambda value: Path(value).is_dir())


def register():
    from . import post, ruri_adapter, material_panel
    ruri_adapter.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sora = bpy.props.PointerProperty(type=SORA_Settings)
    post.register()
    material_panel.register()
    from . import face_controls
    face_controls.register()
    from . import animation_panel
    animation_panel.register()
    if migrate_saved_sources not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate_saved_sources)
    migrate_saved_sources()


def unregister():
    if migrate_saved_sources in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_saved_sources)
    tasks.shutdown()
    from . import animation_panel
    animation_panel.unregister()
    from . import face_controls
    face_controls.unregister()
    from . import post, ruri_adapter, material_panel
    material_panel.unregister()
    post.unregister()
    ruri_adapter.unregister()
    del bpy.types.Scene.sora
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
