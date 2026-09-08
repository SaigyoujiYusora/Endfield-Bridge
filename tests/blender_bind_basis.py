"""Visible Blender MCP native-world bind/deformation test; restores preview state.
Set RURI_DOCUMENT and optionally RURI_COLLECTION to test the loaded import.
"""
import bpy
import json
from pathlib import Path
from mathutils import Matrix, Quaternion, Vector
from endfield_bridge.scene import create_scene
from endfield_bridge.client import request

root = Path('F:/Games/Endfield-unpack/ENDF-DR')
document = globals().get('RURI_DOCUMENT')
if document is None:
    document = request(str(root / 'Sora-Core/src/Sora.Cli/bin/Release/net10.0/Sora-Core.exe'),
                       'scene', path=str(root / 'Sora-Core/artifacts/azrila-npr.sredb'),
                       asset='assets/beyond/dynamicassets/gameplay/prefabs/uimodels/chr_0009_azrila_uimodel.prefab')
collection = globals().get('RURI_COLLECTION')
if collection is None:
    collection, rig = create_scene(bpy.context, document, material_mode='NPR')
else:
    rig = next(obj for obj in collection.objects if obj.type == 'ARMATURE')
objects = [obj for obj in collection.objects if obj.type == 'MESH']
assert len(objects) == len(document['meshes']), 'Unexpected extra or missing source mesh objects'

def source_for(obj):
    candidates = [source for source in document['meshes']
                  if (source.get('sourceId') and str(source['sourceId']) == obj.get('sora_source_id'))
                  or (not source.get('sourceId') and (obj.name == source['name'] or obj.name.startswith(source['name'] + '.')))]
    assert len(candidates) == 1, (obj.name, len(candidates))
    return candidates[0]

def matrix_error(left, right):
    return max(abs(left[i][j] - right[i][j]) for i in range(4) for j in range(4))

rest = {}
for source in document['bones']:
    values = source['restMatrix']
    assert len(values) == 16, source['name']
    rest[source['name']] = Matrix([values[row * 4:row * 4 + 4] for row in range(4)])
frame = Matrix.Diagonal((-1.0, -1.0, 1.0, 1.0))
original_world = rig.matrix_world.copy()
pose_state = [(bone, bone.rotation_mode, bone.matrix_basis.copy()) for bone in rig.pose.bones]
modifier_state = [(modifier, modifier.show_viewport) for obj in objects for modifier in obj.modifiers if modifier.type != 'ARMATURE']
shape_state = [(key, key.value) for obj in objects if obj.data.shape_keys for key in obj.data.shape_keys.key_blocks]
face_property_state = [(obj, key[len('sora_face_'):], obj[key[len('sora_face_'):]])
                       for obj in objects for key in obj.keys()
                       if key.startswith('sora_face_') and key[len('sora_face_'):] in obj]
metrics = dict(maximumRestMatrixError=0.0, maximumNativeVertexError=0.0,
               maximumShapeOffsetError=0.0, maximumPosedVertexError=0.0, movedSamples=0)
try:
    # Fixed expectations catch either missing half of the compensating frame.
    assert matrix_error(rig.matrix_basis, frame) < 1e-5, 'Missing NPR object-frame compensation'
    for modifier, _ in modifier_state:
        modifier.show_viewport = False
    for key, _ in shape_state:
        key.value = 0
    for obj, key, _ in face_property_state:
        obj[key] = 0.0
    for bone, _, _ in pose_state:
        bone.matrix_basis = Matrix.Identity(4)
    rig.matrix_world = frame
    bpy.context.view_layer.update()
    for name, reference in rest.items():
        metrics['maximumRestMatrixError'] = max(metrics['maximumRestMatrixError'],
            matrix_error(rig.matrix_world @ rig.data.bones[name].matrix_local, reference))
    for obj in objects:
        source = source_for(obj)
        assert len(obj.data.vertices) == len(source['positions']), obj.name
        for vertex, position in zip(obj.data.vertices, source['positions']):
            metrics['maximumNativeVertexError'] = max(metrics['maximumNativeVertexError'],
                (obj.matrix_world @ vertex.co - Vector(position)).length)
        for shape in source['shapes']:
            key = obj.data.shape_keys.key_blocks[shape['name']]
            for vertex, basis, offset in zip(key.data, obj.data.vertices, shape['offsets']):
                metrics['maximumShapeOffsetError'] = max(metrics['maximumShapeOffsetError'],
                    (obj.matrix_world.to_3x3() @ (vertex.co - basis.co) - Vector(offset)).length)
    assert metrics['maximumRestMatrixError'] < 1e-4, metrics
    assert metrics['maximumNativeVertexError'] < 1e-4, metrics
    assert metrics['maximumShapeOffsetError'] < 1e-4, metrics
    tests = [('Bip001_R_UpperArm', (0, 1, 0), 0.45), ('Bip001_Head', (0, 1, 0), 0.35)]
    for joint_name, axis, angle in tests:
        assert joint_name in rig.pose.bones, joint_name
        for bone, _, _ in pose_state:
            bone.matrix_basis = Matrix.Identity(4)
        joint = rig.pose.bones[joint_name]
        joint.rotation_mode = 'QUATERNION'
        joint.rotation_quaternion = Quaternion(axis, angle)
        # Independent oracle: recurse native rest matrices and requested local pose.
        native_pose = {}
        for source in document['bones']:
            name = source['name']
            basis = Quaternion(axis, angle).to_matrix().to_4x4() if name == joint_name else Matrix.Identity(4)
            parent = source['parent']
            if parent >= 0:
                parent_name = document['bones'][parent]['name']
                native_pose[name] = native_pose[parent_name] @ rest[parent_name].inverted() @ rest[name] @ basis
            else:
                native_pose[name] = rest[name] @ basis
        for yaw in (0.0, 0.7):
            placement = Matrix.Translation((0.3, -0.2, 0.1)) @ Matrix.Rotation(yaw, 4, 'Z')
            rig.matrix_world = placement @ frame
            bpy.context.view_layer.update()
            transforms = {name: native_pose[name] @ reference.inverted() for name, reference in rest.items()}
            for name, expected in native_pose.items():
                assert matrix_error(rig.matrix_world @ rig.pose.bones[name].matrix, placement @ expected) < 1e-4, name
            for obj in objects:
                source = source_for(obj)
                if not source['weights']:
                    continue
                weights = {}
                for row in source['weights']:
                    weights.setdefault(row['vertex'], []).append((document['bones'][row['bone']]['name'], row['weight']))
                evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                mesh = evaluated.to_mesh()
                try:
                    assert len(mesh.vertices) == len(source['positions']), obj.name
                    for index in range(0, len(mesh.vertices), max(1, len(mesh.vertices) // 150)):
                        position = Vector(source['positions'][index])
                        rows = weights.get(index, [])
                        total = sum(weight for _, weight in rows)
                        expected = sum(((transforms[name] @ position) * weight for name, weight in rows), Vector()) / total if total else position
                        actual = evaluated.matrix_world @ mesh.vertices[index].co
                        metrics['maximumPosedVertexError'] = max(metrics['maximumPosedVertexError'], (actual - placement @ expected).length)
                        metrics['movedSamples'] += (expected - position).length > 0.001
                finally:
                    evaluated.to_mesh_clear()
    assert metrics['maximumPosedVertexError'] < 1e-4, metrics
    assert metrics['movedSamples'] > 0, metrics
finally:
    for bone, mode, basis in pose_state:
        bone.rotation_mode = mode
        bone.matrix_basis = basis
    rig.matrix_world = original_world
    for key, value in shape_state:
        key.value = value
    for obj, key, value in face_property_state:
        obj[key] = value
    for modifier, enabled in modifier_state:
        modifier.show_viewport = enabled
    bpy.context.view_layer.update()
print(json.dumps(dict(result='NATIVE_WORLD_BIND_AND_DEFORMATION_OK', **metrics, poseRestored=True)))
