"""Scene-local ENDF NPR-Shader compositor integration (AGPL-3.0-or-later).

The RGB/vector adapter follows the attributed RuriNPR post stage; the bundled
post group retains its upstream license (see vendor/ruri_npr/LICENSE.txt). Never
call Stack.install/uninstall here: those functions own a global scene-tree name.

The game post includes its own tone mapping. Standard/None avoids a second
display tone map; exposure=0 and gamma=1 reproduce its reference input/output
contract. All four original view settings are restored on disable. Existing
user compositors are evaluated as an untouched nested source before game post.
"""
import json
import uuid

import bpy
from bpy.app.handlers import persistent

# Hot reload replaces Python function objects, while Blender retains the old
# callbacks. Remove only this module's previous callbacks before rebinding.
for _timer_name in ('_view_timer', '_restore_loaded_post'):
    _previous_timer = globals().get(_timer_name)
    if _previous_timer is not None and bpy.app.timers.is_registered(_previous_timer):
        bpy.app.timers.unregister(_previous_timer)
for _handlers in (bpy.app.handlers.load_post, bpy.app.handlers.save_pre):
    for _handler in list(_handlers):
        if getattr(_handler, '__module__', '') == __name__:
            _handlers.remove(_handler)

STATE = 'endf_npr_post_state'
PREVIOUS = 'endf_npr_post_previous'
WRAPPER = 'endf_npr_post_wrapper'
SAVED_ACTIVE = 'endf_npr_post_saved_active'
OWNER = 'endf_npr_post_owner'
SCREENS = 'endf_npr_post_screens'
VIEW_OWNER = 'endf_npr_post_view_owner'
VIEW_STATE = 'endf_npr_post_view_state'


def installed(scene):
    tree = scene.get(WRAPPER)
    return (tree is not None and scene.compositing_node_group == tree
            and tree.get(OWNER) == scene)


def _snapshot(scene):
    view = scene.view_settings
    return {'use_compositing': scene.render.use_compositing,
            'view_transform': view.view_transform, 'look': view.look,
            'exposure': view.exposure, 'gamma': view.gamma,
            'compositor_device': getattr(scene.render, 'compositor_device', None)}


def _restore_settings(scene, saved):
    scene.render.use_compositing = saved['use_compositing']
    # Order matters: changing the transform can reset the available look.
    scene.view_settings.view_transform = saved['view_transform']
    scene.view_settings.look = saved['look']
    scene.view_settings.exposure = saved['exposure']
    scene.view_settings.gamma = saved['gamma']
    if saved.get('compositor_device') is not None:
        scene.render.compositor_device = saved['compositor_device']


def _build(scene, previous):
    from .ruri_adapter import stacks
    stack = next(stack for stack in stacks() if stack.post is not None)
    group = stack.group(stack.post['group'])
    if group.library is not None:
        raise RuntimeError('ENDF NPR-Shader post requires its local bundled node group')
    tree = bpy.data.node_groups.new('ENDF NPR-Shader Post Scene ' + uuid.uuid4().hex[:8], 'CompositorNodeTree')
    try:
        tree[OWNER] = scene
        tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        if previous is not None:
            source = tree.nodes.new('CompositorNodeGroup')
            source.node_tree = previous
            source.label = 'Previous scene compositor (preserved)'
            color = source.outputs.get('Image')
            if color is None:
                color = next((socket for socket in source.outputs if socket.type == 'RGBA'), None)
            if color is None:
                raise ValueError('Existing compositor needs an Image/color output before ENDF NPR-Shader post can be enabled')
        else:
            source = tree.nodes.new('CompositorNodeRLayers')
            color = source.outputs['Image']
        split = tree.nodes.new('CompositorNodeSeparateColor')
        pack = tree.nodes.new('ShaderNodeCombineXYZ')
        stage = tree.nodes.new('CompositorNodeGroup')
        stage.node_tree = group
        stage.label = 'ENDF NPR-Shader post-processing'
        unpack = tree.nodes.new('ShaderNodeSeparateXYZ')
        join = tree.nodes.new('CompositorNodeCombineColor')
        output = tree.nodes.new('NodeGroupOutput')
        tree.links.new(color, split.inputs['Image'])
        for channel, axis in (('Red', 'X'), ('Green', 'Y'), ('Blue', 'Z')):
            tree.links.new(split.outputs[channel], pack.inputs[axis])
        tree.links.new(pack.outputs['Vector'], stage.inputs[stack.post['color_in']])
        tree.links.new(stage.outputs[stack.post['color_out']], unpack.inputs['Vector'])
        for channel, axis in (('Red', 'X'), ('Green', 'Y'), ('Blue', 'Z')):
            tree.links.new(unpack.outputs[axis], join.inputs[channel])
        # Extract alpha from the actual source color, including a user graph.
        tree.links.new(split.outputs['Alpha'], join.inputs['Alpha'])
        tree.links.new(join.outputs['Image'], output.inputs['Image'])
        for index, node in enumerate((source, split, pack, stage, unpack, join, output)):
            node.location = (index * 190, 0)
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def _restore_viewport(screen):
    raw = screen.get(VIEW_STATE)
    if not raw:
        return
    for record in json.loads(raw):
        live = next((space for area in screen.areas
                     if str(area.as_pointer()) == record.get('area_pointer')
                     for space in area.spaces
                     if str(space.as_pointer()) == record.get('space_pointer')
                     and space.type == 'VIEW_3D'), None)
        if live is not None:
            if live.shading.use_compositor == 'ALWAYS':
                live.shading.use_compositor = record['value']
            continue
        index, space_index = record['area'], record['space']
        if index >= len(screen.areas):
            continue
        area = screen.areas[index]
        if area.type != 'VIEW_3D' or space_index >= len(area.spaces):
            continue
        space = area.spaces[space_index]
        # Screen/area indices persist across save/reload; geometry guards
        # against applying an old setting to a newly rearranged editor.
        if space.type == 'VIEW_3D' and list((area.x, area.y, area.width, area.height)) == record['rect']:
            if space.shading.use_compositor == 'ALWAYS':
                space.shading.use_compositor = record['value']
    for key in (VIEW_STATE, VIEW_OWNER):
        if key in screen:
            del screen[key]


def sync_viewport(context=None):
    """Affect only the current window, and restore it on scene switches."""
    context = context or bpy.context
    window = context.window
    if window is None or window.screen is None:
        return
    screen, scene = window.screen, window.scene
    if screen.get(VIEW_OWNER) == scene and installed(scene):
        return
    if screen.get(VIEW_STATE):
        _restore_viewport(screen)
    if not installed(scene):
        return
    records = []
    for index, area in enumerate(screen.areas):
        if area.type != 'VIEW_3D':
            continue
        # Only the active VIEW_3D space, not dormant editor spaces.
        space = area.spaces.active
        records.append({'area': index, 'space': list(area.spaces).index(space),
                        'area_pointer': str(area.as_pointer()),
                        'space_pointer': str(space.as_pointer()),
                        'rect': [area.x, area.y, area.width, area.height],
                        'value': space.shading.use_compositor})
        space.shading.use_compositor = 'ALWAYS'
    if records:
        screen[VIEW_OWNER] = scene
        screen[VIEW_STATE] = json.dumps(records)
        screens = dict(scene.get(SCREENS, {}))
        screens[screen.name] = screen
        scene[SCREENS] = screens


def enable(scene, context=None):
    if scene.library is not None:
        raise ValueError('Enable ENDF NPR-Shader post on a local scene')
    if installed(scene):
        sync_viewport(context)
        return scene.get(WRAPPER)
    old_wrapper = scene.get(WRAPPER)
    previous = scene.compositing_node_group
    # A duplicated scene inherits ID pointers. Unwrap its inherited wrapper
    # and give the duplicate independent ownership, retaining its baseline.
    inherited = previous is not None and previous == old_wrapper and STATE in scene
    saved = json.loads(scene[STATE]) if inherited else _snapshot(scene)
    if inherited:
        previous = scene.get(PREVIOUS)
    elif STATE in scene:
        # User selected a replacement compositor while enabled: release our
        # stale metadata without replacing that deliberate user selection.
        disable(scene)
    tree = _build(scene, previous)
    try:
        scene[STATE] = json.dumps(saved)
        if previous is not None:
            scene[PREVIOUS] = previous
        elif PREVIOUS in scene:
            del scene[PREVIOUS]
        scene[WRAPPER] = tree
        scene[SAVED_ACTIVE] = True
        scene.compositing_node_group = tree
        scene.render.use_compositing = True
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        if hasattr(scene.render, 'compositor_device'):
            scene.render.compositor_device = 'GPU'
        sync_viewport(context)
        return tree
    except Exception:
        scene.compositing_node_group = previous
        _restore_settings(scene, saved)
        for key in (STATE, PREVIOUS, WRAPPER, SAVED_ACTIVE):
            if key in scene:
                del scene[key]
        if tree.users == 0:
            bpy.data.node_groups.remove(tree)
        raise


def disable(scene):
    saved = json.loads(scene[STATE]) if STATE in scene else None
    tree = scene.get(WRAPPER)
    for screen in list(dict(scene.get(SCREENS, {})).values()):
        if screen is not None and screen.get(VIEW_OWNER) == scene:
            _restore_viewport(screen)
    # Restore only if we still own the active assignment. If a user replaced
    # the compositor, retain that graph and its current display settings.
    if tree is not None and scene.compositing_node_group == tree:
        scene.compositing_node_group = scene.get(PREVIOUS)
        if saved is not None:
            _restore_settings(scene, saved)
    for key in (STATE, PREVIOUS, WRAPPER, SCREENS, SAVED_ACTIVE):
        if key in scene:
            del scene[key]
    # Never unlink/delete another scene's wrapper, or a user-adopted group.
    if tree is not None and tree.get(OWNER) == scene and tree.users == 0:
        bpy.data.node_groups.remove(tree)
    return saved is not None


def enable_for_import(context, material_mode=None):
    settings = getattr(context.scene, 'sora', None)
    mode = material_mode or getattr(settings, 'material_mode', 'BASIC')
    if mode in {'RURI', 'NPR'} and getattr(settings, 'npr_post_processing', True):
        return enable(context.scene, context)
    return None


def _view_timer():
    sync_viewport()
    return 0.5


@persistent
def _save_pre(_unused):
    # Save actual assignment intent, including a user's replacement graph.
    for scene in bpy.data.scenes:
        if scene.library is None and STATE in scene:
            scene[SAVED_ACTIVE] = installed(scene)


def _restore_loaded_post():
    # Run after every add-on's load_post handler. Upstream's global post
    # installer can replace our saved assignment while opening a file.
    from .ruri_adapter import stacks
    upstream_names = {stack.post['scene_tree'] for stack in stacks() if stack.post}
    for scene in bpy.data.scenes:
        tree = scene.get(WRAPPER)
        current = scene.compositing_node_group
        if (scene.library is not None or tree is None or tree.get(OWNER) != scene
                or not scene.get(SAVED_ACTIVE, False)):
            continue
        if current != tree and (current is None or current.name not in upstream_names):
            continue  # Never take over an unrelated user compositor.
        scene.compositing_node_group = tree
        scene.render.use_compositing = True
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        if hasattr(scene.render, 'compositor_device'):
            scene.render.compositor_device = 'GPU'
    sync_viewport()
    return None


@persistent
def _load_post(_unused):
    if not bpy.app.timers.is_registered(_restore_loaded_post):
        bpy.app.timers.register(_restore_loaded_post, first_interval=0.0)


def register():
    if _save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_save_pre)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    if not bpy.app.timers.is_registered(_view_timer):
        bpy.app.timers.register(_view_timer, first_interval=0.5, persistent=True)


def unregister():
    if _save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_save_pre)
    if bpy.app.timers.is_registered(_restore_loaded_post):
        bpy.app.timers.unregister(_restore_loaded_post)
    if bpy.app.timers.is_registered(_view_timer):
        bpy.app.timers.unregister(_view_timer)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for scene in bpy.data.scenes:
        if scene.library is None and STATE in scene:
            disable(scene)
