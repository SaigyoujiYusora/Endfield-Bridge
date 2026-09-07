bl_info = {
    "name": "Endfield-Bridge", "author": "SaigyoujiYusora", "version": (0, 1, 0),
    "blender": (5, 1, 0), "location": "View3D > Sidebar > ENDF2Blend",
    "description": "Import Sora-Core scenes, drive faces and play animations", "category": "Import-Export",
}

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty

from .client import CoreError, request
from .scene import apply_clip, create_scene


class SORA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    executable: StringProperty(name="Sora-Core executable", subtype="FILE_PATH")

    def draw(self, context):
        self.layout.prop(self, "executable")


class SORA_AssetRow(bpy.types.PropertyGroup):
    identity: StringProperty()
    detail: StringProperty()
    has_scene: BoolProperty(default=False)


class SORA_Settings(bpy.types.PropertyGroup):
    database: StringProperty(name="Sora Endfield Database", subtype="FILE_PATH")
    query: StringProperty(name="Search")
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


class SORA_OT_search(bpy.types.Operator):
    bl_idname = "sora.search"
    bl_label = "Search Database"

    def execute(self, context):
        settings = context.scene.sora
        try:
            result = call(context, "search", path=bpy.path.abspath(settings.database), query=settings.query, limit=1000)
            settings.assets.clear()
            for source in result["rows"]:
                row = settings.assets.add()
                row.name = source["label"]
                row.identity = source["id"]
                row.detail = source["detail"]
                row.has_scene = source["hasScene"]
            settings.result_database = bpy.path.abspath(settings.database)
            settings.selected = 0
            settings.status = f'{result["total"]} matches; {len(settings.assets)} displayed'
            return {"FINISHED"}
        except (CoreError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SORA_OT_import(bpy.types.Operator):
    bl_idname = "sora.import_asset"
    bl_label = "Import Selected Asset"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "sora", None)
        return settings is not None and context.mode == "OBJECT" and settings.result_database == bpy.path.abspath(settings.database) and 0 <= settings.selected < len(settings.assets) and settings.assets[settings.selected].has_scene

    def execute(self, context):
        settings = context.scene.sora
        try:
            identity = settings.assets[settings.selected].identity
            database = bpy.path.abspath(settings.database)
            document = call(context, "scene", path=database, asset=identity)
            collection, rig = create_scene(context, document)
            for obj in collection.objects:
                obj["sora_asset"] = identity
                obj["sora_database"] = database
            settings.status = f'Imported {document["name"]}'
            settings.clip = document["clips"][0]["name"] if document["clips"] else ""
            return {"FINISHED"}
        except (CoreError, ValueError, KeyError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SORA_OT_clip(bpy.types.Operator):
    bl_idname = "sora.load_clip"
    bl_label = "Load Animation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "POSE"} and context.object is not None and context.object.type == "ARMATURE" and bool(context.object.get("sora_asset"))

    def execute(self, context):
        rig = context.object
        try:
            document = call(context, "scene", path=rig["sora_database"], asset=rig["sora_asset"])
            clip = next((clip for clip in document["clips"] if clip["name"] == context.scene.sora.clip), None)
            if clip is None:
                raise ValueError("Animation name is not present in this asset")
            apply_clip(context, rig, clip, [bone["name"] for bone in document["bones"]])
            return {"FINISHED"}
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
        preferences = context.preferences.addons.get(__package__)
        if preferences:
            layout.prop(preferences.preferences, "executable")
        layout.operator("sora.check")
        layout.prop(settings, "database")
        layout.prop(settings, "query")
        layout.operator("sora.search")
        layout.template_list("UI_UL_list", "sora_assets", settings, "assets", settings, "selected", rows=4)
        layout.operator("sora.import_asset")
        layout.label(text="Materials: Basic PBR")
        layout.label(text=settings.status)
        box = layout.box()
        box.label(text="Face Driver")
        obj = context.object
        has_face_channels = False
        if obj is not None and obj.get("sora_instance"):
            for key in obj.keys():
                if key.startswith("face_"):
                    has_face_channels = True
                    box.prop(obj, '["' + key + '"]', text=obj.get("sora_face_" + key, key), slider=True)
        if not has_face_channels:
            box.label(text="No face channels on this object")
        box = layout.box()
        box.label(text="Animation Player")
        box.prop(settings, "clip")
        box.operator("sora.load_clip")
        box.operator("screen.animation_play", text="Play / Pause", icon="PLAY")


CLASSES = (SORA_Preferences, SORA_AssetRow, SORA_Settings, SORA_OT_check, SORA_OT_search, SORA_OT_import, SORA_OT_clip, SORA_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sora = bpy.props.PointerProperty(type=SORA_Settings)


def unregister():
    del bpy.types.Scene.sora
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
