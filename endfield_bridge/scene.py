import math
import json
import uuid

import bpy
from mathutils import Matrix, Vector
from .materials import build_material, load_images


def create_scene(context, document, material_mode=None):
    material_mode = material_mode or getattr(getattr(context.scene, "sora", None), "material_mode", "RURI")
    if material_mode in {"RURI", "NPR"}:
        from . import ruri_adapter
    if any(len(bone["name"].encode("utf-8")) > 63 for bone in document["bones"]):
        raise ValueError("Bone names must fit Blender's 63-byte UTF-8 limit")
    token = uuid.uuid4().hex
    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before importing")
    shader_frame = material_mode in {"RURI", "NPR"} and any(m.get("npr") for m in document["materials"])
    native_bones = document["bones"]
    if shader_frame:
        from .ruri_adapter import object_frame
        document = object_frame(document)
    collection = None
    created = []
    owned_data = []
    materials = []
    images = []
    material_cache = {}
    rig = None
    try:
        collection = bpy.data.collections.new(document["name"])
        collection["sora_instance"] = token
        context.scene.collection.children.link(collection)
        image_map = load_images(document.get("textures") or [], token, images, document.get("textureDescriptors"), material_mode in {"RURI", "NPR"})
        def material_for(indices):
            key = tuple(indices)
            if key not in material_cache:
                material = build_material([document["materials"][index] for index in indices], image_map, token, material_mode)
                materials.append(material)
                material_cache[key] = material
            return material_cache[key]
        if document["bones"]:
            armature = bpy.data.armatures.new(document["name"])
            owned_data.append(armature)
            rig = bpy.data.objects.new(document["name"], armature)
            created.append(rig)
            collection.objects.link(rig)
            rig["sora_instance"] = token
            head = document.get("headReference")
            rig["sora_head_bone"] = head["name"] if head else "Bip001_Head"
            if head:
                rig["sora_head_reference"] = json.dumps(head)
            rig["sora_head_basis"] = "Frontend rest forward (0,-1,0); native head-axis mapping unverified"
            for selected in context.selected_objects:
                selected.select_set(False)
            rig.select_set(True)
            context.view_layer.objects.active = rig
            bpy.ops.object.mode_set(mode="EDIT")
            bones = []
            for source, native_source in zip(document["bones"], native_bones):
                bone = armature.edit_bones.new(source["name"])
                bone.head = source["head"]
                bone.tail = source["tail"]
                bone.roll = source.get("roll", 0.0)
                if source.get("restMatrix") is not None:
                    values = source["restMatrix"]
                    basis = Matrix([values[row * 4:row * 4 + 4] for row in range(4)])
                    bone.matrix = basis
                    bone.length = (Vector(source["tail"]) - Vector(source["head"])).length
                    bone.align_roll(basis.col[2].to_3d())
                elif shader_frame:
                    # A roll is relative to Blender's direction-dependent bone
                    # frame. Rotating head/tail while retaining that scalar does
                    # not preserve the native rest orientation (notably +Z bones).
                    bone.head = native_source["head"]
                    bone.tail = native_source["tail"]
                    bone.roll = native_source.get("roll", 0.0)
                    basis = Matrix.Diagonal((-1.0, -1.0, 1.0, 1.0)) @ bone.matrix
                    bone.matrix = basis
                    bone.align_roll(basis.col[2].to_3d())
                if source["parent"] >= 0:
                    bone.parent = bones[source["parent"]]
                bones.append(bone)
            bpy.ops.object.mode_set(mode="OBJECT")
            for source in document["bones"]:
                bone = armature.bones[source["name"]]
                bone["sora_source_path"] = source.get("sourcePath") or ""
                if source.get('sourceHash') is not None:
                    bone['sora_source_hash'] = str(int(source['sourceHash']))
            if shader_frame:
                rig.rotation_euler.z = math.pi
            rig.show_in_front = True
        mesh_sources = document["meshes"]
        for source in mesh_sources:
            mesh = bpy.data.meshes.new(source["name"])
            owned_data.append(mesh)
            obj = bpy.data.objects.new(source["name"], mesh)
            created.append(obj)
            collection.objects.link(obj)
            obj["sora_instance"] = token
            mesh.from_pydata(source["positions"], [], source["triangles"])
            mesh.update()
            for polygon in mesh.polygons:
                polygon.use_smooth = True
            if source["normals"]:
                mesh.normals_split_custom_set_from_vertices(source["normals"])
            uv_sets = source.get("uvSets") or ([{"set": 0, "values": source["uv"]}] if source["uv"] else [])
            for uv_set in uv_sets:
                layer = mesh.uv_layers.new(name="UVMap" + (str(uv_set["set"]) if uv_set["set"] else ""))
                for loop in mesh.loops:
                    components = uv_set["values"][loop.vertex_index]
                    layer.data[loop.index].uv = (components[0], components[1] if len(components) > 1 else 0)
                dimension = len(uv_set["values"][0]) if uv_set["values"] else 0
                for component in range(2, dimension):
                    attribute = mesh.attributes.new(name=f"SoraUV{uv_set['set']}_{'ZW'[component - 2]}", type="FLOAT", domain="POINT")
                    for item, components in zip(attribute.data, uv_set["values"]):
                        item.value = components[component]
                if uv_set.get("nativeDimension") is not None:
                    mesh[f"sora_uv{uv_set['set']}_native_dimension"] = uv_set["nativeDimension"]
                if uv_set.get("nativeFormat") is not None:
                    mesh[f"sora_uv{uv_set['set']}_native_format"] = uv_set["nativeFormat"]
            if source.get("colors"):
                attr = mesh.color_attributes.new(name="SoraColor", type="FLOAT_COLOR", domain="POINT")
                for item, rgba in zip(attr.data, source["colors"]):
                    item.color = rgba
            if source.get("tangents"):
                tangent = mesh.attributes.new(name="SoraTangent", type="FLOAT_VECTOR", domain="POINT")
                sign = mesh.attributes.new(name="SoraTangentSign", type="FLOAT", domain="POINT")
                for i, xyzw in enumerate(source["tangents"]):
                    tangent.data[i].vector = xyzw[:3]
                    sign.data[i].value = xyzw[3]
            if source.get("sourceId"):
                obj["sora_source_id"] = str(source["sourceId"])
            slots = source.get("materialSlots")
            if slots is not None and all(index >= 0 for index in slots):
                count = source.get("submeshCount", 1)
                for slot in range(len(slots) if material_mode in {"RURI", "NPR"} else count):
                    indices = [slots[slot]] if material_mode in {"RURI", "NPR"} else (slots[slot:] if slot == count - 1 else [slots[slot]])
                    mesh.materials.append(material_for(indices))
                for polygon, slot in zip(mesh.polygons, source["triangleSlots"]):
                    polygon.material_index = slot
            elif source["material"] >= 0:
                mesh.materials.append(material_for([source["material"]]))
            if rig is not None and source["weights"]:
                groups = [obj.vertex_groups.new(name=bone["name"]) for bone in document["bones"]]
                for weight in source["weights"]:
                    groups[weight["bone"]].add([weight["vertex"]], weight["weight"], "REPLACE")
                modifier = obj.modifiers.new("Sora Skin", "ARMATURE")
                modifier.object = rig
                obj.parent = rig
            elif shader_frame:
                obj.rotation_euler.z = math.pi
            if source["shapes"]:
                obj.shape_key_add(name="Basis")
                for shape_index, source_shape in enumerate(source["shapes"]):
                    shape = obj.shape_key_add(name=source_shape["name"])
                    for vertex, position, offset in zip(shape.data, source["positions"], source_shape["offsets"]):
                        vertex.co = tuple(a + b for a, b in zip(position, offset))
                    property_name = "face_" + str(shape_index)
                    obj[property_name] = 0.0
                    obj.id_properties_ui(property_name).update(min=0.0, max=1.0, description=shape.name)
                    driver = shape.driver_add("value").driver
                    driver.type = "AVERAGE"
                    variable = driver.variables.new()
                    variable.name = "value"
                    variable.type = "SINGLE_PROP"
                    variable.targets[0].id = obj
                    variable.targets[0].data_path = '["' + property_name + '"]'
                    obj["sora_face_" + property_name] = shape.name
            if material_mode in {"RURI", "NPR"}:
                ruri_adapter.prepare_mesh(obj, source)
        if rig is not None and document.get('faceDriver'):
            from . import face_controls
            face_controls.install(rig, document['faceDriver'], document['bones'])
        context.view_layer.update()
        if material_mode in {"RURI", "NPR"}:
            ruri_adapter.finish_import(context, [obj for obj in created if obj.type == "MESH"])
            if any(material.get('ruri_uber_stack') for material in materials):
                from .post import enable_for_import
                enable_for_import(context, material_mode)
        return collection, rig
    except Exception:
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in reversed(owned_data):
            if data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Armature):
                    bpy.data.armatures.remove(data)
        _release_instance_data(token)
        if collection is not None:
            bpy.data.collections.remove(collection, do_unlink=True)
        raise
    finally:
        for selected in list(context.selected_objects):
            selected.select_set(False)
        for selected in previous_selected:
            if selected.name in context.view_layer.objects:
                selected.select_set(True)
        if previous_active is not None and previous_active.name in context.view_layer.objects:
            context.view_layer.objects.active = previous_active
        else:
            context.view_layer.objects.active = None


def _release_instance_data(token, outline_tokens=()):
    """Release instance references in dependency order, retaining shared data."""
    from .ruri_adapter import release_instance
    release_instance(token)
    for material in list(bpy.data.materials):
        owned = material.get("sora_instance") == token or material.get("sora_outline_owner") in outline_tokens
        if owned and material.users == 0:
            bpy.data.materials.remove(material)
    release_instance(token)
    # Includes private alpha views created by the NPR adapter, which are not
    # part of load_images' list. Never delete an image still used elsewhere.
    for image in list(bpy.data.images):
        if image.get("sora_instance") == token and image.users == 0:
            bpy.data.images.remove(image)
    for action in list(bpy.data.actions):
        if action.get("sora_instance") == token and action.users == 0:
            bpy.data.actions.remove(action)


def remove_scene(context, collection):
    """Remove one explicitly selected imported collection and unused owned data."""
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before removing an import")
    token = collection.get("sora_instance")
    if not token:
        raise ValueError("Collection is not an Endfield-Bridge import")
    objects = list(collection.objects)
    if any(obj.get("sora_instance") != token for obj in objects):
        raise ValueError("Collection contains objects outside this import")
    if any(len(obj.users_collection) != 1 for obj in objects):
        raise ValueError("An imported object is linked to another collection")
    outline_tokens = {obj.get("sora_outline_owner") for obj in objects if obj.get("sora_outline_owner")}
    data_blocks = {obj.data for obj in objects if obj.data}
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if group.get("sora_outline_owner") in outline_tokens and group.users == 0:
            bpy.data.node_groups.remove(group)
    for data in data_blocks:
        if data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
    _release_instance_data(token, outline_tokens)
    bpy.data.collections.remove(collection)


def apply_clip(context, rig, clip, bone_names, bone_sources=None, keep_face_controls=False):
    from .animation_actions import apply_clip as build_action
    return build_action(context, rig, clip, bone_names, bone_sources, keep_face_controls)
