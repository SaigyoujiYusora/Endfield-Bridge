"""Run inside the parent Blender session to exercise hierarchy reordering."""
import bpy
from endfield_bridge.scene import create_scene, remove_scene


def run():
    rows = [('root', -1, ''), ('branch_A', 0, 'branch_A'),
            ('branch_B', 0, 'branch_B'), ('late_child_A', 1, 'branch_A/late_child_A')]
    bones = [{'name': name, 'parent': parent, 'sourcePath': path, 'sourceHash': 100+i,
              'head': [i, 0, 0], 'tail': [i, 1, 0]} for i, (name, parent, path) in enumerate(rows)]
    collection, rig = create_scene(bpy.context, {'name': 'Hierarchy identity regression',
        'bones': bones, 'meshes': [], 'materials': [], 'clips': []}, 'BASIC')
    try:
        actual_order = [bone.name for bone in rig.data.bones]
        for source in bones:
            bone = rig.data.bones[source['name']]
            assert bone['sora_source_path'] == source['sourcePath']
            assert int(bone['sora_source_hash']) == source['sourceHash']
        assert actual_order != [source['name'] for source in bones], 'Fixture did not exercise hierarchy reordering'
        print('BONE_SOURCE_IDENTITY_REORDER_OK', actual_order)
    finally:
        remove_scene(bpy.context, collection)
