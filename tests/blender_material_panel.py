"""MCP: load definitions, then exercise(material) on an imported test material.

Runs actual registered operators with EXEC_DEFAULT. Restores shader values and
snapshots in finally; no render, save or context selection changes.
"""
import math
import bpy
from endfield_bridge import material_panel, cycles_uniforms


def audit(material, stack):
    count = 0
    for node in material.node_tree.nodes:
        name = node.get(cycles_uniforms.ROW)
        if name is None:
            continue
        expected = stack._param_read(material, name)
        kind = node[cycles_uniforms.KIND]
        actual = ([float(s.default_value) for s in list(node.inputs)[:3]]
                  if kind == 'XYZ' else [float(node.outputs[0].default_value)])
        expected = expected[:3] if kind == 'XYZ' else [expected[3]] if kind == 'W' else [expected]
        assert all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6) for a, b in zip(actual, expected)), (name, actual, expected)
        count += 1
    assert count, 'Use a Cycles validation material for GPU uniform verification'
    return count


def exercise(material):
    stack = material_panel.stack_for(material)
    rows = [r for g in material_panel.groups_for(stack, material) for r in g['rows']]
    compiled = {r[0] for r in stack.part(material['ruri_uber_part'])['params']}
    assert compiled.issubset({r['name'] for r in rows}), 'Native parameters are not discoverable'
    bound = {n.get(cycles_uniforms.ROW) for n in material.node_tree.nodes}
    scalar = next(r for r in rows if r['kind'] in {'VALUE', 'SLIDER'} and r['name'] in bound)
    color = next(r for r in rows if r['kind'] in {'HDRCOLOR', 'COLOR'} and r['name'] in bound)
    before = stack.panel_read(material)
    texture = next(r for r in rows if r['kind'] == 'TEXTURE' and r.get('has_st') and before['images'].get(r['name']))
    image = before['images'][texture['name']]
    image_copy = image.copy()
    image_copy.name = 'ENDF UI Test Temporary Texture'
    color_space = image.colorspace_settings.name
    keys = ('ruri_uber_floats', 'ruri_uber_colors', 'ruri_uber_images', 'ruri_uber_st')
    snapshots = {k: material[k].to_dict() if k in material else None for k in keys}
    old_scalar = material_panel.value_of(stack, material, scalar, before)
    old_color = material_panel.value_of(stack, material, color, before)
    old_st = before['st'].get(texture['name'], (1, 1, 0, 0))
    others = {m.name: repr({k: m[k].to_dict() if k in m else None for k in keys})
              for m in bpy.data.materials if m != material}
    try:
        assert bpy.ops.endf.npr_parameter('EXEC_DEFAULT', material_name=material.name,
            parameter=scalar['name'], scalar=0.375) == {'FINISHED'}
        assert math.isclose(stack._param_read(material, scalar['name']), 0.375)
        audit(material, stack)
        assert bpy.ops.endf.npr_parameter('EXEC_DEFAULT', material_name=material.name,
            parameter=color['name'], vector=(3.5, -0.25, 0.125, 0.625)) == {'FINISHED'}
        assert list(stack._param_read(material, color['name'])) == [3.5, -0.25, 0.125, 0.625]
        audit(material, stack)
        assert bpy.ops.endf.npr_parameter('EXEC_DEFAULT', material_name=material.name,
            parameter=texture['name'], image_name=image_copy.name,
            tiling=(1.5, 0.75), offset=(-0.25, 0.125)) == {'FINISHED'}
        after = stack.panel_read(material)
        assert after['images'][texture['name']] == image_copy
        assert tuple(after['st'][texture['name']]) == (1.5, 0.75, -0.25, 0.125)
        assert any(n.type == 'TEX_IMAGE' and n.image == image_copy for n in material.node_tree.nodes), 'Texture GPU node was not updated'
        audit(material, stack)
        for name, expected in others.items():
            other = bpy.data.materials[name]
            assert repr({k: other[k].to_dict() if k in other else None for k in keys}) == expected, ('Other material changed', name)
        return {'status': 'ENDF_NPR_UI_OPERATORS_OK', 'float': scalar['name'],
                'hdr': color['name'], 'texture_st': texture['name'],
                'discoverable_rows': len(rows), 'gpu_uniforms': audit(material, stack)}
    finally:
        stack.panel_write(material, scalar, old_scalar)
        stack.panel_write(material, color, old_color)
        stack.panel_write_image(material, texture, image)
        stack.panel_write_st(material, texture, old_st[:2], old_st[2:])
        image.colorspace_settings.name = color_space
        for key, value in snapshots.items():
            if value is None:
                if key in material:
                    del material[key]
            else:
                material[key] = value
        stack._param_write(material)
        stack._param_flush()
        if image_copy.users == 0:
            bpy.data.images.remove(image_copy)
