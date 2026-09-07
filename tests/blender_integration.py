import addon_utils
import json
from pathlib import Path
import sys

root = Path('F:/Games/Endfield-unpack/ENDF-DR')
addon_root = str(root / 'Endfield-Bridge')
if addon_root not in sys.path:
    sys.path.insert(0, addon_root)
addon_utils.enable('endfield_bridge', default_set=True, persistent=False)
import endfield_bridge
from endfield_bridge.client import request
from endfield_bridge.scene import create_scene, apply_clip

executable = str(root / 'Sora-Core/src/Sora.Cli/bin/Release/net10.0/Sora-Core.exe')
database = str(root / 'Sora-Core/artifacts/fixtures/fixture.sredb')
bpy.context.preferences.addons['endfield_bridge'].preferences.executable = executable
original_scene = bpy.context.window.scene
test_scene = bpy.data.scenes.new('ENDF2Blend verification')
bpy.context.window.scene = test_scene
test_scene['sora_original_scene'] = original_scene.name
document = request(executable, 'scene', path=database, asset='character:sample')
collection, rig = create_scene(bpy.context, document)
assert len(collection.objects) == 2
mesh = next(obj for obj in collection.objects if obj.type == 'MESH')
assert len(mesh.data.vertices) == 3 and len(rig.data.bones) == 1
assert mesh.modifiers[0].object == rig
assert mesh.data.has_custom_normals
face_key = next(key for key in mesh.keys() if key.startswith('face_'))
mesh[face_key] = 0.75
mesh.update_tag()
bpy.context.view_layer.update()
assert abs(mesh.data.shape_keys.key_blocks['Smile'].value - 0.75) < 1e-6
collection2, rig2 = create_scene(bpy.context, document)
mesh2 = next(obj for obj in collection2.objects if obj.type == 'MESH')
assert mesh2.data != mesh.data and rig2.data != rig.data
assert mesh2.data.shape_keys.key_blocks['Smile'].value == 0
action = apply_clip(bpy.context, rig, document['clips'][0], ['Root'])
bpy.context.scene.frame_set(31)
assert abs(rig.pose.bones['Root'].location.z - 1) < 1e-6
assert rig2.animation_data is None
for obj in collection2.objects:
    obj.hide_set(True)
for obj in collection.objects:
    obj['sora_database'] = database
    obj['sora_asset'] = 'character:sample'
    obj.select_set(False)
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.context.scene.sora.database = database
bpy.context.scene.sora.clip = 'Wave'
assert bpy.ops.sora.search() == {'FINISHED'}
assert len(bpy.context.scene.sora.assets) == 1
assert bpy.ops.sora.check() == {'FINISHED'}
test_scene.frame_set(1)
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
        area.spaces.active.region_3d.view_distance = 6
        area.spaces.active.region_3d.view_location = (0, 0, 1)
        from mathutils import Quaternion
        area.spaces.active.region_3d.view_rotation = Quaternion((1, 0, 0), 1.57079632679)
        area.spaces.active.shading.type = 'SOLID'
        area.spaces.active.shading.color_type = 'MATERIAL'
print(json.dumps({'result': 'BLENDER_BRIDGE_INTEGRATION_OK', 'blender': bpy.app.version_string, 'scene': test_scene.name, 'instanceIsolation': True, 'faceDriver': True, 'animationEndZ': 1, 'databaseSearch': True, 'actions': action.name}))
