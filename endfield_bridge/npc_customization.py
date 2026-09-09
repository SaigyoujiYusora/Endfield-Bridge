"""Native _CUSTOMIZE_AVATAR branch for the AGPL NPR frontend.

See vendor/ruri_npr/LICENSE.txt. No texture pixels or shared vendor groups
are changed. The native branch consumes BaseMap * _BaseColor before lighting.
"""
import math
import struct
import uuid

import bpy

STAMP = 'native-customize-avatar-v1'
ENABLE = '_AvatarCustomizeEnable'
COLORS = ('_CustomizeBaseColor', '_CustomizeBaseTintColor', '_CustomizeAddTintColor')
MARKER = 'endf_npc_customization'


def validate_transparent_graph(material):
    """Validate the flat BaseMap graph before excluding Standard s1 consumers."""
    nodes = list(material.node_tree.nodes) if material.node_tree else []
    kinds = {node.type for node in nodes}
    if ('GROUP' in kinds
            or not {'BSDF_TRANSPARENT', 'EMISSION', 'MIX_SHADER', 'TEX_IMAGE'}.issubset(kinds)):
        raise RuntimeError('ENDF NPR-Shader transparent BaseMap graph contract changed: ' + material.name)
    products = [node for node in nodes if node.bl_idname == 'ShaderNodeMixRGB'
        and node.blend_type == 'MULTIPLY' and not node.use_clamp
        and not node.inputs[0].is_linked and node.inputs[0].default_value == 1.0
        and len(node.inputs[1].links) == 1
        and node.inputs[1].links[0].from_node.type == 'TEX_IMAGE'
        and node.inputs[1].links[0].from_socket.name == 'Color'
        and not node.inputs[2].is_linked]
    if len(products) != 1 or not any(link.to_node.type == 'EMISSION'
            and link.to_socket.name == 'Color' for link in products[0].outputs['Color'].links):
        raise RuntimeError('ENDF NPR-Shader transparent albedo contract changed: ' + material.name)


def _source(socket, name):
    return (len(socket.links) == 1 and socket.links[0].from_node.type == 'GROUP_INPUT'
            and socket.links[0].from_socket.name == name)


def _patch(tree):
    """Recognize the observed native-base product and upstream albedo branch."""
    products = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeVectorMath'
                and n.operation == 'MULTIPLY' and
                _source(n.inputs[0], 'F0_BaseMap') and _source(n.inputs[1], '_BaseColor')]
    if len(products) != 1:
        raise RuntimeError('NPC customization: Standard BaseMap product contract changed')
    product = products[0]
    # Upstream s1 has BaseMap -> optional BaseColor mix -> final BaseColor multiply.
    # Replace the final albedo only when customization is enabled; the native
    # customize masks come from the first, exact BaseMap * BaseColor product.
    mixes = [link.to_node for link in product.outputs[0].links
             if link.to_node.bl_idname == 'ShaderNodeMix' and link.to_socket == link.to_node.inputs[5]
             and _source(link.to_node.inputs[4], 'F0_BaseMap')]
    finals = [link.to_node for mix in mixes for link in mix.outputs[1].links
              if link.to_node.bl_idname == 'ShaderNodeVectorMath'
              and link.to_node.operation == 'MULTIPLY'
              and link.to_socket == link.to_node.inputs[0]
              and _source(link.to_node.inputs[1], '_BaseColor')]
    if len(finals) != 1:
        raise RuntimeError('NPC customization: Standard albedo branch contract changed')
    original = finals[0].outputs[0]
    targets = [link.to_socket for link in original.links]
    if not targets:
        raise RuntimeError('NPC customization: Standard albedo has no consumers')
    for name in (ENABLE, *COLORS):
        sock = tree.interface.new_socket(name=name, in_out='INPUT',
            socket_type='NodeSocketFloat' if name == ENABLE else 'NodeSocketVector')
        sock.default_value = 0.0 if name == ENABLE else (1.0, 1.0, 1.0)
    group_input = next(n for n in tree.nodes if n.type == 'GROUP_INPUT')
    from .vendor.ruri_npr.ruri_endfield import G
    previous_nodes = {n.as_pointer() for n in tree.nodes}
    g = G(tree)
    r, green, blue = g.sep(product.outputs[0])
    first = g.mixv(green, group_input.outputs[COLORS[2]], group_input.outputs[COLORS[1]])
    second = g.mixv(blue, first, group_input.outputs[COLORS[0]])
    customized = g.vmath('SCALE', second, s=r)
    enabled = g.math('GREATER_THAN', group_input.outputs[ENABLE], 0.5)
    result = g.mixv(enabled, original, customized)
    for added in tree.nodes:
        if added.as_pointer() not in previous_nodes:
            added['endf_npc_customization_node'] = True
    for target in targets:
        tree.links.new(result, target)
    tree[MARKER] = STAMP
    tree['endf_npr_private_clone'] = True
    if 'endf_npr_source_group' in tree:
        del tree['endf_npr_source_group']


def sync(material):
    """Called from the same write/restore path as the existing material table."""
    if material.get('ruri_uber_part') != 'Standard' or material.node_tree is None:
        return
    floats = dict(material.get('ruri_uber_floats') or {})
    if ENABLE not in floats:
        return
    colors = dict(material.get('ruri_uber_colors') or {})
    enable = float(floats[ENABLE])
    if not math.isfinite(enable):
        raise ValueError('NPC customization: invalid ' + ENABLE)
    if (enable <= 0.5 and not all(name in colors for name in COLORS)
            and not any(n.type == 'GROUP' and n.node_tree and n.node_tree.get(MARKER)
                        for n in material.node_tree.nodes)):
        return
    values = {ENABLE: 1.0 if enable > 0.5 else 0.0}
    for name in COLORS:
        value = list(colors.get(name, (1.0, 1.0, 1.0, 1.0)))
        if len(value) < 3 or not all(math.isfinite(float(v)) for v in value):
            raise ValueError('NPC customization: invalid ' + name)
        # Blender stores socket vectors as float32. Compare that representation
        # to avoid redundant writes/update tags for native double snapshots.
        values[name] = tuple(struct.unpack('<f', struct.pack('<f', float(v)))[0] for v in value[:3])
    # The provider replaces transparent Standard segments before _param_write.
    # Its explicit marker is assigned at replacement time, not after provider()
    # returns. Asset kind/name is deliberately irrelevant to this contract.
    if material.get('endf_npr_transparent_base'):
        validate_transparent_graph(material)
        if enable > 0.5:
            raise RuntimeError('NPC customization: enabled customization is unsupported on the transparent BaseMap path: ' + material.name)
        return
    nodes = [n for n in material.node_tree.nodes if n.type == 'GROUP' and n.node_tree
             and n.get('ruri_inst') == 1 and n.inputs.get('F0_BaseMap') is not None]
    if len(nodes) != 1:
        raise RuntimeError('NPC customization: expected one Standard s1 instance: ' + material.name)
    node = nodes[0]
    if node.node_tree.get(MARKER) != STAMP or node.node_tree.users > 1:
        clone = node.node_tree.copy()
        clone.use_fake_user = False
        try:
            if clone.get(MARKER) != STAMP:
                _patch(clone)
        except Exception:
            bpy.data.node_groups.remove(clone)
            raise
        clone.name = 'ENDF NPC customization ' + material.name
        node.node_tree = clone
    tree = node.node_tree
    owner = material.get('endf_npc_customization_id')
    if not owner:
        owner = uuid.uuid4().hex
        material['endf_npc_customization_id'] = owner
    if tree.get('endf_npc_customization_owner') != owner:
        tree['endf_npc_customization_owner'] = owner
    token = material.get('sora_instance')
    if token and tree.get('sora_instance') != token:
        tree['sora_instance'] = token
    changed = False
    for name, value in values.items():
        sock = node.inputs[name]
        current = float(sock.default_value) if name == ENABLE else tuple(sock.default_value)
        if current != value:
            sock.default_value = value
            changed = True
    # Full adapter rebuild replaces segment nodes. Reclaim only our unreferenced
    # private groups for this material, never shared/vendor groups.
    for stale in list(bpy.data.node_groups):
        if stale != tree and stale.users == 0 and stale.get('endf_npc_customization_owner') == owner:
            bpy.data.node_groups.remove(stale)
    if changed:
        material.node_tree.update_tag()
        material.update_tag()
