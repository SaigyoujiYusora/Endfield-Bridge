import math
import json
import uuid

import bpy
from mathutils import Matrix, Vector
from .materials import build_material, build_material_steps, load_images


def create_scene(context, document, material_mode=None):
    """Synchronous compatibility entry point; UI consumes the same generator."""
    work = create_scene_steps(context, document, material_mode)
    while True:
        try:
            next(work)
        except StopIteration as finished:
            return finished.value


def create_scene_steps(context, document, material_mode=None):
    material_mode = material_mode or getattr(getattr(context.scene, "sora", None), "material_mode", "RURI")
    if material_mode in {"RURI", "NPR"}:
        from . import ruri_adapter
    if any(len(bone["name"].encode("utf-8")) > 63 for bone in document["bones"]):
        raise ValueError("Bone names must fit Blender's 63-byte UTF-8 limit")
    from .scene_nodes import validate_nodes, create_nodes_steps, bind_mesh_node
    node_records = validate_nodes(document)
    token = uuid.uuid4().hex
    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before importing")
    shader_frame = any(m.get("npr") for m in document["materials"])
    native_bones = document["bones"]
    if shader_frame:
        from . import ruri_adapter
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
        nodes = yield from create_nodes_steps(collection, token, node_records, created)
        image_map = {}
        textures = document.get("textures") or []
        for index, texture in enumerate(textures):
            yield {"stage": "Loading textures", "completed": index, "total": len(textures)}
            image_map.update(load_images([texture], token, images, document.get("textureDescriptors"), material_mode in {"RURI", "NPR"}))
        yield {"stage": "Creating skeleton"}
        def material_for_steps(indices, detail):
            key = tuple(indices)
            if key in material_cache:
                return material_cache[key]
            yield {"stage": "Building materials", "completed": mesh_index, "total": len(mesh_sources),
                   "detail": detail}
            material = yield from build_material_steps([document["materials"][index] for index in indices],
                                                       image_map, token, material_mode)
            materials.append(material)
            material_cache[key] = material
            return material
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
            for source_index, source in enumerate(document["bones"]):
                bone = armature.bones[source["name"]]
                bone["sora_source_path"] = source.get("sourcePath") or ""
                bone["sora_source_index"] = source_index
                if source.get('sourceHash') is not None:
                    bone['sora_source_hash'] = str(int(source['sourceHash']))
            if shader_frame:
                rig.rotation_euler.z = math.pi
            armature.display_type = "STICK"
            rig.show_in_front = True
            from .pose_controls import record_import_pose
            record_import_pose(rig, document.get('faceDriver'))
        mesh_sources = document["meshes"]
        for mesh_index, source in enumerate(mesh_sources):
            yield {"stage": "Creating meshes", "completed": mesh_index, "total": len(mesh_sources)}
            mesh = bpy.data.meshes.new(source["name"])
            owned_data.append(mesh)
            obj = bpy.data.objects.new(source["name"], mesh)
            created.append(obj)
            collection.objects.link(obj)
            obj["sora_instance"] = token
            obj["sora_render_mesh_index"] = mesh_index
            # Native m_Enabled=false parts keep geometry/slots but start hidden; absent means enabled.
            if not source.get("rendererEnabled", True):
                obj.hide_viewport = True
                obj.hide_render = True
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
            yield {"stage": "Building materials", "completed": mesh_index, "total": len(mesh_sources)}
            slots = source.get("materialSlots")
            if slots is not None and all(index >= 0 for index in slots):
                count = source.get("submeshCount", 1)
                for slot in range(len(slots) if material_mode in {"RURI", "NPR"} else count):
                    indices = [slots[slot]] if material_mode in {"RURI", "NPR"} else (slots[slot:] if slot == count - 1 else [slots[slot]])
                    mesh.materials.append((yield from material_for_steps(
                        indices, document["materials"][indices[0]]["name"])))
                for polygon, slot in zip(mesh.polygons, source["triangleSlots"]):
                    polygon.material_index = slot
            elif source["material"] >= 0:
                mesh.materials.append((yield from material_for_steps(
                    [source["material"]], document["materials"][source["material"]]["name"])))
            if rig is not None and source["weights"]:
                groups = [obj.vertex_groups.new(name=bone["name"]) for bone in document["bones"]]
                for weight_index, weight in enumerate(source["weights"]):
                    if weight_index % 4096 == 0:
                        yield {"stage": "Binding skin weights", "completed": weight_index, "total": len(source["weights"])}
                    groups[weight["bone"]].add([weight["vertex"]], weight["weight"], "REPLACE")
                modifier = obj.modifiers.new("Sora Skin", "ARMATURE")
                modifier.object = rig
                obj.parent = rig
            elif shader_frame:
                obj.rotation_euler.z = math.pi
            bind_mesh_node(obj, source, nodes, shader_frame)
            if source["shapes"]:
                obj.shape_key_add(name="Basis")
                for shape_index, source_shape in enumerate(source["shapes"]):
                    yield {"stage": "Creating face shapes", "completed": shape_index, "total": len(source["shapes"])}
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
            if shader_frame:
                ruri_adapter.prepare_mesh(obj, source)
        if rig is not None and document.get('faceDriver'):
            from . import face_controls
            face_controls.install(rig, document['faceDriver'], document['bones'])
        yield {"stage": "Finishing scene binding"}
        context.view_layer.update()
        if material_mode in {"RURI", "NPR"}:
            ruri_adapter.finish_import(context, [obj for obj in created if obj.type == "MESH"])
            if any(material.get('ruri_uber_stack') for material in materials):
                from .post import enable_for_import
                enable_for_import(context, material_mode)
        from .render_modes import initialize
        initialize(collection, document, material_mode, shader_frame)
        return collection, rig
    except BaseException:
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


def _validate_remove_tree(collection, seen=None):
    seen=set() if seen is None else seen
    if collection.as_pointer() in seen:raise ValueError('Imported collection ownership cycle')
    seen.add(collection.as_pointer())
    token=collection.get('sora_instance')
    if not token or any(obj.get('sora_instance')!=token or len(obj.users_collection)!=1 for obj in collection.objects):
        raise ValueError('Collection contains shared or foreign objects')
    for child in collection.children:
        if child.get('sora_owner_collection')!=collection:
            raise ValueError('Collection contains a child outside this imported instance')
        _validate_remove_tree(child,seen)


def remove_scene(context, collection, force_cleanup=False):
    """Remove one explicitly selected imported collection and unused owned data."""
    if context.mode != "OBJECT" and not force_cleanup:
        raise ValueError("Switch to Object Mode before removing an import")
    _validate_remove_tree(collection)
    token = collection.get("sora_instance")
    if not token:
        raise ValueError("Collection is not an Endfield-Bridge import")
    objects = list(collection.objects)
    if any(obj.get("sora_instance") != token for obj in objects):
        raise ValueError("Collection contains objects outside this import")
    if any(len(obj.users_collection) != 1 for obj in objects):
        raise ValueError("An imported object is linked to another collection")
    for child in list(collection.children):
        remove_scene(context,child,force_cleanup=force_cleanup)
    if force_cleanup:
        for obj in objects:
            if obj.mode != "OBJECT":
                with context.temp_override(object=obj,active_object=obj):
                    bpy.ops.object.mode_set(mode="OBJECT")
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
