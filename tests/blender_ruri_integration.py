"""Visible Blender MCP fixture; set RURI_DOCUMENT to the real scene DTO first.

exec(compile(Path(this_file).read_text(), this_file, 'exec'))
Keeps the resulting import visible for comparison. Does not save the scene.
"""
import bpy
from endfield_bridge.scene import create_scene
from endfield_bridge import ruri_adapter
from endfield_bridge.vendor.ruri_npr import ruri_endfield as runtime

assert 'RURI_DOCUMENT' in globals(), 'Provide the real Azrila scene DTO in RURI_DOCUMENT'
collection = globals().get('RURI_COLLECTION')
if collection is None:
    collection, rig = create_scene(bpy.context, RURI_DOCUMENT, material_mode='RURI')
materials = {m for obj in collection.objects if obj.type == 'MESH' for m in obj.data.materials if m}
claimed = [m for m in materials if m.get('ruri_uber_part')]
assert claimed, 'No original Ruri shader claimed the fixture'
columns = [(m['ruri_uber_stack'], m['ruri_param_col']) for m in claimed]
if columns:
    assert len(columns) == len(set(columns)), 'Parameter column collision'
parts = sorted({m['ruri_uber_part'] for m in claimed})
assert {'Face', 'Hair', 'Eyes'}.issubset(parts), parts
for material in claimed:
    descriptor = __import__('json').loads(material['sora_material_descriptor'])
    for name, rgba in descriptor['colors'].items():
        actual = material['ruri_uber_colors'][name]
        assert max(abs(a - b) for a, b in zip(actual, rgba)) < 1e-5, (material.name, name)
    for name, binding in descriptor['textures'].items():
        st = list(binding['scale']) + list(binding['offset'])
        assert max(abs(a - b) for a, b in zip(material['ruri_uber_st'][name], st)) < 1e-5
    assert not any(n.type == 'BSDF_PRINCIPLED' for n in material.node_tree.nodes)
    base_name = (material.get('ruri_uber_images') or {}).get('_BaseMap')
    if base_name:
        base_image = bpy.data.images[base_name]
        expected_alpha = 'STRAIGHT' if material.get('endf_npr_transparent_base') else 'CHANNEL_PACKED'
        assert base_image.alpha_mode == expected_alpha, (material.name, base_image.name, base_image.alpha_mode)
    stack = next(item for item in ruri_adapter.stacks() if item.PANEL_KEY == material['ruri_uber_stack'])
    assert stack.host['registry_module'] == ruri_adapter.__name__
    assert stack.host['rig_identity_module'] == ruri_adapter.__name__
mesh_objects = [obj for obj in collection.objects if obj.type == 'MESH']
assert len(mesh_objects) == len(RURI_DOCUMENT['meshes']), 'Extra or missing source mesh objects'
for obj in mesh_objects:
    assert 'Color' in obj.data.color_attributes
    source = next((item for item in RURI_DOCUMENT['meshes']
                   if str(item.get('sourceId')) == obj.get('sora_source_id')), None)
    if source is None:
        source = next((item for item in RURI_DOCUMENT['meshes']
                       if obj.name == item['name'] or obj.name.startswith(item['name'] + '.')), None)
    assert source is not None, obj.name
    assert len(obj.data.vertices) == len(source['positions']), obj.name
    if source.get('materialSlots'):
        assert len(obj.data.materials) == len(source['materialSlots']), (obj.name, 'source material slots changed')
    if source is not None and not source.get('colors'):
        assert all(max(abs(value - 1) for value in item.color) < 1e-6 for item in obj.data.color_attributes['Color'].data)
    assert 'ruri_tangent' in obj.data.attributes
    assert 'ruri_tangent_sign' in obj.data.attributes
    if source.get('tangents'):
        tangent = obj.data.attributes['ruri_tangent']
        sign = obj.data.attributes['ruri_tangent_sign']
        for loop in obj.data.loops:
            native = source['tangents'][loop.vertex_index]
            expected = (-native[0], -native[1], native[2])
            assert max(abs(a - b) for a, b in zip(tangent.data[loop.index].vector, expected)) < 1e-5
            assert abs(sign.data[loop.index].value + native[3]) < 1e-5
face_objects = [obj for obj in mesh_objects if any(m and m.get('ruri_uber_part') == 'Face' for m in obj.data.materials)]
assert face_objects and all(obj.name in runtime.RIG_DRIVEN for obj in face_objects)
assert all(runtime.RIG_OBJECT_PROP + '0' in obj for obj in face_objects)
outline_objects = [obj for obj in mesh_objects if any(mod.type == 'NODES' and mod.node_group and mod.node_group.get('ruri_outline_runtime_revision') for mod in obj.modifiers)]
assert outline_objects, 'Expected original outline geometry stages'
print('RURI_INTEGRATION_OK', dict(collection=collection.name, materials=len(claimed), parts=parts,
      faces=len(face_objects), outline_meshes=len(outline_objects)))
