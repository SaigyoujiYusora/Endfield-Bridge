import importlib
import json
import math
from pathlib import Path
from unittest.mock import patch

import endfield_bridge
endfield_bridge.unregister()
importlib.reload(endfield_bridge.scene)
importlib.reload(endfield_bridge)
endfield_bridge.register()
from endfield_bridge.scene import create_scene, apply_clip
from mathutils import Matrix, Quaternion, Vector

root = Path('F:/Games/Endfield-unpack/ENDF-DR')
document = json.loads((root / 'Sora-Core/artifacts/fixtures/fixture.json').read_text())['assets'][0]['scene']
document['bones'] = [
    {'name': 'Root', 'parent': -1, 'head': [0, 0, 0], 'tail': [0, 0, 1], 'roll': math.pi / 4},
    {'name': 'Child', 'parent': 0, 'head': [0, 0, 1], 'tail': [1, 0, 1], 'roll': math.pi / 2},
]
clip = {'name': 'Rotated child', 'duration': 1, 'fps': 24, 'tracks': [
    {'bone': 1, 'channel': 'location', 'keys': [{'time': 0, 'value': [0, 0, 0]}, {'time': 1, 'value': [0.3, 0.2, 0.1]}]},
    {'bone': 1, 'channel': 'rotation', 'keys': [{'time': 0, 'value': [0, 0, 0, 1]}, {'time': 1, 'value': [0, math.sin(0.25), 0, math.cos(0.25)]}]},
]}
collection, rig = create_scene(bpy.context, document)
names = ['Root', 'Child']
root_rest = Matrix.Rotation(math.pi / 2, 4, 'X') @ Matrix.Rotation(math.pi / 4, 4, 'Y')
child_rest = Matrix.Translation((0, 0, 1)) @ Matrix.Rotation(-math.pi / 2, 4, 'Z') @ Matrix.Rotation(math.pi / 2, 4, 'Y')
def close_matrix(left, right):
    return max(abs(left[i][j] - right[i][j]) for i in range(4) for j in range(4)) < 1e-5
assert close_matrix(rig.data.bones['Root'].matrix_local, root_rest)
assert close_matrix(rig.data.bones['Child'].matrix_local, child_rest)
action = apply_clip(bpy.context, rig, clip, names)
bpy.context.scene.frame_set(25)
expected = child_rest @ Matrix.LocRotScale(Vector((0.3, 0.2, 0.1)), Quaternion((0, 1, 0), 0.5), Vector((1, 1, 1)))
assert close_matrix(rig.pose.bones['Child'].matrix, expected)
timing = lambda: (bpy.context.scene.render.fps, bpy.context.scene.render.fps_base, bpy.context.scene.frame_start, bpy.context.scene.frame_end, bpy.context.scene.frame_current)
before_timing = timing()
before_matrix = rig.pose.bones['Child'].matrix_basis.copy()
before_actions = set(bpy.data.actions.keys())
try:
    with patch.object(endfield_bridge.scene.math, 'ceil', side_effect=RuntimeError('forced late failure')):
        apply_clip(bpy.context, rig, clip, names)
except RuntimeError as error:
    assert str(error) == 'forced late failure'
else:
    raise AssertionError('Expected injected rollback failure')
assert rig.animation_data.action == action and timing() == before_timing
assert close_matrix(rig.pose.bones['Child'].matrix_basis, before_matrix)
assert set(bpy.data.actions.keys()) == before_actions
before = (len(bpy.data.collections), len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.armatures), len(bpy.data.materials))
bad = dict(document, meshes=[dict(document['meshes'][0], material=999)])
try:
    create_scene(bpy.context, bad)
except IndexError:
    pass
else:
    raise AssertionError('Expected invalid material reference')
after = (len(bpy.data.collections), len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.armatures), len(bpy.data.materials))
assert before == after
for selected in bpy.context.selected_objects:
    selected.select_set(False)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
rig['sora_asset'] = 'fixture'
bpy.ops.object.mode_set(mode='EDIT')
assert not endfield_bridge.SORA_OT_clip.poll(bpy.context)
try:
    apply_clip(bpy.context, rig, clip, names)
except ValueError:
    pass
else:
    raise AssertionError('Edit-mode animation unexpectedly allowed')
bpy.ops.object.mode_set(mode='OBJECT')
settings = bpy.context.scene.sora
settings.database = str(root / 'Sora-Core/artifacts/fixtures/fixture.sredb')
settings.result_database = settings.database
settings.assets.clear()
settings.selected = 0
row = settings.assets.add()
row.name = 'Metadata-only bundle'
row.has_scene = False
assert not endfield_bridge.SORA_OT_import.poll(bpy.context)
row.has_scene = True
assert endfield_bridge.SORA_OT_import.poll(bpy.context)
settings.database += '.changed'
assert not endfield_bridge.SORA_OT_import.poll(bpy.context)
print(json.dumps({'result': 'BLENDER_ROLLBACK_AND_ROLL_OK', 'rolledRootAndChild': True, 'rotationAndTranslation': True, 'lateFailureRollback': True, 'importFailureCleanup': True, 'editModeGuard': True, 'metadataImportGuard': True, 'databaseIdentityGuard': True}))
