"""Execute via MCP: synthetic outline sockets, no changes to existing objects."""
import math
import bpy
from mathutils import Matrix
from endfield_bridge import ruri_adapter
from endfield_bridge.vendor.ruri_npr import ruri_endfield as runtime


def exercise_outline_sync():
    stack = next(s for s in ruri_adapter.stacks() if s.post is None and s.CLONE_O_PREFIX)
    mesh = bpy.data.meshes.new('ENDF outline sync fixture')
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new('ENDF outline sync root', 'GeometryNodeTree')
    child = bpy.data.node_groups.new(stack.CLONE_O_PREFIX + 'sync fixture', 'GeometryNodeTree')
    try:
        for name in ('cam_right', 'cam_up', 'cam_look', 'cam_pos'):
            child.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketVector')
        for name in ('half_fov', 'screen_x', 'screen_y', runtime.OUTLINE_VIEW_VALID,
                     runtime.OUTLINE_PROJECTION, runtime.OUTLINE_DEPTH_COEFFICIENT):
            child.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketFloat')
        node = tree.nodes.new('GeometryNodeGroup')
        node.node_tree = child
        obj.modifiers.new(stack.VTX_MODIFIER, 'NODES').node_group = tree
        def values():
            return {s.name: tuple(s.default_value) if s.type == 'VECTOR' else s.default_value
                    for s in node.inputs}
        payload = dict(projection='PERSPECTIVE', matrix_world=Matrix.Translation((2, 3, 4)),
                       width=1234, height=789, half_fov=math.atan(1 / 1.7320507764816284),
                       ortho_height=2 / 1.7320507764816284, clip_start=0.01, clip_end=1000.0)
        results = []
        for projection in ('PERSPECTIVE', 'ORTHOGRAPHIC'):
            payload['projection'] = projection
            runtime.Stack.sync_outline_view(stack, view=payload, objects=[obj], rebuild=False)
            native = values()
            stack.sync_outline_view(view=payload, objects=[obj], rebuild=False)
            assert values() == native, 'Frontend changed native projection/socket values'
            counts = [stack.sync_outline_view(view=payload, objects=[obj], rebuild=False) for _ in range(5)]
            assert counts == [0] * 5, ('Identical view dirties geometry', counts)
            moved = dict(payload, matrix_world=Matrix.Rotation(0.15, 4, 'Y') @ payload['matrix_world'])
            assert stack.sync_outline_view(view=moved, objects=[obj], rebuild=False) == 1
            assert stack.sync_outline_view(view=moved, objects=[obj], rebuild=False) == 0
            obj.matrix_world = Matrix.Rotation(0.2, 4, 'Z')
            assert stack.sync_outline_view(view=moved, objects=[obj], rebuild=False) == 1
            assert stack.sync_outline_view(view=moved, objects=[obj], rebuild=False) == 0
            obj.matrix_world = Matrix.Identity(4)
            results.append(projection)
        print('ENDF_OUTLINE_SYNC_OK', results)
        return results
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.data.node_groups.remove(tree)
        bpy.data.node_groups.remove(child)


exercise_outline_sync()
