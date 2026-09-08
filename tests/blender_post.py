"""Run in Blender after enabling endfield_bridge; preserves the active scene.

Checks independent scenes, shared user graph preservation, repeat toggles,
scene duplication, replacement graph ownership, viewport restoration, GPU
selection, and actual .blend library round-trip of persistent ID pointers.
"""
import json
from pathlib import Path
import tempfile

import bpy
from endfield_bridge import post


def signature(tree):
    return ([(n.name, n.bl_idname, tuple(n.location), n.mute) for n in tree.nodes],
            [(l.from_node.name, l.from_socket.identifier, l.to_node.name,
              l.to_socket.identifier) for l in tree.links],
            [(s.name, s.item_type, getattr(s, 'in_out', '')) for s in tree.interface.items_tree])


def view(scene):
    return (scene.view_settings.view_transform, scene.view_settings.look,
            scene.view_settings.exposure, scene.view_settings.gamma,
            scene.render.use_compositing, getattr(scene.render, 'compositor_device', None))


scenes, groups = [], []
window = bpy.context.window
original = window.scene if window else None
original_screen = window.screen if window else None
viewport_before = [(s, s.shading.use_compositor) for a in original_screen.areas
                   for s in [a.spaces.active] if a.type == 'VIEW_3D'] if original_screen else []
try:
    user = bpy.data.node_groups.new('ENDF test preserved user compositor', 'CompositorNodeTree')
    groups.append(user)
    user.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    render = user.nodes.new('CompositorNodeRLayers')
    output = user.nodes.new('NodeGroupOutput')
    user.links.new(render.outputs['Image'], output.inputs['Image'])
    user['user_sentinel'] = 'Do not rewrite this graph'
    before = signature(user)
    first = bpy.data.scenes.new('ENDF post test first'); scenes.append(first)
    second = bpy.data.scenes.new('ENDF post test second'); scenes.append(second)
    for scene in (first, second):
        scene.compositing_node_group = user
        scene.view_settings.exposure = 1.25
        scene.view_settings.gamma = 0.85
        scene.render.use_compositing = False
    baseline = view(first)
    if window:
        window.scene = first
        post.sync_viewport(bpy.context)
        viewport_before = [(s, s.shading.use_compositor) for a in window.screen.areas
                           for s in [a.spaces.active] if a.type == 'VIEW_3D']
    first_tree = post.enable(first, bpy.context)
    assert post.enable(first, bpy.context) == first_tree, 'enable is not idempotent'
    second_tree = post.enable(second, bpy.context)
    assert first_tree != second_tree and post.installed(first) and post.installed(second)
    assert first.get(post.PREVIOUS) == user and second.get(post.PREVIOUS) == user
    assert signature(user) == before and user['user_sentinel'] == 'Do not rewrite this graph'
    stage = next(n for n in first_tree.nodes if getattr(n, 'node_tree', None)
                 and n.node_tree.get('endf_npr_source_group') == 'Ruri Endfield Post')
    assert len(stage.node_tree.nodes) == 47, 'Unexpected bundled post group'
    assert any(getattr(n, 'node_tree', None) == user for n in first_tree.nodes)
    assert view(first)[:4] == ('Standard', 'None', 0.0, 1.0)
    if hasattr(first.render, 'compositor_device'):
        assert first.render.compositor_device == 'GPU'
    if window:
        assert all(s.shading.use_compositor == 'ALWAYS' for s, _ in viewport_before)
        window.scene = second
        post.sync_viewport(bpy.context)
        assert window.screen.get(post.VIEW_OWNER) == second
    post.disable(first)
    assert first.compositing_node_group == user and view(first) == baseline
    assert post.installed(second), 'Disabling one scene affected another'
    if window:
        assert all(s.shading.use_compositor == 'ALWAYS' for s, _ in viewport_before)
    duplicate = second.copy(); scenes.append(duplicate)
    duplicate_tree = post.enable(duplicate, bpy.context)
    assert duplicate_tree != second_tree and duplicate_tree.get(post.OWNER) == duplicate
    assert duplicate.get(post.PREVIOUS) == user
    post.disable(duplicate)
    assert duplicate.compositing_node_group == user and view(duplicate) == baseline
    assert post.installed(second)
    post.disable(second)
    assert second.compositing_node_group == user and view(second) == baseline
    assert all(s.shading.use_compositor == value for s, value in viewport_before)
    for _ in range(3):
        post.enable(first, bpy.context)
        post.disable(first)
    assert signature(user) == before
    # A user selecting a different active graph must win over stale metadata.
    post.enable(first, bpy.context)
    first.compositing_node_group = user
    first.view_settings.exposure = 2.0
    post.disable(first)
    assert first.compositing_node_group == user and first.view_settings.exposure == 2.0
    # A foreign load handler replacing the compositor must not lose saved intent.
    expected_tree = post.enable(first, bpy.context)
    post._save_pre(None)
    foreign = bpy.data.node_groups.get('Ruri Endfield Post Scene')
    if foreign is None:
        foreign = bpy.data.node_groups.new('Ruri Endfield Post Scene', 'CompositorNodeTree')
        groups.append(foreign)
    first.compositing_node_group = foreign
    post._restore_loaded_post()
    assert post.installed(first) and first.compositing_node_group == expected_tree
    # A deliberate replacement saved by the user is never recovered over.
    first.compositing_node_group = user
    post._save_pre(None)
    first.compositing_node_group = foreign
    post._restore_loaded_post()
    assert first.compositing_node_group == foreign
    first.compositing_node_group = user
    post.disable(first)
    # UI opt-out and BASIC mode never automatically install a compositor.
    first.sora.npr_post_processing = False
    first.sora.material_mode = 'RURI'
    if window:
        window.scene = first
        assert post.enable_for_import(bpy.context) is None
        first.sora['npr_post_processing'] = True  # Stored default, no UI callback.
        first.sora.material_mode = 'BASIC'
        assert post.enable_for_import(bpy.context) is None
    # Detached scene avoids saving current screen/UI references in fixture.
    detached = bpy.data.scenes.new('ENDF post saved pointer fixture'); scenes.append(detached)
    detached.compositing_node_group = user
    detached.view_settings.exposure = 0.75
    detached_baseline = view(detached)
    post.enable(detached, bpy.context)
    with tempfile.TemporaryDirectory(prefix='endf-post-') as folder:
        path = str(Path(folder) / 'post-roundtrip.blend')
        bpy.data.libraries.write(path, {detached})
        with bpy.data.libraries.load(path, link=False) as (source, destination):
            destination.scenes = [detached.name]
        loaded = destination.scenes[0]; scenes.append(loaded)
        loaded_previous = loaded.get(post.PREVIOUS)
        if loaded_previous is not None and loaded_previous != user:
            groups.append(loaded_previous)
        assert loaded_previous is not None and post.installed(loaded)
        assert loaded.get(post.WRAPPER).get(post.OWNER) == loaded
        post.disable(loaded)
        assert loaded.compositing_node_group == loaded_previous and view(loaded) == detached_baseline
    print(json.dumps({'result': 'ENDF_NPR_POST_LIFECYCLE_OK',
                      'checks': ['two scenes', 'idempotent enable', '47-node post', 'GPU compositor',
                                 'shared user graph preserved', 'color settings restored',
                                 'scene duplication', 'viewport switch and restoration',
                                 'repeated toggle', 'user replacement retained', 'BASIC and opt-out',
                                 'blend persistent ID pointer round-trip']}))
finally:
    for scene in reversed(scenes):
        post.disable(scene)
    if window and original:
        window.scene = original
        post.sync_viewport(bpy.context)
    for scene in reversed(scenes):
        bpy.data.scenes.remove(scene)
    for group in groups:
        if group.users == 0:
            bpy.data.node_groups.remove(group)
