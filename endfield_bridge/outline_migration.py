"""One-shot, copy-on-write migration of locally owned outline bridges.

The attributed runtime supplies only the clone bridge upgrade. Existing geometry,
user nodes, materials and old node IDs are retained; no global-name rebuild runs.
"""
import bpy
import mathutils

NOTICE = 'endf_outline_migration_notice'
REVISION_KEY = 'ruri_outline_runtime_revision'


def migrate_object(stack, obj):
    from .vendor.ruri_npr import ruri_endfield as runtime
    mod = obj.modifiers.get(stack.VTX_MODIFIER)
    old = getattr(mod, 'node_group', None)
    if old is None or old.get(REVISION_KEY) == runtime.OUTLINE_RUNTIME_REVISION:
        return False
    if not stack._owns_outline_material(obj):
        return False
    token = obj.get('sora_instance')
    if (obj.library or old.library or not token or old.get('sora_instance') != token):
        raise ValueError('Stale outline is not locally owned by this instance; preserved')
    created = []
    try:
        tree = old.copy(); created.append(tree)
        tree.name = old.name + ' migrated'
        tree.use_fake_user = False
        outlines = [n for n in tree.nodes if n.type == 'GROUP' and n.node_tree
                    and n.node_tree.name.startswith(stack.CLONE_O_PREFIX)]
        if not outlines:
            raise ValueError('Stale outline has no recognized outline clone; preserved')
        clones = {}
        for node in outlines:
            source = node.node_tree
            if source.library or source.get('sora_instance') != token:
                raise ValueError('Stale outline clone is not locally owned; preserved')
            if source not in clones:
                clone = source.copy(); created.append(clone)
                clone.name = source.name + ' migrated'
                clone.use_fake_user = False
                if clone.get(REVISION_KEY) != runtime.OUTLINE_RUNTIME_REVISION:
                    # A partially installed bridge is ambiguous; do not append it twice.
                    labels = {n.label for n in clone.nodes if n.label.startswith('RuriOutline')}
                    if labels:
                        raise ValueError('Stale outline has an unversioned/partial depth bridge; preserved')
                    stack._upgrade_outline_clone(clone)
                clones[source] = clone
            node.node_tree = clones[source]
        gates = [n for n in tree.nodes if n.label == 'RuriOutlineViewGate']
        if not gates:
            stores = [n for n in tree.nodes if n.bl_idname == 'GeometryNodeStoreNamedAttribute'
                      and n.inputs['Name'].default_value == 'ruri_outline']
            if len(stores) != 1:
                raise ValueError('Stale outline requires one outline attribute branch; preserved')
            outgoing = list(stores[0].outputs['Geometry'].links)
            if len(outgoing) != 1 or outgoing[0].to_node.bl_idname != 'GeometryNodeJoinGeometry':
                raise ValueError('Stale outline branch has unsupported downstream edits; preserved')
            target = outgoing[0].to_socket
            invalid = tree.nodes.new('ShaderNodeMath'); invalid.operation = 'SUBTRACT'
            invalid.label = 'RuriOutlineViewInvalid'; invalid.inputs[0].default_value = 1.0
            tree.links.new(outlines[-1].outputs[runtime.OUTLINE_VIEW_VALID], invalid.inputs[1])
            gate = tree.nodes.new('GeometryNodeDeleteGeometry'); gate.domain = 'FACE'
            gate.label = 'RuriOutlineViewGate'
            tree.links.new(stores[0].outputs['Geometry'], gate.inputs['Geometry'])
            tree.links.new(invalid.outputs[0], gate.inputs['Selection'])
            tree.links.remove(outgoing[0])
            tree.links.new(gate.outputs['Geometry'], target)
        else:
            if len(gates) != 1:
                raise ValueError('Stale outline has multiple validity gates; preserved')
            gate = gates[0]
            selection = gate.inputs.get('Selection')
            links = list(selection.links) if selection else []
            if (gate.bl_idname != 'GeometryNodeDeleteGeometry' or len(links) != 1
                    or links[0].from_node.label != 'RuriOutlineViewInvalid'):
                raise ValueError('Stale outline validity gate was edited; preserved')
        # The existing base bounds remain required for translation-invariant ortho.
        if 'ruri_outline_base_center' not in tree or 'ruri_outline_base_extent' not in tree:
            coords = [v.co for v in obj.data.vertices]
            lo = mathutils.Vector(tuple(min((v[i] for v in coords), default=0.0) for i in range(3)))
            hi = mathutils.Vector(tuple(max((v[i] for v in coords), default=0.0) for i in range(3)))
            half = (hi - lo) * 0.5
            basis = obj.matrix_world.to_3x3()
            tree['ruri_outline_base_center'] = tuple((lo + hi) * 0.5)
            tree['ruri_outline_base_extent'] = max(0.01, max(
                (basis @ mathutils.Vector((x*half.x, y*half.y, z*half.z))).length
                for x in (-1,1) for y in (-1,1) for z in (-1,1)))
        tree[REVISION_KEY] = runtime.OUTLINE_RUNTIME_REVISION
        mod.node_group = tree  # sole commit, only after all contracts succeed
        if NOTICE in obj: del obj[NOTICE]
        return True
    except Exception:
        # Parent first releases references to newly copied children.
        for group in created:
            if group.users == 0:
                bpy.data.node_groups.remove(group)
        raise


def restore_outlines(stacks):
    """Called once by load restore, never from the viewport polling callback."""
    result = {'migrated': 0, 'unsupported': []}
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.get('sora_render_mode') == 'BASIC':
            continue
        for stack in stacks:
            if stack.post is not None:
                continue
            try:
                result['migrated'] += bool(migrate_object(stack, obj))
            except Exception as exc:
                message = str(exc)
                obj[NOTICE] = message
                result['unsupported'].append({'object': obj.name, 'reason': message})
                print('[ENDF outline migration] ' + obj.name + ': ' + message)
    for scene in bpy.data.scenes:
        if scene.library is not None:
            continue
        notices = [o.name + ': ' + o[NOTICE] for o in scene.objects if o.get(NOTICE)]
        if notices:
            scene[NOTICE] = '\n'.join(notices)
        elif NOTICE in scene:
            del scene[NOTICE]
    return result
