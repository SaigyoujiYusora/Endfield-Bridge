"""Run in Blender with the frontend registered; independent lifecycle checks."""
import json
from pathlib import Path

import bpy
from endfield_bridge import face_controls
from endfield_bridge.scene import create_scene, remove_scene


def run(fixture_path):
    fixture = json.loads(Path(fixture_path).read_text(encoding='utf-8'))
    document = dict(fixture, name='Face lifecycle check', meshes=[], materials=[], clips=[])
    collections = []
    try:
        first_collection, first = create_scene(bpy.context, document, 'BASIC')
        collections.append(first_collection)
        second_collection, second = create_scene(bpy.context, document, 'BASIC')
        collections.append(second_collection)
        data = face_controls.descriptor(first[face_controls.DATA])
        assert len(data['controls']) == 245 and len(data['bones']) == 87
        bone = first.pose.bones[data['sceneBoneNames'][data['bones'][0]['sceneBone']]]
        existing = bone.driver_add('location', 0)
        existing.driver.expression = '0.125'
        try:
            face_controls.set_enabled(first, True)
        except ValueError:
            pass
        else:
            raise AssertionError('Existing driver was overwritten')
        assert existing.driver.expression == '0.125'
        bone.driver_remove('location', 0)
        before = {b.name: (b.rotation_mode, b.matrix_basis.copy()) for b in first.pose.bones}
        rest = {b.name: b.matrix_local.copy() for b in first.data.bones}
        second_before = {b.name: b.matrix_basis.copy() for b in second.pose.bones}
        face_controls.set_enabled(first, True)
        count = len(first.animation_data.drivers)
        assert count == 870
        face_controls.set_enabled(first, True)
        assert len(first.animation_data.drivers) == count
        control = next(i for i, c in enumerate(data['controls']) if c['bones'])
        first[face_controls.property_name(control)] = .5
        first.update_tag()
        bpy.context.view_layer.update()
        assert all(b.matrix_local == rest[b.name] for b in first.data.bones)
        assert all(b.matrix_basis == second_before[b.name] for b in second.pose.bones)
        assert all(curve.driver.is_valid for curve in first.animation_data.drivers), 'Scripted driver evaluation failed'
        face_controls.set_enabled(first, False)
        bpy.context.view_layer.update()
        assert not first.animation_data.drivers
        assert all(b.rotation_mode == before[b.name][0] and max(abs(x-y) for r,s in zip(b.matrix_basis,before[b.name][1]) for x,y in zip(r,s)) < 1e-6 for b in first.pose.bones)
        face_controls.set_enabled(first, True)
        renamed = bone.name + '_UserRename'
        first.data.bones[bone.name].name = renamed
        face_controls.invalidate(first)  # Exercise the same cold lookup used after open.
        face_controls.loaded(None)
        assert face_controls.rebind_owned_after_load() >= 870
        first.update_tag()
        bpy.context.view_layer.update()
        assert all(curve.driver.is_valid for curve in first.animation_data.drivers)
        face_controls.set_enabled(first, False)
        assert not first.animation_data.drivers, 'Renamed Face drivers were orphaned'
        assert renamed in first.data.bones, 'User bone rename was undone'
        bone = first.pose.bones[renamed]
        face_controls.set_enabled(first, True)
        bone.driver_remove('location', 0)
        replacement = bone.driver_add('location', 0)
        replacement.driver.expression = '0.25'
        bone.rotation_mode = 'ZYX'
        replacement_pointer = replacement.as_pointer()
        assert face_controls.rebind_owned_after_load() >= 869
        assert replacement.as_pointer() == replacement_pointer and replacement.driver.expression == '0.25'
        face_controls.set_enabled(first, False)
        assert len(first.animation_data.drivers) == 1
        remaining = first.animation_data.drivers[0]
        assert remaining.as_pointer() == replacement_pointer and remaining.driver.expression == '0.25'
        assert bone.rotation_mode == 'ZYX', 'Foreign driver bone mode was reset'
        bone.driver_remove('location', 0)
        print('FACE_LIFECYCLE_OK: conflicts, idempotence, rest preservation, independent instances, disable restore')
    finally:
        for collection in reversed(collections):
            remove_scene(bpy.context, collection)


if __name__ == '__main__':
    fixture = globals().get('FACE_FIXTURE')
    if not fixture:
        raise ValueError('Set FACE_FIXTURE to a Core-produced face/bone JSON fixture, or call run(path)')
    run(fixture)
