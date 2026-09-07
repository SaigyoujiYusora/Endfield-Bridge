import json
from pathlib import Path
from mathutils import Quaternion
from endfield_bridge.client import request
from endfield_bridge.scene import create_scene

root = Path('F:/Games/Endfield-unpack/ENDF-DR')
exe = str(root / 'Sora-Core/src/Sora.Cli/bin/Release/net10.0/Sora-Core.exe')
database = str(root / 'Sora-Core/artifacts/azrila-geometry.sredb')
document = request(exe, 'scene', path=database, asset='character:azrila')
scene = bpy.data.scenes.new('ENDF2Blend native geometry')
scene['sora_original_scene'] = 'Scene'
bpy.context.window.scene = scene
collection, rig = create_scene(bpy.context, document)
assert len(rig.data.bones) == 411
meshes = [obj for obj in collection.objects if obj.type == 'MESH']
assert len(meshes) == 12
for obj in meshes:
    assert obj.data.has_custom_normals
    assert obj.modifiers[0].object == rig
    assert all(abs(sum(group.weight for group in vertex.groups) - 1) < 1e-5 for vertex in obj.data.vertices)
    obj['sora_database'] = database
    obj['sora_asset'] = 'character:azrila'
rig['sora_database'] = database
rig['sora_asset'] = 'character:azrila'
rig.hide_set(True)
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        space = area.spaces.active
        space.region_3d.view_location = (0, 0, 0.9)
        space.region_3d.view_distance = 3.8
        space.region_3d.view_rotation = Quaternion((1, 0, 0), 1.57079632679)
        space.shading.type = 'SOLID'
        space.shading.color_type = 'MATERIAL'
scene.sora.database = database
print(json.dumps({'result': 'NATIVE_CHARACTER_GEOMETRY_IMPORTED', 'meshes': len(meshes), 'bones': len(rig.data.bones), 'vertices': sum(len(obj.data.vertices) for obj in meshes), 'triangles': sum(len(obj.data.polygons) for obj in meshes), 'normalAndWeightChecks': True, 'materialsAndAnimation': 'not included'}))
