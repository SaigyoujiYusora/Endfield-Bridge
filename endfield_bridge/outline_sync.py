"""Outline view synchronization adapted from RuriNPR (AGPL-3.0-or-later).

Source: vendor/ruri_npr/ruri_endfield.py, Stack.sync_outline_view.
See vendor/ruri_npr/LICENSE.txt for the original license.
The geometry and projection formulas are unchanged. Compare desired inputs in
Blender RNA's float32 representation so an identical view never dirties geometry.
"""
import math
import struct

import bpy
import mathutils


def _float32(value):
    return struct.unpack('f', struct.pack('f', float(value)))[0]


def _set_vector_socket(socket, value):
    value = tuple(_float32(v) for v in value)
    if tuple(socket.default_value) != value:
        socket.default_value = value
        return True
    return False


def sync_outline_view(stack, view=None, camera=None, objects=None, rebuild=True):
    """Update view constants without rebuilding any current modifier tree."""
    from .vendor.ruri_npr import ruri_endfield as runtime
    payload = stack._camera_outline_view(camera) if camera is not None else view
    scene = bpy.context.scene
    pool = [o for o in (objects if objects is not None else scene.objects)
            if o.type == 'MESH' and o.data is not None]
    stale = []
    if rebuild:
        for obj in pool:
            if not stack._owns_outline_material(obj):
                continue
            mod = obj.modifiers.get(stack.VTX_MODIFIER)
            tree = getattr(mod, 'node_group', None) if mod is not None else None
            if tree is None or tree.get('ruri_outline_runtime_revision') != runtime.OUTLINE_RUNTIME_REVISION:
                stale.append(obj)
        if stale:
            stack.apply_vertex_stage(objects=stale, camera=camera)

    valid = payload is not None
    changed_objects = {obj.as_pointer() for obj in stale}
    for obj in pool:
        mod = obj.modifiers.get(stack.VTX_MODIFIER)
        tree = getattr(mod, 'node_group', None) if mod is not None else None
        if tree is None:
            continue
        outline_nodes = [node for node in tree.nodes if node.type == 'GROUP'
                         and getattr(node, 'node_tree', None) is not None
                         and node.node_tree.name.startswith(stack.CLONE_O_PREFIX)]
        if not outline_nodes:
            continue
        changed = False
        if valid:
            world = payload['matrix_world']
            camera_basis = world.to_3x3()
            look_world = camera_basis @ mathutils.Vector((0.0, 0.0, -1.0))
            inv = obj.matrix_world.inverted()
            inv3 = inv.to_3x3()
            right = inv3 @ camera_basis.col[0]
            up = inv3 @ camera_basis.col[1]
            look = inv3 @ look_world
            half_fov = float(payload['half_fov'])
            if payload['projection'] == 'ORTHOGRAPHIC':
                saved_center = tree.get('ruri_outline_base_center') or (0.0, 0.0, 0.0)
                center = obj.matrix_world @ mathutils.Vector(tuple(saved_center))
                extent = max(float(tree.get('ruri_outline_base_extent', 0.01)), 0.01)
                virtual_distance = max(extent * 1000.0, 10.0)
                camera_position = center - look_world.normalized() * virtual_distance
                half_fov = math.atan(float(payload['ortho_height']) / (2.0 * virtual_distance))
            else:
                camera_position = world.translation
            position = inv @ camera_position
            near = max(float(payload['clip_start']), 1.0e-5)
            far = max(float(payload['clip_end']), near + 1.0e-4)
            perspective = payload['projection'] == 'PERSPECTIVE'
            depth_coefficient = (runtime.OUTLINE_NDC_SCALE * (far - near) / (far * near)
                                 if perspective else runtime.OUTLINE_NDC_SCALE * (far - near) * 0.5)
            for node in outline_nodes:
                changed |= _set_vector_socket(node.inputs['cam_right'], right)
                changed |= _set_vector_socket(node.inputs['cam_up'], up)
                changed |= _set_vector_socket(node.inputs['cam_look'], look)
                changed |= _set_vector_socket(node.inputs['cam_pos'], position)
                values = {
                    'half_fov': half_fov,
                    'screen_x': max(float(payload['width']), 2.0),
                    'screen_y': max(float(payload['height']), 2.0),
                    runtime.OUTLINE_VIEW_VALID: 1.0,
                    runtime.OUTLINE_PROJECTION: 1.0 if perspective else 0.0,
                    runtime.OUTLINE_DEPTH_COEFFICIENT: depth_coefficient,
                }
                for name, value in values.items():
                    socket = node.inputs.get(name)
                    value = _float32(value)
                    if socket is not None and float(socket.default_value) != value:
                        socket.default_value = value
                        changed = True
        else:
            for node in outline_nodes:
                socket = node.inputs.get(runtime.OUTLINE_VIEW_VALID)
                if socket is not None and float(socket.default_value) != 0.0:
                    socket.default_value = 0.0
                    changed = True
        if changed:
            tree.update_tag()
            obj.update_tag()
            changed_objects.add(obj.as_pointer())
    return len(changed_objects)
