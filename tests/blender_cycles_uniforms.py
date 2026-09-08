"""MCP helpers: execute definitions, then audit_all() or exercise(material).

No rendering, saving or engine changes. exercise restores edited snapshots and
replays writes in finally. Run against disposable imported validation materials.
"""
import bpy
from endfield_bridge import ruri_adapter, cycles_uniforms


def stack_for(material):
    return next(s for s in ruri_adapter.stacks()
                if s.PANEL_KEY == material.get('ruri_uber_stack'))


def audit(material):
    stack = stack_for(material)
    count = 0
    for node in material.node_tree.nodes:
        name = node.get(cycles_uniforms.ROW)
        if name is None:
            continue
        value = stack._param_read(material, name)
        kind = node[cycles_uniforms.KIND]
        actual = ([float(s.default_value) for s in list(node.inputs)[:3]]
                  if kind == 'XYZ' else float(node.outputs[0].default_value))
        expected = value[:3] if kind == 'XYZ' else value[3] if kind == 'W' else value
        assert actual == expected, (material.name, name, kind, actual, expected)
        count += 1
    assert count, ('No Cycles uniform bindings', material.name)
    return {'material': material.name, 'bindings': count}


def audit_all():
    return [audit(m) for m in bpy.data.materials
            if m.get('sora_instance') and m.node_tree
            and any(n.get(cycles_uniforms.ROW) for n in m.node_tree.nodes)]


def exercise(material):
    stack = stack_for(material)
    rows = stack.part(material['ruri_uber_part'])['params']
    bound = {n.get(cycles_uniforms.ROW) for n in material.node_tree.nodes}
    float_row = next(r for r in rows if r[1] == 'F' and r[0] in bound)
    color_row = next(r for r in rows if r[1] == 'V4' and r[0] in bound
                     and not r[0].startswith('__size') and not r[0].endswith('_ST'))
    st_row = next((r for r in rows if r[0].endswith('_ST') and r[0] in bound), None)
    keys = ('ruri_uber_floats', 'ruri_uber_colors', 'ruri_uber_st')
    snapshots = {k: material[k].to_dict() if k in material else None for k in keys}
    old_float = stack._param_read(material, float_row[0])
    old_color = stack._param_read(material, color_row[0])
    others = {m.name: [(n.name, tuple(s.default_value for s in n.inputs)
                       if n.get(cycles_uniforms.KIND) == 'XYZ' else n.outputs[0].default_value)
                      for n in m.node_tree.nodes if n.get(cycles_uniforms.ROW)]
              for m in bpy.data.materials if m != material and m.node_tree}
    try:
        stack.panel_write(material, {'name': float_row[0], 'kind': 'VALUE'}, 0.375)
        audit(material)
        stack.panel_write(material, {'name': color_row[0], 'kind': 'COLOR', 'size': 4},
                          [3.5, -0.25, 0.125, 0.625])
        audit(material)
        if st_row:
            values = dict(material.get('ruri_uber_st') or {})
            values[st_row[0][:-3]] = [1.5, 0.75, -0.25, 0.125]
            material['ruri_uber_st'] = values
            stack._param_write(material)
            audit(material)
        assert not cycles_uniforms.sync(stack, material, material['ruri_param_col'])
        for name, expected in others.items():
            tree = bpy.data.materials[name].node_tree
            actual = [(n.name, tuple(s.default_value for s in n.inputs)
                       if n.get(cycles_uniforms.KIND) == 'XYZ' else n.outputs[0].default_value)
                      for n in tree.nodes if n.get(cycles_uniforms.ROW)]
            assert actual == expected, ('Other instance changed', name)
        return {'float': float_row[0], 'hdr_color': color_row[0],
                'st': st_row[0] if st_row else None, 'other_materials': len(others)}
    finally:
        # Panel writes also project to vertex clones; restore those projections.
        stack.panel_write(material, {'name': float_row[0], 'kind': 'VALUE'}, old_float)
        stack.panel_write(material, {'name': color_row[0], 'kind': 'COLOR', 'size': 4}, old_color)
        for key, value in snapshots.items():
            if value is None:
                if key in material:
                    del material[key]
            else:
                material[key] = value
        stack._param_write(material)
        stack._param_flush()
