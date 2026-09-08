"""Native Animation Player UI through the existing Sora-Core RPC client."""
import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty

from .client import CoreError, request
from .scene import apply_clip


def target(context):
    obj = context.object
    rig = obj if obj and obj.type == 'ARMATURE' else obj.find_armature() if obj and obj.type == 'MESH' else None
    return rig if rig and rig.get('sora_instance') and rig.get('sora_asset') and rig.get('sora_database') else None


def call(context, method, **parameters):
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        raise CoreError('Endfield-Bridge preferences are unavailable')
    return request(bpy.path.abspath(addon.preferences.executable), method, **parameters)


class SORA_AnimationRow(bpy.types.PropertyGroup):
    identity: StringProperty()
    resource_path: StringProperty()


class SORA_AnimationClipRow(bpy.types.PropertyGroup):
    cab: StringProperty()
    path_id: StringProperty()


class SORA_AnimationSettings(bpy.types.PropertyGroup):
    game_root: StringProperty(name='Game Folder', subtype='DIR_PATH')
    query: StringProperty(name='Find animation')
    rows: CollectionProperty(type=SORA_AnimationRow)
    selected: IntProperty(default=0)
    clips: CollectionProperty(type=SORA_AnimationClipRow)
    selected_clip: IntProperty(default=-1)
    clip_resource: StringProperty()
    clip_root: StringProperty()
    keep_face_controls: BoolProperty(name='Keep enabled manual Face controls', default=True,
        description='Retain all source keys in the new Action; explicitly mute only new curves overridden by owned Face drivers')
    status: StringProperty(default='Choose the game folder and search native animations')


class SORA_OT_animation_search(bpy.types.Operator):
    bl_idname = 'sora.animation_search'
    bl_label = 'Search native animations'

    def execute(self, context):
        settings = context.scene.sora_animation
        try:
            if not settings.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            parameters = {'root': bpy.path.abspath(settings.game_root), 'query': settings.query}
            rig = target(context)
            if rig is not None:
                parameters.update(path=rig['sora_database'], asset=rig['sora_asset'])
            rows = call(context, 'animation-search', **parameters)
            if not isinstance(rows, list):
                raise CoreError('Animation search returned an invalid list')
            checked = [(str(row['id']), str(row['path']), str(row['label'])) for row in rows]
            settings.rows.clear()
            for identity, path, label in checked:
                row = settings.rows.add()
                row.name, row.identity, row.resource_path = label, identity, path
            settings.selected = 0
            settings.clips.clear()
            settings.selected_clip = -1
            settings.clip_resource = ''
            settings.status = f'{len(checked)} native animations found'
            return {'FINISHED'}
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError) as error:
            settings.status = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_animation_clips(bpy.types.Operator):
    bl_idname = 'sora.animation_clips'
    bl_label = 'List clips in selected resource'

    def execute(self, context):
        settings = context.scene.sora_animation
        try:
            if not settings.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            if not 0 <= settings.selected < len(settings.rows):
                raise ValueError('Select a native animation resource')
            resource = settings.rows[settings.selected].resource_path
            root = bpy.path.abspath(settings.game_root)
            parameters = {'root': root, 'resource': resource}
            rig = target(context)
            if rig is not None:
                parameters.update(path=rig['sora_database'], asset=rig['sora_asset'])
            result = call(context, 'animation-clips', **parameters)
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
            settings.status = f'{len(checked)} clips found; select the clip to load'
            return {'FINISHED'}
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError) as error:
            settings.status = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class SORA_OT_animation_import(bpy.types.Operator):
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
            if not settings.game_root.strip():
                raise ValueError('Choose the native Game Folder')
            if not 0 <= settings.selected < len(settings.rows):
                raise ValueError('Select a native animation from the search results')
            row = settings.rows[settings.selected]
            if settings.clip_resource != row.resource_path or settings.clip_root != bpy.path.abspath(settings.game_root):
                raise ValueError('List the clips in this resource before loading')
            if not 0 <= settings.selected_clip < len(settings.clips):
                raise ValueError('Select an exact clip from this resource')
            selected = settings.clips[settings.selected_clip]
            result = call(context, 'animation-import', root=bpy.path.abspath(settings.game_root),
                          path=rig['sora_database'], asset=rig['sora_asset'], resource=row.resource_path,
                          selection={'cab': selected.cab, 'pathId': selected.path_id})
            clip, bones = result['clip'], result['bones']
            metadata = {key:value for key,value in result.items() if key not in {'clip','bones'}}
            if metadata:
                clip = dict(clip)
                native = dict(clip.get('native') or {})
                for key,value in metadata.items():
                    if key in native and native[key] != value:
                        raise CoreError('Conflicting native animation metadata: ' + key)
                    native[key] = value
                clip['native'] = native
            action = apply_clip(context, rig, clip, [bone['name'] for bone in bones],
                                bone_sources=bones, keep_face_controls=settings.keep_face_controls)
            settings.status = 'Loaded ' + clip['name'] + ('; manual Face controls override retained face keys' if action.get('sora_face_mode') == 'MANUAL' else '')
            return {'FINISHED'}
        except (CoreError, ValueError, KeyError, TypeError, RuntimeError, OverflowError) as error:
            settings.status = str(error)
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


def draw(layout, context):
    settings = context.scene.sora_animation
    layout.prop(settings, 'game_root')
    layout.prop(settings, 'query')
    layout.operator('sora.animation_search')
    layout.template_list('UI_UL_list', 'native_animations', settings, 'rows', settings, 'selected', rows=5)
    layout.operator('sora.animation_clips')
    if (0 <= settings.selected < len(settings.rows)
            and settings.clip_resource == settings.rows[settings.selected].resource_path
            and settings.clip_root == bpy.path.abspath(settings.game_root)):
        layout.template_list('UI_UL_list', 'native_animation_clips', settings, 'clips', settings, 'selected_clip', rows=3)
    layout.prop(settings, 'keep_face_controls')
    layout.operator('sora.animation_import')
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
           SORA_OT_animation_clips, SORA_OT_animation_import, SORA_OT_animation_face)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sora_animation = PointerProperty(type=SORA_AnimationSettings)


def unregister():
    del bpy.types.Scene.sora_animation
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
