"""Projection-aware observation direction for the attributed NPR node library.

The bundled s0 groups normalize camera-position minus surface-position in
object space, with the library's Y/Z swizzle. This adapter supplies the rendering
engine's per-shading-point incoming vector to that same normalization instead.
It has no viewport handler or shared camera uniforms. The original position
branches remain available for depth and other position-dependent calculations.
"""

REVISION = 'incoming-object-yz-v1'
STAMP = 'endf_npr_view_direction'
# Each additional part has the same verified s0 camera-minus-surface contract.
# Crossing X71 alone is not sufficient evidence (Eyes has no such crossing).
PARTS = {'Face', 'Eyes', 'Standard', 'Hair', 'Fur', 'VFX', 'LiquidAg'}
PREFIX = 'ENDF Observation '


def _source(socket):
    links = list(socket.links)
    if len(links) != 1:
        raise ValueError('NPR observation input must have exactly one link')
    return links[0].from_socket


def _expect_input(node, index, source, output=0):
    actual = _source(node.inputs[index])
    if actual != source.outputs[output]:
        raise ValueError('Unexpected NPR observation link: ' + node.name)


def _node(group, name, kind, **settings):
    node = group.nodes.get(name)
    if node is None or node.bl_idname != kind:
        raise ValueError('Unexpected NPR observation node: ' + name)
    if any(getattr(node, key) != value for key, value in settings.items()):
        raise ValueError('Unexpected NPR observation transform: ' + name)
    return node


def _swizzle(group, separate_name, combine_name, upstream):
    separate = _node(group, separate_name, 'ShaderNodeSeparateXYZ')
    combine = _node(group, combine_name, 'ShaderNodeCombineXYZ')
    _expect_input(separate, 0, upstream)
    for target, source in enumerate((0, 2, 1)):
        _expect_input(combine, target, separate, source)
    return combine


def patch_group(group):
    """Patch only the explicitly verified Character s0 contracts; return change count.

    Call when the adapter obtains an owned library group, before template copies.
    Unexpected contracts fail before mutation rather than guessing node meaning.
    """
    source = group.get('endf_npr_source_group') or group.name
    if source not in {prefix + part + ' s0' for part in PARTS for prefix in
                      ('Ruri Endfield Uber ', 'ENDF NPR-Shader Character ')}:
        return 0
    if group.library is not None:
        raise ValueError('Cannot modify a linked NPR observation group')
    normalize = _node(group, 'Vector Math.006', 'ShaderNodeVectorMath',
                      operation='NORMALIZE')
    if group.get(STAMP) == REVISION:
        geometry = _node(group, PREFIX + 'Incoming', 'ShaderNodeNewGeometry')
        transform = _node(group, PREFIX + 'Object', 'ShaderNodeVectorTransform',
                          vector_type='VECTOR', convert_from='WORLD', convert_to='OBJECT')
        if _source(transform.inputs[0]) != geometry.outputs['Incoming']:
            raise ValueError('Modified NPR incoming direction contract')
        swapped = _swizzle(group, PREFIX + 'Separate', PREFIX + 'Swizzle', transform)
        _expect_input(normalize, 0, swapped)
        return 0
    if group.get(STAMP) or any(n.name.startswith(PREFIX) for n in group.nodes):
        raise ValueError('Unknown NPR observation revision; explicit migration required')
    camera = _node(group, 'Vector Transform.003', 'ShaderNodeVectorTransform',
                   vector_type='POINT', convert_from='CAMERA', convert_to='WORLD')
    if camera.inputs[0].is_linked or tuple(camera.inputs[0].default_value) != (0., 0., 0.):
        raise ValueError('NPR camera position must be the camera origin')
    camera_object = _node(group, 'Vector Transform.004', 'ShaderNodeVectorTransform',
                          vector_type='POINT', convert_from='WORLD', convert_to='OBJECT')
    _expect_input(camera_object, 0, camera)
    camera_swapped = _swizzle(group, 'Separate XYZ.003', 'Combine XYZ.004', camera_object)
    position = _node(group, 'Vector Transform', 'ShaderNodeVectorTransform',
                     vector_type='POINT', convert_from='WORLD', convert_to='OBJECT')
    position_input = _source(position.inputs[0])
    if position_input.node.bl_idname != 'NodeGroupInput' or position_input.name != 'input_positionWS':
        raise ValueError('NPR surface position is not input_positionWS')
    position_swapped = _swizzle(group, 'Separate XYZ', 'Combine XYZ', position)
    subtract = _node(group, 'Vector Math.005', 'ShaderNodeVectorMath', operation='SUBTRACT')
    _expect_input(subtract, 0, camera_swapped)
    _expect_input(subtract, 1, position_swapped)
    _expect_input(normalize, 0, subtract)
    created = []
    try:
        for kind, label in [('ShaderNodeNewGeometry', 'Incoming'),
                            ('ShaderNodeVectorTransform', 'Object'),
                            ('ShaderNodeSeparateXYZ', 'Separate'),
                            ('ShaderNodeCombineXYZ', 'Swizzle')]:
            node = group.nodes.new(kind)
            created.append(node)
            node.name = node.label = PREFIX + label
        geometry, transform, separate, combine = created
        transform.vector_type = 'VECTOR'
        transform.convert_from = 'WORLD'
        transform.convert_to = 'OBJECT'
        group.links.new(geometry.outputs['Incoming'], transform.inputs[0])
        group.links.new(transform.outputs[0], separate.inputs[0])
        for target, source_axis in enumerate((0, 2, 1)):
            group.links.new(separate.outputs[source_axis], combine.inputs[target])
        group.links.new(combine.outputs[0], normalize.inputs[0])
        group[STAMP] = REVISION
    except Exception:
        group.links.new(subtract.outputs[0], normalize.inputs[0])
        for node in reversed(created):
            group.nodes.remove(node)
        if STAMP in group:
            del group[STAMP]
        raise
    return 1
