"""Preserve top-level compositor render sources and scene-local user edits."""
import hashlib
import json
import uuid

import bpy

REVISION = 'top-level-source-v2'
REVISION_KEY = 'endf_npr_post_graph_revision'
ADDED = 'endf_npr_post_added'
BASELINE = 'endf_npr_post_upstream_signature'
ACTIONS = 'endf_npr_post_private_actions'
NOTICE = 'endf_npr_post_notice'
REFERENCES = 'endf_npr_post_signature_references'


def _value(value):
    if isinstance(value, bpy.types.ID):
        return value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, 'items'):
        return {str(k):_value(v) for k,v in value.items()}
    try:
        return [_value(v) for v in value]
    except TypeError:
        return str(value)


def _settings(item):
    ignored = {'name', 'label', 'location', 'width', 'height', 'select', 'hide',
               'show_options', 'show_preview', 'show_texture', 'use_custom_color',
               'color', 'warning_propagation'}
    return {p.identifier: _value(getattr(item, p.identifier))
            for p in item.bl_rna.properties
            if p.identifier not in ignored and not p.is_readonly
            and p.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}}


def _curve(curve):
    return {'path': curve.data_path, 'index': curve.array_index,
            'settings': _settings(curve),
            'keys': [_settings(k) for k in curve.keyframe_points],
            'modifiers': [_settings(m) for m in curve.modifiers]}


def _action(action):
    if action is None:
        return None
    curves = list(getattr(action, 'fcurves', ()))
    layers = []
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                curves.extend(bag.fcurves)
        layers.append(_settings(layer))
    return {'settings': _settings(action), 'layers': layers,
            'curves': [_curve(c) for c in curves]}


def _animation(tree):
    data = tree.animation_data
    if data is None:
        return None
    drivers = []
    for curve in data.drivers:
        drivers.append({'curve': _curve(curve), 'driver': _settings(curve.driver),
            'variables': [{'settings': _settings(v), 'targets': [
                {'settings': _settings(t), 'id': _value(t.id)} for t in v.targets]}
                for v in curve.driver.variables]})
    return {'action': _action(data.action), 'drivers': drivers,
            'nla': [[{'settings': _settings(s), 'action': _action(s.action)}
                     for s in track.strips] for track in data.nla_tracks]}


def signature(tree, ignored=(), *, animation=True, layout=True, register=False):
    """Behavioral signature, excluding node-editor layout and our metadata."""
    ignored = set(ignored)
    animation_state = _animation(tree) if animation else None
    animated_paths = set()
    def paths(value):
        if isinstance(value,dict):
            if 'path' in value and 'index' in value:
                animated_paths.add(value['path'])
            for child in value.values(): paths(child)
        elif isinstance(value,list):
            for child in value: paths(child)
    paths(animation_state)
    nodes = []
    for node in tree.nodes:
        if node.name in ignored:
            continue
        record = {'name': node.name, 'type': node.bl_idname,
            'settings': _settings(node), 'mute': node.mute,
            'group': _value(getattr(node, 'node_tree', None)),
            'scene': _value(getattr(node, 'scene', None)),
            'sockets': [[(s.identifier, '<animated>' if hasattr(s,'default_value') and s.path_from_id('default_value') in animated_paths
                         else _value(getattr(s, 'default_value', None)))
                         for s in sockets] for sockets in (node.inputs, node.outputs)],
            'properties': {k:_value(v) for k,v in node.items()}}
        if layout:
            record['layout'] = [list(node.location),node.width,node.label,node.hide,
                                node.parent.name if node.parent else None]
        nodes.append(record)
    payload = {'nodes': nodes, 'links': sorted([
        (l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
        for l in tree.links if l.from_node.name not in ignored and l.to_node.name not in ignored]),
        'interface': [(s.name, getattr(s, 'in_out', ''), getattr(s, 'socket_type', ''))
                      for s in tree.interface.items_tree],
        'properties': {k: _value(v) for k, v in tree.items() if not k.startswith('endf_npr_post_')}}
    if animation:
        payload['animation'] = animation_state
    references = list(dict(tree.get(REFERENCES, {})).values())
    def serialize(value):
        if isinstance(value,bpy.types.ID):
            if value == tree:
                return ['SELF',value.bl_rna.identifier]
            for index,known in enumerate(references):
                if value == known:
                    return ['ID',value.bl_rna.identifier,index]
            if register:
                references.append(value)
                return ['ID',value.bl_rna.identifier,len(references)-1]
            return ['new ID',value.bl_rna.identifier,value.name_full]
        raise TypeError(type(value).__name__)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=serialize).encode()).hexdigest()
    if register:
        tree[REFERENCES] = {str(i):value for i,value in enumerate(references)}
    return digest


def _assert_render_sources(tree, depth=0, visited=None):
    visited = set() if visited is None else visited
    if tree.as_pointer() in visited:
        return
    visited.add(tree.as_pointer())
    for node in tree.nodes:
        if depth and node.bl_idname == 'CompositorNodeRLayers':
            raise ValueError('Existing compositor has Render Layers inside a nested group; '
                             'move that source to the scene tree before enabling ENDF post: ' + tree.name)
        nested = getattr(node, 'node_tree', None)
        if nested is not None:
            _assert_render_sources(nested, depth + 1, visited)


def _output(tree):
    outputs = [n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput' and n.is_active_output]
    if len(outputs) != 1:
        raise ValueError('Existing compositor requires exactly one active Group Output')
    node = outputs[0]
    socket = node.inputs.get('Image')
    if socket is None:
        socket = next((s for s in node.inputs if s.type == 'RGBA'), None)
    if socket is None or len(socket.links) > 1:
        raise ValueError('Existing compositor requires an Image/color output')
    return node, socket


def _reachable(output):
    result, todo = set(), [output]
    while todo:
        node = todo.pop()
        if node in result:
            continue
        result.add(node)
        todo.extend(l.from_node for socket in node.inputs for l in socket.links)
    return result


def _copy_actions(tree, previous):
    data = tree.animation_data
    if data is None:
        return
    copied = {}
    def clone(action):
        if action is None:
            return None
        if action.as_pointer() not in copied:
            copied[action.as_pointer()] = action.copy()
            copied[action.as_pointer()].use_fake_user = False
        return copied[action.as_pointer()]
    if data.action is not None:
        data.action = clone(data.action)
    for track in data.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                strip.action = clone(strip.action)
    for curve in data.drivers:
        for variable in curve.driver.variables:
            for target in variable.targets:
                if target.id == previous:
                    target.id = tree
    tree[ACTIONS] = {str(i): action for i, action in enumerate(copied.values())}


def dispose(tree):
    actions = list(dict(tree.get(ACTIONS, {})).values())
    bpy.data.node_groups.remove(tree)
    for action in actions:
        if action.users == 0:
            bpy.data.actions.remove(action)


def unused(tree):
    # A self-targeting driver is not another scene/user adopting this graph.
    return not tree.use_fake_user and not (bpy.data.user_map(subset={tree}).get(tree,set())-{tree})


def build(scene, previous, group, color_in, color_out, owner_key):
    if previous is not None:
        _assert_render_sources(previous)
        _output(previous)
    tree = previous.copy() if previous is not None else bpy.data.node_groups.new(
        'ENDF NPR-Shader Post Scene', 'CompositorNodeTree')
    tree.name = 'ENDF NPR-Shader Post Scene ' + uuid.uuid4().hex[:8]
    tree.use_fake_user = False
    try:
        tree[owner_key] = scene
        tree[REVISION_KEY] = REVISION
        if previous is not None:
            _copy_actions(tree, previous)
        else:
            tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
            render = tree.nodes.new('CompositorNodeRLayers')
            render.scene = scene
            output = tree.nodes.new('NodeGroupOutput')
            tree.links.new(render.outputs['Image'], output.inputs['Image'])
        output, image = _output(tree)
        reachable = _reachable(output)
        existing = [n for n in reachable if getattr(n, 'node_tree', None) is not None
                    and (n.node_tree == group or n.node_tree.get('ruri_stamp') == group.get('ruri_stamp')
                         or n.node_tree.get('endf_npr_source_group') == 'Ruri Endfield Post')]
        if len(existing) > 1:
            raise ValueError('Existing compositor already contains multiple ENDF post stages; preserved')
        if existing:
            candidate = existing[0].node_tree
            # Metadata is excluded only for canonical attribution differences.
            def canonical_signature(t):
                return signature(t, animation=False, layout=False, register=True)
            # Source tags differ between the attributed original and adapter.
            # Compare nodes via copies with tags removed; originals stay untouched.
            copies = [candidate.copy(), group.copy()]
            try:
                for copy in copies:
                    for key in list(copy.keys()):
                        del copy[key]
                equal = (canonical_signature(copies[0]) == canonical_signature(copies[1])
                         and candidate.animation_data is None and group.animation_data is None)
            finally:
                for copy in copies:
                    bpy.data.node_groups.remove(copy)
            if not equal:
                raise ValueError('Existing ENDF post stage was edited or has an unknown contract; preserved')
            tree[ADDED] = json.dumps({'nodes': [], 'mode': 'verified_existing_stage'})
        else:
            source = image.links[0].from_socket if image.is_linked else None
            baseline_default = _value(image.default_value)
            split = tree.nodes.new('CompositorNodeSeparateColor')
            pack = tree.nodes.new('ShaderNodeCombineXYZ')
            stage = tree.nodes.new('CompositorNodeGroup'); stage.node_tree = group
            unpack = tree.nodes.new('ShaderNodeSeparateXYZ')
            join = tree.nodes.new('CompositorNodeCombineColor')
            added = [split, pack, stage, unpack, join]
            for i, node in enumerate(added):
                node.label = 'ENDF post adapter'
                node.location = (output.location.x + i*190, output.location.y-220)
            if source is not None:
                tree.links.new(source, split.inputs['Image'])
            else:
                split.inputs['Image'].default_value = baseline_default
            for channel, axis in (('Red','X'),('Green','Y'),('Blue','Z')):
                tree.links.new(split.outputs[channel],pack.inputs[axis])
            tree.links.new(pack.outputs[0],stage.inputs[color_in])
            tree.links.new(stage.outputs[color_out],unpack.inputs[0])
            for channel, axis in (('Red','X'),('Green','Y'),('Blue','Z')):
                tree.links.new(unpack.outputs[axis],join.inputs[channel])
            tree.links.new(split.outputs['Alpha'],join.inputs['Alpha'])
            tree.links.new(join.outputs['Image'],image)
            tree[ADDED] = json.dumps({'nodes':[n.name for n in added], 'mode':'appended',
                'output':[output.name,image.identifier], 'join':join.name,
                'source':[source.node.name,source.identifier] if source is not None else None,
                'default':baseline_default})
        tree[BASELINE] = signature(tree, json.loads(tree[ADDED])['nodes'], register=True)
        return tree
    except Exception:
        dispose(tree)
        raise


def upstream_changed(tree):
    record = json.loads(tree[ADDED])
    return signature(tree, record['nodes']) != tree[BASELINE]


def retain_edited_upstream(tree):
    """Strip only our adapter; keep an edited private upstream active on disable."""
    record = json.loads(tree[ADDED])
    if record['nodes']:
        nodes = [tree.nodes.get(name) for name in record['nodes']]
        if any(n is None for n in nodes):
            raise ValueError('ENDF adapter nodes were edited; preserve this graph and migrate it explicitly')
        output = tree.nodes.get(record['output'][0])
        image = next((s for s in output.inputs if s.identifier == record['output'][1]), None) if output else None
        if image is not None and image.is_linked and image.links[0].from_node.name == record['join']:
            source = record['source']
            if source is not None:
                node = tree.nodes.get(source[0])
                socket = next((s for s in node.outputs if s.identifier == source[1]), None) if node else None
                if socket is None:
                    raise ValueError('Edited compositor removed the original output source; preserved for explicit migration')
                tree.links.new(socket,image)
            else:
                tree.links.remove(image.links[0])
                image.default_value = record['default']
        for node in nodes:
            tree.nodes.remove(node)
    for key in list(tree.keys()):
        if key.startswith('endf_npr_post_'):
            del tree[key]
    tree.name = 'ENDF preserved edited compositor'
    return tree
