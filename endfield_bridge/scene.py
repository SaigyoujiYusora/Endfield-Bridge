import math
import uuid

import bpy
from mathutils import Matrix, Vector
from .materials import build_material, load_images


def create_scene(context, document):
    if any(len(bone["name"].encode("utf-8")) > 63 for bone in document["bones"]):
        raise ValueError("Bone names must fit Blender's 63-byte UTF-8 limit")
    token = uuid.uuid4().hex
    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before importing")
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
        image_map = load_images(document.get("textures") or [], token, images)
        def material_for(indices):
            key = tuple(indices)
            if key not in material_cache:
                material = build_material([document["materials"][index] for index in indices], image_map, token)
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
            for selected in context.selected_objects:
                selected.select_set(False)
            rig.select_set(True)
            context.view_layer.objects.active = rig
            bpy.ops.object.mode_set(mode="EDIT")
            bones = []
            for source in document["bones"]:
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
                if source["parent"] >= 0:
                    bone.parent = bones[source["parent"]]
                bones.append(bone)
            bpy.ops.object.mode_set(mode="OBJECT")
            rig.show_in_front = True
        for source in document["meshes"]:
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
            if source["uv"]:
                layer = mesh.uv_layers.new(name="UVMap")
                for loop in mesh.loops:
                    layer.data[loop.index].uv = source["uv"][loop.vertex_index]
            slots = source.get("materialSlots")
            if slots is not None and all(index >= 0 for index in slots):
                count = source.get("submeshCount", 1)
                for slot in range(count):
                    indices = slots[slot:] if slot == count - 1 else [slots[slot]]
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
        context.view_layer.update()
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
        for material in materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        for image in images:
            if image.users == 0:
                bpy.data.images.remove(image)
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


def apply_clip(context, rig, clip, bone_names):
    if context.mode not in {"OBJECT", "POSE"}:
        raise ValueError("Switch to Object or Pose Mode before loading an animation")
    if rig is None or rig.type != "ARMATURE" or not rig.get("sora_instance"):
        raise ValueError("Select a Sora-Core imported armature")
    for track in clip["tracks"]:
        if bone_names[track["bone"]] not in rig.pose.bones:
            raise ValueError("Animation skeleton does not match the imported armature")
    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_slot = rig.animation_data.action_slot if rig.animation_data else None
    had_animation_data = rig.animation_data is not None
    timing = (context.scene.render.fps, context.scene.render.fps_base, context.scene.frame_start, context.scene.frame_end, context.scene.frame_current, context.scene.frame_subframe)
    pose_state = [(bone, bone.rotation_mode, bone.matrix_basis.copy()) for bone in rig.pose.bones]
    action = bpy.data.actions.new(clip["name"])
    action["sora_instance"] = rig["sora_instance"]
    try:
        rig.animation_data_create()
        rig.animation_data.action = action
        for bone in rig.pose.bones:
            bone.location = (0, 0, 0)
            bone.scale = (1, 1, 1)
            bone.rotation_euler = (0, 0, 0)
            bone.rotation_quaternion = (1, 0, 0, 0)
            bone.rotation_axis_angle = (0, 0, 1, 0)
        for track in clip["tracks"]:
            bone = rig.pose.bones[bone_names[track["bone"]]]
            attribute = {"location": "location", "rotation": "rotation_quaternion", "scale": "scale"}[track["channel"]]
            if track["channel"] == "rotation":
                bone.rotation_mode = "QUATERNION"
            previous_quaternion = None
            for key in track["keys"]:
                value = key["value"]
                if track["channel"] == "rotation":
                    value = [value[3], value[0], value[1], value[2]]
                    if previous_quaternion is not None and sum(a * b for a, b in zip(value, previous_quaternion)) < 0:
                        value = [-component for component in value]
                    previous_quaternion = value
                setattr(bone, attribute, value)
                bone.keyframe_insert(data_path=attribute, frame=1 + key["time"] * clip["fps"], group=bone.name)
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for point in curve.keyframe_points:
                            point.interpolation = "LINEAR"
        action.use_fake_user = True
        context.scene.render.fps = round(clip["fps"])
        context.scene.render.fps_base = context.scene.render.fps / clip["fps"]
        context.scene.frame_start = 1
        context.scene.frame_end = max(1, math.ceil(clip["duration"] * clip["fps"]) + 1)
        context.scene.frame_set(1)
        if previous_action is not None:
            previous_action.use_fake_user = True
        return action
    except Exception:
        if had_animation_data:
            rig.animation_data.action = previous_action
            if previous_slot is not None:
                rig.animation_data.action_slot = previous_slot
        else:
            rig.animation_data_clear()
        bpy.data.actions.remove(action)
        context.scene.render.fps, context.scene.render.fps_base, context.scene.frame_start, context.scene.frame_end = timing[:4]
        context.scene.frame_set(timing[4], subframe=timing[5])
        for bone, mode, matrix in pose_state:
            bone.rotation_mode = mode
            bone.matrix_basis = matrix
        raise
