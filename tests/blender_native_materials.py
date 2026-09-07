import importlib
import json
from pathlib import Path
from mathutils import Quaternion
import endfield_bridge.materials
import endfield_bridge.scene
importlib.reload(endfield_bridge.materials)
importlib.reload(endfield_bridge.scene)
from endfield_bridge.scene import create_scene
from endfield_bridge.client import request

root = Path('F:/Games/Endfield-unpack/ENDF-DR')
exe = str(root / 'Sora-Core/src/Sora.Cli/bin/Release/net10.0/Sora-Core.exe')
database = str(root / 'Sora-Core/artifacts/azrila-native.sredb')
identity = 'assets/beyond/dynamicassets/gameplay/prefabs/uimodels/chr_0009_azrila_uimodel.prefab'
document = request(exe, 'scene', path=database, asset=identity)
scene = bpy.data.scenes.new('ENDF2Blend native materials')
scene['sora_original_scene'] = 'Scene'
bpy.context.window.scene = scene
collection, rig = create_scene(bpy.context, document)
meshes = [obj for obj in collection.objects if obj.type == 'MESH']
assert len(meshes) == 11
token = collection['sora_instance']
images = [image for image in bpy.data.images if image.get('sora_instance') == token]
assert len(images) == 14 and all(image.packed_file is not None for image in images)
assert all(len(obj.data.materials) > 0 for obj in meshes)
for obj in collection.objects:
    obj['sora_database'] = database
    obj['sora_asset'] = identity
rig.hide_set(True)
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        space = area.spaces.active
        space.region_3d.view_location = (0, 0, 0.9)
        space.region_3d.view_distance = 3.8
        space.region_3d.view_rotation = Quaternion((1, 0, 0), 1.57079632679)
        space.shading.type = 'MATERIAL'
scene.sora.database = database
print(json.dumps({'result': 'NATIVE_CHARACTER_MATERIALS_IMPORTED', 'meshes': len(meshes), 'bones': len(rig.data.bones), 'packedImages': len(images), 'nativeMaterialRecords': len(document['materials']), 'blenderMaterials': len({mat.name for obj in meshes for mat in obj.data.materials}), 'animationAndFacePresets': 'not included'}))
