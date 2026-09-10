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
from . import library_ui
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
        from .equipment import owner_collection
        collection = owner_collection(context)
        if collection is None:
            self.report({'ERROR'}, 'Select an object in an imported instance')
            return {'CANCELLED'}
        try:
            remove_scene(context, collection)
            return {"FINISHED"}
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SORA_AssetRow(bpy.types.PropertyGroup):
    identity: StringProperty()
    detail: StringProperty()
    has_scene: BoolProperty(default=False)
    can_import: BoolProperty(default=False)
    can_attempt_import: BoolProperty(default=False)
    contract_version: IntProperty(default=0)
    reason: StringProperty()
    kind: StringProperty()
    internal_name: StringProperty()
    display_zh: StringProperty()
    display_en: StringProperty()
    localization_status: StringProperty()
    resource_path: StringProperty()
    database_mode: StringProperty()


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
    self.total = 0


def invalidate_source(self, context):
    library_ui.source_changed(self)
    self.payload_bytes = -1
    self.max_payload_bytes = 0
    self.database_format_version = 0
    self.budget_database = ''


def database_budget(settings, result):
    values = tuple(result.get(key) for key in ('formatVersion', 'payloadBytes', 'maxPayloadBytes'))
    version, payload, maximum = values
    valid = (all(type(value) is int and 0 <= value <= 2_147_483_647 for value in values)
             and version > 0 and maximum > 0 and payload <= maximum)
    settings.database_format_version = version if valid else 0
    settings.payload_bytes = payload if valid else -1
    settings.max_payload_bytes = maximum if valid else 0
    settings.budget_database = settings.database if valid else ''


def database_budget_lines(settings):
    if settings.payload_bytes < 0 or settings.budget_database != settings.database:
        return ('库容量：尚未获取',)
    used = settings.payload_bytes / 1048576
    maximum = settings.max_payload_bytes / 1048576
    remaining = (settings.max_payload_bytes - settings.payload_bytes) / 1048576
    return (f'JSON {used:.1f} / {maximum:.1f} MiB',
            f'余量 {remaining:.1f} MiB · v{settings.database_format_version}')


class SORA_Settings(bpy.types.PropertyGroup):
    face_filter: StringProperty(name="Face controls and presets")
    face_index: IntProperty(name="Control", default=0, min=0)
    face_modified: BoolProperty(name="Modified only", default=False)
    face_preset_index: IntProperty(name="Preset", default=0, min=0)
    material_mode: EnumProperty(name="Import materials", items=[("RURI", "ENDF NPR-Shader", "ENDF NPR-Shader shader and vertex stages"), ("BASIC", "Basic PBR", "Principled material fallback")], default="RURI")
    npr_post_processing: BoolProperty(
        name="ENDF NPR-Shader post-processing", default=True, update=update_npr_post,
        description="Apply game tone mapping to the entire scene after its existing compositor, including viewport and final render; uses Standard color management and neutral exposure/gamma, and restores previous settings when disabled")

    game_root: StringProperty(name="Game Folder", subtype="DIR_PATH", update=invalidate_source)
    database: StringProperty(name="Sora Endfield Database", subtype="FILE_PATH", update=invalidate_source)
    payload_bytes: IntProperty(default=-1, min=-1, description="Validated JSON payload bytes, excluding the 52-byte file header")
    max_payload_bytes: IntProperty(default=0, min=0)
    database_format_version: IntProperty(default=0, min=0)
    budget_database: StringProperty()
    source_details: BoolProperty(name="Data source settings", default=True)
    category: EnumProperty(name="Library", items=[('PEOPLE','人物',''),('ITEMS','物品',''),('SCENES','场景','')], update=library_ui.switch_page)
    kind: EnumProperty(name="Type", items=[('character','角色',''),('npc','NPC','')], update=library_ui.switch_page)
    library_pages: StringProperty(default='{}', options={'SKIP_SAVE'})
    library_page: StringProperty(default='character', options={'SKIP_SAVE'})
    library_switching: BoolProperty(default=False, options={'SKIP_SAVE'})
    asset_details: BoolProperty(name='资源详情', default=False, update=library_ui.details_changed)
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
    query: StringProperty(name="Search", update=library_ui.query_changed)
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


def database_parameters(settings, operation, abspath):
    if not settings.game_root.strip():
        raise CoreError('Choose the game folder before validating or building a database')
    if operation not in {'game-validate', 'database-build'}:
        raise CoreError('Unknown database operation')
    parameters = {'root': abspath(settings.game_root)}
    if settings.database.strip():
        parameters['path'] = abspath(settings.database)
    elif operation == 'database-build':
        raise CoreError('Choose a database output file before building')
    return parameters


def database_complete(settings, context, operation, has_database, result):
    invalidate(settings, context)
    database_budget(settings, result if has_database else {})
    settings.source_details = True
    version = str(result.get('gameVersion') or result.get('version') or 'version unreported')
    if operation == 'game-validate':
        compatibility = result.get('databaseCompatibility')
        if has_database and compatibility is not None and (
                not isinstance(compatibility, dict)
                or type(compatibility.get('contractVersion')) is not int
                or compatibility['contractVersion'] != 1
                or compatibility.get('mode') not in {'standalone-scene', 'indexed-game', 'legacy-snapshot'}):
            raise CoreError('Unknown database compatibility contract; update Sora-Core and validate again')
        if has_database and isinstance(compatibility, dict):
            count, total = compatibility.get('cachedSceneCount'), compatibility.get('assetCount')
            if (type(count) is not int or type(total) is not int or not 0 <= count <= total
                    or not all(type(compatibility.get(key)) is bool for key in (
                        'allAssetsHaveCachedScenes', 'canImportCachedScenes', 'canResolveIndexedAssets', 'versionMatches'))
                    or compatibility['allAssetsHaveCachedScenes'] != (total > 0 and count == total)
                    or not isinstance(compatibility.get('databaseVersion'), str)):
                raise CoreError('Incomplete database compatibility status; update Sora-Core and validate again')
            indexed = compatibility['mode'] == 'indexed-game'
            matched_index = indexed and result.get('matches') is True
            if (compatibility['canResolveIndexedAssets'] != matched_index
                    or compatibility['canImportCachedScenes'] != (count > 0 and (not indexed or matched_index))
                    or (not indexed and compatibility['mode'] != ('standalone-scene' if count > 0 else 'legacy-snapshot'))):
                raise CoreError('Inconsistent database compatibility status; update Sora-Core and validate again')
        if has_database and isinstance(compatibility, dict) and compatibility.get('mode') == 'standalone-scene':
            count = compatibility.get('cachedSceneCount')
            total = compatibility.get('assetCount')
            complete = compatibility.get('allAssetsHaveCachedScenes')
            if (type(compatibility.get('contractVersion')) is not int or compatibility['contractVersion'] != 1
                    or type(count) is not int or type(total) is not int or not 0 < count <= total
                    or type(complete) is not bool or complete != (count == total)
                    or compatibility.get('canImportCachedScenes') is not True
                    or compatibility.get('canResolveIndexedAssets') is not False
                    or type(compatibility.get('versionMatches')) is not bool
                    or not isinstance(compatibility.get('databaseVersion'), str)):
                raise CoreError('Incomplete standalone compatibility status; update Sora-Core and validate again')
            comparison = 'version labels equal; resource match unverified' if compatibility['versionMatches'] else 'game version differs'
            settings.status = (f'Standalone Scene compatibility: {count}/{total} cached scenes; '
                f'database {compatibility["databaseVersion"]}, game {version}: {comparison}. '
                'Cached scenes import independently; native resource associations unverified. No indexed on-demand parsing or full-game coverage. '
                + ('All asset records have cached scenes.' if complete else 'Uncached records cannot be imported; build a matching unified index.'))
            return
        if has_database and isinstance(compatibility, dict) and compatibility.get('mode') == 'legacy-snapshot':
            raise CoreError('Legacy snapshot has no cached scenes or verified unified index; build a matching unified database')
        if result.get('matches') is False:
            settings.status = 'Game/database version mismatch; update or rebuild the database before importing'
            raise CoreError(settings.status)
        if has_database and result.get('matches') is not True:
            raise CoreError('Database match status was not returned; validate again before importing')
        settings.status = ('Game/database matched: ' if has_database else 'Game folder validated: ') + version
        if result.get('manifestRevision') is not None:
            settings.status += '; manifest revision ' + str(result['manifestRevision'])
        settings.source_details = not has_database
    else:
        count = result.get('assets', result.get('assetCount'))
        if type(count) is not int or count < 0:
            raise CoreError('Database build returned no valid indexed asset count')
        settings.status = f'Database built: {version}; {count} indexed assets. Load / search to browse'
        settings.source_details = False


class SORA_OT_database(TaskOperator, bpy.types.Operator):
    bl_idname = "sora.database_task"
    bl_label = "Database"
    operation: StringProperty(default='game-validate')
    def execute(self, context):
        settings = context.scene.sora
        try:
            parameters = database_parameters(settings, self.operation, bpy.path.abspath)
            operation = self.operation
            def complete(result):
                database_complete(settings, context, operation, 'path' in parameters, result)
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
        library_ui.remember(settings)
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
                    row.contract_version = 1
                    row.name = source['label']
                    metadata = source.get('metadata') or {}
                    locator = source.get('locator') or {}
                    row.database_mode = source.get('databaseMode') or ('indexed-game' if locator else 'standalone-scene')
                    row.resource_path = locator.get('path') or ''
                    row.internal_name = metadata.get('internalName') or (row.resource_path.rsplit('/', 1)[-1].rsplit('.', 1)[0] if row.resource_path else source['label'])
                    row.display_zh = metadata.get('displayNameZh') or ''
                    row.display_en = metadata.get('displayNameEn') or ''
                    row.localization_status = metadata.get('localizationStatus') or 'missing-translation'
                    row.identity = source['id']
                    row.detail = source.get('detail', '')
                    row.has_scene = source.get('hasScene', False)
                    capability = source.get('capability') or {}
                    row.can_import = capability.get('canImport', row.has_scene)
                    row.can_attempt_import = capability.get('canAttemptImport', row.can_import)
                    row.reason = capability.get('reason', 'Cached scene' if row.has_scene else 'No supported scene parser')
                    row.kind = source.get('kind', '')
                settings.result_database = database
                library_ui.restore_selection(settings)
                settings.offset = offset
                settings.total = result['total']
                settings.source_details = False
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
    if row.contract_version != 1: return 'Reload search to refresh import capabilities'
    if row.database_mode not in {'indexed-game','standalone-scene'}: return 'Reload search to identify database coverage'
    return '' if row.can_attempt_import else row.reason


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
        row = settings.assets[settings.selected]
        asset_kind = row.kind
        source_path = row.resource_path or (identity if identity.startswith('assets/') else '')
        formal_character = asset_kind == 'character' and row.database_mode == 'indexed-game'
        prior_active = context.view_layer.objects.active
        prior_selected = list(context.selected_objects)
        def complete(result):
            document = result['scene'] if formal_character else result
            collection = None
            try:
                collection, rig = yield from create_scene_steps(context, document, mode)
                for obj in collection.objects:
                    obj['sora_asset'] = identity
                    obj['sora_database'] = database
                    obj['sora_resource_path'] = source_path
                if formal_character:
                    from .equipment import create_dedicated_steps
                    yield from create_dedicated_steps(context, collection, rig, result['equipment'], mode)
                elif asset_kind == 'character':
                    collection['sora_equipment_status'] = 'Standalone Scene: dedicated equipment not associated; load it explicitly with a matching game folder'
                imported = rig or next((obj for obj in collection.objects if obj.type == 'MESH'), None)
                if imported is not None:
                    for selected in list(context.selected_objects): selected.select_set(False)
                    imported.select_set(True)
                    context.view_layer.objects.active = imported
                settings.status = 'Imported ' + document['name'] + ('; static dedicated equipment ready, events/damping not evaluated' if formal_character else '')
                settings.clip = document['clips'][0]['name'] if document['clips'] else ''
            except BaseException:
                if collection is not None:
                    remove_scene(context, collection, force_cleanup=True)
                for selected in list(context.selected_objects): selected.select_set(False)
                for selected in prior_selected:
                    if selected.name in context.view_layer.objects: selected.select_set(True)
                if prior_active and prior_active.name in context.view_layer.objects:
                    context.view_layer.objects.active = prior_active
                raise
        try:
            parameters = {'path': database, 'asset': identity,
                'root': bpy.path.abspath(settings.game_root) if settings.game_root else ''}
            if formal_character: parameters['includeOwner'] = True
            return tasks.start(self, context, 'equipment-assembly' if formal_character else 'scene', parameters, complete)
        except (CoreError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_instance(bpy.types.Operator):
    bl_idname = 'sora.select_instance'
    bl_label = 'Next imported instance'
    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and not tasks.busy()
    def execute(self, context):
        roots = library_ui.visible_roots(context)
        if not roots:
            return {'CANCELLED'}
        from .equipment import owner_collection
        current = owner_collection(context)
        index = next((i for i, c in enumerate(roots) if c == current), -1)
        collection = roots[(index + 1) % len(roots)]
        try:
            library_ui.select(context,collection['sora_instance'])
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}


class SORA_OT_instance_search(bpy.types.Operator):
    bl_idname = 'sora.find_instance'
    bl_label = '搜索 / 选择导入实例'
    bl_property = 'instance'
    instance: EnumProperty(name='当前场景实例', items=library_ui.instance_items)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and not tasks.busy()

    def invoke(self, context, event):
        if not library_ui.visible_roots(context):
            self.report({'INFO'},'当前场景没有可见的导入实例')
            return {'CANCELLED'}
        self._instance_choices = library_ui.instance_items(self,context)
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            library_ui.select(context,self.instance)
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}


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


def wrapped_label(layout, value, context):
    """Wrap text using display-cell width; never discard the error payload."""
    import unicodedata
    width = max(16, int((context.region.width / context.preferences.system.ui_scale - 42) / 7))
    for paragraph in str(value or '').splitlines():
        line, cells = '', 0
        for char in paragraph:
            size = 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
            if cells + size > width and line:
                layout.label(text=line)
                line, cells = '', 0
            line += char
            cells += size
        if line:
            layout.label(text=line)


def instance_name(context):
    from .equipment import owner_collection
    collection = owner_collection(context)
    if collection is None:
        return 'No imported instance selected'
    rig = next((o for o in collection.objects if o.type == 'ARMATURE' and not o.get('sora_display_source')), None)
    return rig.name if rig else collection.name


class SORA_OT_copy_diagnostics(bpy.types.Operator):
    bl_idname = 'sora.copy_diagnostics'
    bl_label = 'Copy complete error details'
    def execute(self, context):
        context.window_manager.clipboard = context.scene.sora.task_error or context.scene.sora.status
        return {'FINISHED'}


class SORA_UL_assets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        column = layout.column(align=True)
        column.label(text=item.display_zh or item.name, icon='OUTLINER_OB_MESH')
        column.label(text=item.internal_name + (' [Chinese mapping unavailable]' if not item.display_zh else ''))


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
        for line in database_budget_lines(settings):
            box.label(text=line)
        box = layout.box()
        box.enabled = not settings.task_running
        box.label(text='资源库')
        box.row(align=True).prop(settings, 'category', expand=True)
        if settings.category == 'SCENES':
            box.label(text='区域 / 过场：占位，尚未支持', icon='INFO')
        else:
            if settings.category == 'PEOPLE': box.row(align=True).prop(settings, 'kind', expand=True)
            else: box.label(text='通用武器（按后端支持范围）')
            box.prop(settings, 'query')
            box.operator('sora.search')
            box.template_list('SORA_UL_assets', 'sora_assets', settings, 'assets', settings, 'selected', rows=4)
            row = box.row(align=True)
            previous = row.row(); previous.enabled = settings.offset > 0
            previous.operator('sora.search', text='', icon='TRIA_LEFT').direction = -1
            row.label(text=f'{settings.offset + 1 if settings.total else 0}–{settings.offset + len(settings.assets)} / {settings.total}')
            following = row.row(); following.enabled = settings.offset + len(settings.assets) < settings.total
            following.operator('sora.search', text='', icon='TRIA_RIGHT').direction = 1
            if 0 <= settings.selected < len(settings.assets):
                asset = settings.assets[settings.selected]
                box.label(text='中文: ' + (asset.display_zh or '中文映射不可用'))
                box.label(text='内部: ' + asset.internal_name)
                box.label(text='可导入' if asset.can_attempt_import else '不可导入：展开详情查看原因',
                          icon='CHECKMARK' if asset.can_attempt_import else 'INFO')
                box.prop(settings, 'asset_details', icon='DISCLOSURE_TRI_DOWN' if settings.asset_details else 'DISCLOSURE_TRI_RIGHT')
                if settings.asset_details:
                    if asset.resource_path: wrapped_label(box, asset.resource_path, context)
                    wrapped_label(box, asset.detail, context)
                    wrapped_label(box, asset.reason, context)
            box.prop(settings, 'material_mode', text='Import materials')
            box.operator('sora.import_asset')
            reason = import_reason(context)
            if reason and (settings.asset_details or not 0 <= settings.selected < len(settings.assets)):
                wrapped_label(box, reason, context)
        box = layout.box()
        wrapped_label(box, '当前实例: ' + instance_name(context), context)
        buttons = box.column(align=True)
        buttons.enabled = not settings.task_running
        select_row = buttons.row(align=True)
        select_row.operator_context = 'INVOKE_DEFAULT'
        select_row.operator('sora.find_instance', icon='VIEWZOOM')
        select_row.operator('sora.select_instance', text='', icon='TRIA_RIGHT')
        buttons.operator('sora.remove_import')
        box.row(align=True).prop(settings, 'function_page', expand=True)
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
            from . import pose_controls
            pose_controls.draw(box, context)
        elif settings.function_page == 'MATERIAL':
            from . import render_modes
            render_modes.draw(box, context)
            box.prop(settings, 'npr_post_processing')
            box.label(text='Post-processing affects the entire scene', icon='INFO')
        else:
            from . import equipment
            equipment.draw(box, context)
        wrapped_label(layout, 'Task failed; open error details' if settings.task_error else settings.status, context)
        for notice_key in ('endf_outline_migration_notice', 'endf_npr_post_notice'):
            notice = context.scene.get(notice_key)
            if notice:
                wrapped_label(layout.box(), notice, context)
        if settings.task_error:
            layout.prop(settings, 'diagnostics')
            if settings.diagnostics:
                details = layout.box()
                details.operator('sora.copy_diagnostics', icon='COPYDOWN')
                wrapped_label(details, settings.task_error, context)


CLASSES = (SORA_Preferences, SORA_AssetRow, SORA_Settings, SORA_OT_check, SORA_OT_cancel, SORA_OT_database, SORA_OT_instance, SORA_OT_instance_search, SORA_OT_stick, SORA_OT_search, SORA_OT_import, SORA_OT_remove, SORA_OT_clip, SORA_OT_copy_diagnostics, SORA_UL_assets, SORA_PT_panel)


@persistent
def migrate_saved_sources(_=None):
    from pathlib import Path
    from .migration import migrate_scene
    for scene in bpy.data.scenes:
        migrate_scene(scene, bpy.path.abspath, lambda value: Path(value).is_dir())


def _migrate_sources_timer():
    migrate_saved_sources()
    return None


def register():
    from . import post, ruri_adapter, material_panel, face_controls, animation_panel, pose_controls, equipment, generic_weapons, equipment_animation
    from .registration import RegistrationTransaction
    transaction = RegistrationTransaction(bpy, __package__, (
        (bpy.types.Scene, 'sora'), (bpy.types.Scene, 'sora_animation'), (bpy.types.Scene, 'sora_weapons'),
        (bpy.types.Scene, 'sora_equipment_animation'),
        (bpy.types.Object, 'sora_face_browser'),
        (bpy.types.WindowManager, 'endf_npr_search')))
    try:
        ruri_adapter.register()
        for cls in CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.Scene.sora = bpy.props.PointerProperty(type=SORA_Settings)
        post.register()
        material_panel.register()
        face_controls.register()
        animation_panel.register()
        pose_controls.register()
        equipment.register()
        generic_weapons.register()
        equipment_animation.register()
        tasks.register()
        if migrate_saved_sources not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(migrate_saved_sources)
        if not bpy.app.timers.is_registered(_migrate_sources_timer):
            bpy.app.timers.register(_migrate_sources_timer, first_interval=0.0)
    except Exception as error:
        failures = transaction.rollback()
        if failures:
            raise RuntimeError(str(error) + '; registration rollback: ' + '; '.join(failures)) from error
        raise


def unregister():
    if bpy.app.timers.is_registered(_migrate_sources_timer):
        bpy.app.timers.unregister(_migrate_sources_timer)
    if migrate_saved_sources in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_saved_sources)
    tasks.unregister()
    from . import pose_controls, equipment, generic_weapons, equipment_animation
    equipment_animation.unregister()
    generic_weapons.unregister()
    equipment.unregister()
    pose_controls.unregister()
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
