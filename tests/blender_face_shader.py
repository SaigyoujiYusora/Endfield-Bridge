"""Parent-run live checks on two disposable, separately imported NPR rigs."""
from endfield_bridge import face_shader, face_controls
from endfield_bridge.cycles_uniforms import ROW, KIND
import bpy
import json


def read(rig):
    return {(mat.name, row['name']): face_shader._read(mat, stack, row)
            for mat, stack, row, _ in face_shader.targets(rig, face_shader.mappings(rig))}


def run(rig, other):
    original_data = rig[face_controls.DATA]
    altered = json.loads(original_data)
    next(control['shader'] for control in altered['controls'] if control.get('shader'))['blendMode'] = 99
    rig[face_controls.DATA] = json.dumps(altered)
    try:
        try:
            face_shader.mappings(rig)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsupported shader mode was silently accepted')
    finally:
        rig[face_controls.DATA] = original_data
    before = read(rig)
    other_before = read(other)
    props = {face_controls.property_name(i): rig[face_controls.property_name(i)] for i, _ in face_shader.mappings(rig)}
    try:
        face_shader.set_enabled(rig, True)
        for index, name in face_shader.mappings(rig):
            rig[face_controls.property_name(index)] = .375 if name == '_EmotionBlend' else 2.0
        assert face_shader.sync(rig) > 0
        assert face_shader.sync(rig) == 0
        for _ in range(5):
            bpy.context.view_layer.update()
            assert face_shader.sync(rig) == 0
        assert read(other) == other_before, 'Other import material changed'
        cycles_nodes = 0
        for material, stack, row, _ in face_shader.targets(rig, face_shader.mappings(rig)):
            expected = .375 if row['name'] == '_EmotionBlend' else 2.0
            assert abs(face_shader._read(material, stack, row) - expected) < 1e-6
            for node in material.node_tree.nodes:
                if node.get(ROW) == row['name'] and node.get(KIND) == 'F':
                    assert abs(node.outputs[0].default_value - expected) < 1e-6
                    cycles_nodes += 1
        if bpy.context.scene.render.engine == 'CYCLES':
            assert cycles_nodes >= 2, 'Cycles scalar projections were not present'
        face_shader.set_enabled(rig, False)
        assert read(rig) == before, 'Disable failed to restore prior material values'
        assert read(other) == other_before
        print('FACE_SHADER_OK', {'targets': len(before), 'cyclesNodes': cycles_nodes})
    finally:
        face_shader.set_enabled(rig, False)
        for prop, value in props.items():
            rig[prop] = value


def after_reopen(rig):
    """Parent saves/opens a disposable file with both face layers enabled first."""
    assert rig.get(face_controls.ENABLED)
    assert rig.get(face_shader.ENABLED)
    assert bpy.app.driver_namespace.get('sora_f') is face_controls.component
    bpy.context.view_layer.update()
    assert len(rig.animation_data.drivers) == 870
    assert all(curve.driver.is_valid for curve in rig.animation_data.drivers)
    face_shader.sync(rig)
    assert face_shader.sync(rig) == 0
    assert all(face_shader.SNAPSHOT in mat for mat, _, _, _ in face_shader.targets(rig, face_shader.mappings(rig)))
    print('FACE_REOPEN_OK: registered drivers, owned shader snapshots, no unchanged writes')


def rename_before_save(rig):
    """Parent calls on its disposable enabled fixture before saving/opening."""
    data = face_controls.descriptor(rig[face_controls.DATA])
    bone = face_controls._native_bones(rig, data)[0]
    name = bone.name + '_SavedRename'
    bone.name = name
    rig['sora_test_renamed_bone'] = name
    rig.update_tag()
    return name


def after_reopen_renamed(rig):
    """Parent invokes after the one-shot load repair completes."""
    name = rig['sora_test_renamed_bone']
    assert name in rig.data.bones
    bpy.context.view_layer.update()
    assert all(curve.driver.is_valid for curve in rig.animation_data.drivers)
    face_controls.set_enabled(rig, False)
    assert not rig.animation_data.drivers, 'Renamed owned drivers survived disable'
    assert name in rig.data.bones, 'Saved user rename was reverted'
    print('FACE_RENAMED_REOPEN_OK: cold source-path lookup and complete owned cleanup')
