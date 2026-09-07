import importlib
import json
from pathlib import Path
from mathutils import Matrix, Quaternion, Vector
import endfield_bridge.scene
importlib.reload(endfield_bridge.scene)
from endfield_bridge.scene import create_scene
from endfield_bridge.client import request

root = Path('F:/Games/Endfield-unpack/ENDF-DR')
executable = str(root / 'Sora-Core/src/Sora.Cli/bin/Release/net10.0/Sora-Core.exe')
database = str(root / 'Sora-Core/artifacts/azrila-native.sredb')
identity = 'assets/beyond/dynamicassets/gameplay/prefabs/uimodels/chr_0009_azrila_uimodel.prefab'
document = request(executable, 'scene', path=database, asset=identity)
scene = bpy.data.scenes.new('ENDF2Blend bind verification')
scene['sora_original_scene'] = 'Scene'
bpy.context.window.scene = scene
collection, rig = create_scene(bpy.context, document)
rest = {}
maximum_rest_error = 0.0
worst_bone = ''
for source in document['bones']:
    values = source['restMatrix']
    matrix = Matrix([values[row * 4:row * 4 + 4] for row in range(4)])
    rest[source['name']] = matrix
    actual = rig.data.bones[source['name']].matrix_local
    error = max(abs(actual[i][j] - matrix[i][j]) for i in range(4) for j in range(4))
    if error > maximum_rest_error:
        maximum_rest_error, worst_bone = error, source['name']
print(json.dumps({'restError': maximum_rest_error, 'worstBone': worst_bone}))
joint = rig.pose.bones['Bip001_R_UpperArm']
before = joint.matrix_basis.copy()
joint.rotation_mode = 'QUATERNION'
joint.rotation_quaternion = Quaternion((0, 1, 0), 0.45)
bpy.context.view_layer.update()
transforms = {name: rig.pose.bones[name].matrix @ matrix.inverted() for name, matrix in rest.items()}
maximum_skin_error = 0.0
moved = 0
for obj in collection.objects:
    if obj.type != 'MESH':
        continue
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        for index in range(0, len(obj.data.vertices), max(1, len(obj.data.vertices) // 80)):
            vertex = obj.data.vertices[index]
            expected = Vector((0, 0, 0))
            for weight in vertex.groups:
                name = obj.vertex_groups[weight.group].name
                expected += (transforms[name] @ vertex.co) * weight.weight
            maximum_skin_error = max(maximum_skin_error, (expected - mesh.vertices[index].co).length)
            moved += (expected - vertex.co).length > 0.001
    finally:
        evaluated.to_mesh_clear()
joint.matrix_basis = before
bpy.context.view_layer.update()
assert maximum_skin_error < 1e-4, maximum_skin_error
assert maximum_rest_error < 1e-4, {'error': maximum_rest_error, 'bone': worst_bone, 'posedVertexError': maximum_skin_error}
assert moved > 0, 'No sampled vertices moved'
rig.hide_set(True)
for obj in collection.objects:
    obj['sora_asset'] = identity
    obj['sora_database'] = database
scene.sora.database = database
print(json.dumps({'result': 'NATIVE_BIND_AND_DEFORMATION_OK', 'maximumRestMatrixError': maximum_rest_error, 'maximumPosedVertexError': maximum_skin_error, 'movedSamples': moved, 'poseRestored': True}))
