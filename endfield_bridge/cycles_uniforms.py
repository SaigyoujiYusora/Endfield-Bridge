"""Cycles material uniforms, projected from the runtime's authoritative CPU rows.

Only material parameter inputs that assembly has left unlinked are bound here.
Lighting, textures, geometry and other varying paths remain in the shader graph.
Combine XYZ deliberately avoids color conversion/clamping for signed/HDR vectors.
"""

ROW = 'endf_npr_uniform_row'
KIND = 'endf_npr_uniform_kind'
REVISION = 1


def wire(stack, g, insts, part):
    """Create copyable template bindings; actual material values arrive on write."""
    defaults = stack._mat_defaults(part)
    for name, kind, texel, comp, _dv, _dw in stack.part(part)['params']:
        sockets = [inst.inputs.get(name) for inst in insts]
        sockets = [sock for sock in sockets if sock is not None and not sock.is_linked]
        tails = ([inst.inputs.get(name + '_w') for inst in insts] if kind == 'V4' else [])
        tails = [sock for sock in tails if sock is not None and not sock.is_linked]
        if sockets:
            node = g._nd('ShaderNodeValue' if kind == 'F' else 'ShaderNodeCombineXYZ')
            node[ROW], node[KIND] = name, 'F' if kind == 'F' else 'XYZ'
            node.label = 'ENDF Cycles Uniform ' + name
            _assign(node, defaults[texel], comp)
            for sock in sockets:
                g._set(sock, node.outputs[0])
        if tails:
            node = g._nd('ShaderNodeValue')
            node[ROW], node[KIND] = name, 'W'
            node.label = 'ENDF Cycles Uniform ' + name + '_w'
            _assign(node, defaults[texel], 3)
            for sock in tails:
                g._set(sock, node.outputs[0])


def _assign(node, cell, component):
    sockets = list(node.inputs)[:3] if node[KIND] == 'XYZ' else [node.outputs[0]]
    values = cell[:3] if node[KIND] == 'XYZ' else [cell[component]]
    changed = False
    for socket, value in zip(sockets, values):
        value = float(value)
        if socket.default_value != value:
            socket.default_value = value
            changed = True
    return changed


def sync(stack, material, column):
    """Update this material's marked nodes only; unchanged writes do not tag it."""
    tree = material.node_tree
    if tree is None:
        return False
    rows = {row[0]: row for row in stack.part(material['ruri_uber_part'])['params']}
    mirror = stack._mat_mirror()
    changed = False
    for node in tree.nodes:
        name = node.get(ROW)
        if name not in rows:
            continue
        _, _kind, texel, component, _dv, _dw = rows[name]
        if node.get(KIND) == 'W':
            component = 3
        changed = _assign(node, mirror[texel, int(column)], component) or changed
    if changed:
        tree.update_tag()
        material.update_tag()
    return changed
