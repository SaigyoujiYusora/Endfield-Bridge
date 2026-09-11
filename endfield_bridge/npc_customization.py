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


TRANSPARENT_STAMP = 'basemap-links-v2'
VERIFIED_SHADER = ('CAB-8e64a7d61483ea16539b04f304be9ed7', '-7822190029627442914')


def validate_transparent_graph(material):
    """Closed, socket-identity contract for the provider's flat BaseMap graph."""
    def fail():
        raise RuntimeError('ENDF NPR-Shader transparent graph contract changed: ' + material.name)
    tree = material.node_tree
    if tree is None or material.surface_render_method != 'BLENDED':
        fail()
    kinds = ('TEX_COORD', 'MAPPING', 'TEX_IMAGE', 'MIX_RGB', 'EMISSION',
             'MATH', 'BSDF_TRANSPARENT', 'MIX_SHADER', 'OUTPUT_MATERIAL')
    nodes = list(tree.nodes)
    if len(nodes) != len(kinds) or any(sum(n.type == k for n in nodes) != 1 for k in kinds):
        fail()
    uv, mapping, image, tint, emission, alpha, transparent, mix, output = (
        next(n for n in nodes if n.type == k) for k in kinds)
    if (image.label != '_BaseMap' or image.image is None
            or tint.blend_type != 'MULTIPLY' or tint.use_clamp
            or tint.inputs[0].default_value != 1.0
            or alpha.operation != 'MULTIPLY' or alpha.use_clamp
            or mapping.vector_type != 'POINT'):
        fail()
    pairs = [(uv.outputs['UV'], mapping.inputs['Vector']),
             (mapping.outputs['Vector'], image.inputs['Vector']),
             (image.outputs['Color'], tint.inputs[1]),
             (tint.outputs['Color'], emission.inputs['Color']),
             (image.outputs['Alpha'], alpha.inputs[0]),
             (alpha.outputs[0], mix.inputs[0]),
             (transparent.outputs[0], mix.inputs[1]),
             (emission.outputs[0], mix.inputs[2]),
             (mix.outputs[0], output.inputs['Surface'])]
    if len(tree.links) != len(pairs) or any(not any(
            link.from_socket == source and link.to_socket == target
            for link in tree.links) for source, target in pairs):
        fail()
    marker = material.get('endf_npr_transparent_base')
    if marker not in (None, True, TRANSPARENT_STAMP):
        fail()
    return tint, alpha


def sync_transparent(material):
    tint, alpha = validate_transparent_graph(material)
    color = list(dict(material.get('ruri_uber_colors') or {}).get('_BaseColor', (1, 1, 1, 1)))
    if len(color) != 4 or not all(math.isfinite(float(x)) for x in color):
        raise ValueError('Transparent BaseColor must contain four finite values')
    tint.inputs[2].default_value = color
    alpha.inputs[1].default_value = color[3]


def validate_shader_identity(material):
    import json
    # shaderSourceRef.fileId is a relative dependency index, not a CAB name.
    # Formula provenance is keyed by the resolved shaderId (CAB + pathId).
    raw = material.get('sora_native_shader_id')
    if raw is None:
        descriptor = json.loads(material.get('sora_material_descriptor', '{}'))
        ref = (descriptor.get('source') or {}).get('shaderId') or {}
    else:
        ref = json.loads(raw)
    if (str(ref.get('cab')), str(ref.get('pathId'))) != VERIFIED_SHADER:
        raise RuntimeError('NPC customization: native shader identity has no verified formula contract: ' + material.name)


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


def sync(material, record=None):
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
    validate_shader_identity(material)
    nodes = [n for n in material.node_tree.nodes if n.type == 'GROUP' and n.node_tree
             and n.get('ruri_inst') == 1 and n.inputs.get('F0_BaseMap') is not None]
    if len(nodes) != 1:
        raise RuntimeError('NPC customization: expected one Standard s1 instance: ' + material.name)
    node = nodes[0]
    if node.node_tree.get(MARKER) != STAMP or node.node_tree.users > 1:
        clone = node.node_tree.copy()
        if record is not None:
            # Local ENDF2Blend modification: register this private clone with the
            # per-build ownership record at the copy site so a cancelled import
            # can release exactly the groups it created.
            record.group(clone)
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
