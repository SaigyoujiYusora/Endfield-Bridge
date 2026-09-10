"""ENDF NPR-Shader frontend adapter, derived from the attributed RuriNPR runtime."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import bpy

# Dispose previous function objects before importlib.reload replaces their names.
_previous_timer = globals().get('_view_timer')
if _previous_timer is not None and bpy.app.timers.is_registered(_previous_timer):
    bpy.app.timers.unregister(_previous_timer)
from . import parameter_upload
for _previous_stack in globals().get('_STACKS') or ():
    parameter_upload.cancel(_previous_stack)
for _handlers in (bpy.app.handlers.load_post, bpy.app.handlers.render_pre,
                  bpy.app.handlers.frame_change_post, bpy.app.handlers.depsgraph_update_post):
    for _handler in list(_handlers):
        if getattr(_handler, '__module__', '') == __name__:
            _handlers.remove(_handler)
_STACKS = None
_SYNC_BUSY = False
_TIMER_BUSY = False


def stacks():
    global _STACKS
    if _STACKS is None:
        from .vendor.ruri_npr import ruri_endfield as runtime
        from .neutral_images import neutral_image
        from .projection_tables import install as install_projection_tables
        install_projection_tables(runtime)
        runtime._neutral_image = neutral_image
        runtime.LIGHT_TABLE = 'ENDF NPR-Shader Light Table'
        folder = str(Path(runtime.__file__).parent)
        class EndfStack(runtime.Stack):
            def _param_flush_soon(self):
                parameter_upload.schedule(self)

            def sync_outline_view(self, view=None, camera=None, objects=None, rebuild=True):
                from .outline_sync import sync_outline_view
                return sync_outline_view(self, view=view, camera=camera,
                                         objects=objects, rebuild=rebuild)

            def build_material(self, *args, **kwargs):
                # Assembly may call the upstream light refresh directly.
                # Invalidate before it can replace the shared table contents.
                from . import runtime_sync
                runtime_sync.reset()
                result = super().build_material(*args, **kwargs)
                mat = args[0] if args else kwargs['mat']
                if mat.get('endf_npr_transparent_base'):
                    del mat['endf_npr_transparent_base']
                return result

            def _wire_params(self, g, insts, part):
                if bpy.context.scene.render.engine != 'CYCLES':
                    return super()._wire_params(g, insts, part)
                from .cycles_uniforms import wire
                return wire(self, g, insts, part)

            def _transparent_base_output(self, mat, image, props):
                super()._transparent_base_output(mat, image, props)
                from .npc_customization import validate_transparent_graph
                validate_transparent_graph(mat)
                # provider() writes parameters before returning the material.
                # Record the actual graph replacement before those callbacks.
                from .npc_customization import TRANSPARENT_STAMP
                mat['endf_npr_transparent_base'] = TRANSPARENT_STAMP

            def provider(self, builder, props):
                # Reject destructive vendor cache upgrades before any loading,
                # rename, remap or deletion. Existing scenes require explicit migration.
                self._check_existing_templates()
                self._pending_shader_ref = dict(props.shader_ref or {})
                self._pending_shader_id = dict(getattr(props, 'shader_id', None) or {})
                try:
                    return super().provider(builder, props)
                finally:
                    self._pending_shader_ref = None
                    self._pending_shader_id = None

            def _check_existing_templates(self, groups_only=False):
                for mat in bpy.data.materials:
                    if not groups_only and mat.name.startswith(self.TEMPLATE_MAT) and (
                            mat.get(self.STAMP_KEY) != self.STAMP + ':transparent-basemap-v2'
                            or mat.node_tree is None):
                        raise RuntimeError('Existing NPR template requires explicit migration; preserved: ' + mat.name)
                for group in bpy.data.node_groups:
                    source = group.get('endf_npr_source_group') or group.name
                    if source in self.group_names and not group.get('endf_npr_private_clone'):
                        if group.library is not None or group.get('ruri_stamp') != self.STAMP:
                            raise RuntimeError('Existing NPR group requires explicit migration; preserved: ' + group.name)

            def panel_write(self, mat, row, value):
                from .npc_customization import ENABLE, validate_transparent_graph, validate_shader_identity
                name = row['name']
                floats = dict(mat.get('ruri_uber_floats') or {})
                if name == '_SurfaceType' and float(value) != float(floats.get(name, 0)):
                    raise ValueError('SurfaceType changes require a new material import; current material is preserved')
                if mat.get('endf_npr_transparent_base'):
                    validate_transparent_graph(mat)
                    if name == ENABLE and float(value) > 0.5:
                        raise ValueError('Enabled customization is unsupported on the transparent BaseMap path')
                elif name == ENABLE and float(value) > 0.5:
                    validate_shader_identity(mat)
                # Preflight the current contract before vendor mutates snapshots.
                from .npc_customization import sync
                sync(mat)
                old_colors = {k: list(v) for k, v in dict(mat.get('ruri_uber_colors') or {}).items()}
                sockets = []
                for node in [*self._panel_insts(mat), *self._panel_vertex_nodes(mat)]:
                    for socket in node.inputs:
                        if hasattr(socket, 'default_value'):
                            v = socket.default_value
                            sockets.append((socket, v if isinstance(v, (int, float, str)) else tuple(v)))
                col = mat.get('ruri_param_col')
                old_column = self._mat_mirror()[:, int(col), :].copy() if col is not None else None
                try:
                    return super().panel_write(mat, row, value)
                except Exception:
                    mat['ruri_uber_floats'] = floats
                    mat['ruri_uber_colors'] = old_colors
                    for socket, old in sockets:
                        socket.default_value = old
                    if old_column is not None:
                        self._mat_mirror()[:, int(col), :] = old_column
                        self._param_flush_soon()
                    if mat.get('endf_npr_transparent_base'):
                        from .npc_customization import sync_transparent
                        sync_transparent(mat)
                    raise

            def _param_write(self, mat):
                pending = getattr(self, '_pending_shader_ref', None)
                if pending is not None:
                    mat['sora_native_shader_ref'] = json.dumps(pending, sort_keys=True)
                resolved = getattr(self, '_pending_shader_id', None)
                if resolved is not None:
                    mat['sora_native_shader_id'] = json.dumps(resolved, sort_keys=True)
                from .npc_customization import sync_transparent
                if mat.get('endf_npr_transparent_base'):
                    sync_transparent(mat)
                column = super()._param_write(mat)
                from .cycles_uniforms import sync
                sync(self, mat, column)
                from .npc_customization import sync as sync_customization
                sync_customization(mat)
                return column

            def restore(self):
                super().restore()
                from .cycles_uniforms import sync
                for mat in bpy.data.materials:
                    if (mat.get('ruri_uber_stack') == self.PANEL_KEY
                            and mat.get('ruri_param_col') is not None
                            and mat.get('ruri_uber_part') in self.m['parts']):
                        sync(self, mat, mat['ruri_param_col'])
                        from .npc_customization import sync as sync_customization
                        sync_customization(mat)

            def group(self, name):
                self._check_existing_templates(groups_only=True)
                existing = next((group for group in bpy.data.node_groups
                    if group.library is None and group.get('endf_npr_source_group') == name
                    and not group.get('sora_instance') and not group.get('endf_npr_private_clone')
                    and group.get('ruri_stamp') == self.STAMP), None)
                group = existing if existing is not None else super().group(name)
                group['endf_npr_source_group'] = name
                group.name = name.replace('Ruri Endfield Uber', 'ENDF NPR-Shader Character').replace('Ruri Endfield', 'ENDF NPR-Shader')
                from .view_direction import patch_group
                patch_group(group)
                return group

            def _clone_vtx(self, template_name, clone_name, mat):
                group = super()._clone_vtx(template_name, clone_name, mat)
                if 'endf_npr_source_group' in group:
                    del group['endf_npr_source_group']
                group['endf_npr_private_clone'] = True
                return group
        _STACKS = []
        for original in runtime.MANIFESTS:
            manifest = deepcopy(original)
            manifest['host']['registry_module'] = __name__
            for key in list(manifest['host']):
                if key.startswith(('register_', 'unregister_')):
                    manifest['host'][key] = '_reject_host_registration'
            # Source group IDs remain the exact bundled-library lookup keys.
            for key in ('panel_title', 'mat_table', 'template_mat', 'vtx_modifier',
                        'vtx_tree_prefix', 'clone_v', 'clone_o', 'material_name'):
                value = manifest['names'].get(key)
                if value:
                    manifest['names'][key] = value.replace('Ruri Endfield', 'ENDF NPR-Shader').replace('Ruri_Endfield', 'ENDF NPR-Shader')
            manifest['names']['panel_key'] = manifest['names']['panel_key'].replace('ruri_', 'endf_npr_', 1)
            manifest['host']['rig_identity_module'] = __name__
            manifest['host']['rig_bone_fn'] = 'resolve_bone'
            manifest['host']['rig_unity_name_fn'] = 'unity_bone_name'
            _STACKS.append(EndfStack(folder, manifest))
        runtime.STACKS[:] = _STACKS
    return _STACKS


def _reject_host_registration(*args, **kwargs):
    raise RuntimeError('ENDF NPR-Shader uses its local adapter lifecycle; upstream host registration is disabled')


def resolve_bone(armature, unity_name):
    if armature is None:
        return ''
    for bone in armature.data.bones:
        path = bone.get('sora_source_path', '')
        if path and path.rsplit('/', 1)[-1] == unity_name:
            return bone.name
    head = json.loads(armature.get('sora_head_reference', '{}'))
    if unity_name == 'Bip001_Head' and isinstance(head.get('name'), str) and head['name'] in armature.data.bones:
        return head['name']
    return unity_name if unity_name in armature.data.bones else ''


def unity_bone_name(armature, bone_name):
    bone = armature.data.bones.get(bone_name)
    return (bone.get('sora_source_path', '').rsplit('/', 1)[-1] or bone_name) if bone else ''


def _discard_failed_materials(before_materials, before_groups):
    """provider() can fail before returning its newly allocated datablock."""
    for pending in list(bpy.data.materials):
        if pending.as_pointer() not in before_materials and pending.users == 0:
            bpy.data.materials.remove(pending)
    for pending in list(bpy.data.node_groups):
        if (pending.as_pointer() not in before_groups and pending.users == 0
                and pending.get('endf_npc_customization')):
            bpy.data.node_groups.remove(pending)


def build_material(records, images, token):
    if len(records) != 1:
        raise ValueError('Each ENDF NPR-Shader material slot requires one record')
    source = records[0]
    descriptor = source.get('npr') or {}
    native = descriptor.get('source') or {}
    bindings = descriptor.get('textures') or {}
    props = SimpleNamespace(
        name=f"{source['name']} [{token[:8]}]", shader_ref=native.get('shaderSourceRef') or {},
        shader_id=native.get('shaderId') or {},
        floats={**descriptor.get('ints', {}), **descriptor.get('floats', {})},
        colors=descriptor.get('colors', {}),
        textures={k: v['textureId'] for k, v in bindings.items() if v.get('textureId')},
        texture_st={k: list(v.get('scale') or [1, 1]) + list(v.get('offset') or [0, 0]) for k, v in bindings.items()},
        disabled_passes=(descriptor.get('renderState') or {}).get('disabledPasses', []))
    builder = SimpleNamespace(options={}, shader_display_name=lambda p: native.get('shaderName'), _load_image=images.get)
    for stack in stacks():
        if stack.post is not None:
            continue
        resolved = stack._variant(builder, props)
        if resolved is None:
            continue
        part, _ = resolved
        meta = stack.PART_META[part]
        transparent_base = ((props.floats.get('_SurfaceType', 0) >= 0.5 or meta['transparent'])
                            and not meta.get('multiply') and stack.ST_SLOT in props.textures)
        image_view = None
        original_base = images.get(props.textures.get(stack.ST_SLOT))
        if transparent_base and original_base is not None:
            # Alpha interpretation is image-global in Blender. Give the flat
            # transparent pass a private view before upstream sets STRAIGHT.
            image_view = original_base.copy()
            image_view.name = 'ENDF NPR-Shader transparent ' + props.name
            image_view['sora_instance'] = token
            image_view['sora_image_view'] = 'transparent BaseMap'
            image_view.pack()
            base_id = props.textures[stack.ST_SLOT]
            builder._load_image = lambda identity: image_view if identity == base_id else images.get(identity)
        material = None
        prior_names = [(item, item.name) for item in bpy.data.materials]
        before_materials = {item.as_pointer() for item in bpy.data.materials}
        before_groups = {item.as_pointer() for item in bpy.data.node_groups}
        try:
            material = stack.provider(builder, props)
            if material is None:
                raise RuntimeError('ENDF NPR-Shader provider declined its resolved shader')
            material['sora_instance'] = token
            material['sora_material_descriptor'] = json.dumps(descriptor)
            material['sora_native_shader_ref'] = json.dumps(props.shader_ref, sort_keys=True)
            material['sora_native_shader_id'] = json.dumps(props.shader_id, sort_keys=True)
            material['sora_render_pipeline'] = 'ENDF NPR-Shader'
            if transparent_base and original_base is not None:
                from .npc_customization import validate_transparent_graph
                validate_transparent_graph(material)
                if not material.get('endf_npr_transparent_base'):
                    raise RuntimeError('ENDF NPR-Shader transparent graph lifecycle marker is missing')
            # Cached templates omit engine/world identity; replay the assembly
            # while preserving the deliberate flat transparent graph.
            rewire_material(stack, material)
            material['sora_ruri_engine'] = bpy.context.scene.render.engine
            return material
        except Exception:
            # Shared templates have fake users and survive. Existing unused
            # user materials are protected by the pre-provider snapshot.
            _discard_failed_materials(before_materials, before_groups)
            for existing, name in prior_names:
                existing.name = name
            if image_view is not None and image_view.users == 0:
                bpy.data.images.remove(image_view)
            raise
    from .materials import build_material as basic
    material = basic(records, images, token, 'BASIC')
    material['sora_npr_diagnostics'] = 'ENDF NPR-Shader does not claim native shader: ' + str(native.get('shaderName'))
    return material


def object_frame(document):
    """Place the shader's native local frame inside a compensating object yaw."""
    def rotate(value):
        return [-value[0], -value[1], *value[2:]]
    bones = []
    for bone in document['bones']:
        item = dict(bone, head=rotate(bone['head']), tail=rotate(bone['tail']))
        if bone.get('restMatrix') is not None:
            matrix = bone['restMatrix']
            item['restMatrix'] = [-v for v in matrix[:8]] + matrix[8:]
        bones.append(item)
    meshes = []
    for mesh in document['meshes']:
        item = dict(mesh, positions=[rotate(v) for v in mesh['positions']],
                    normals=[rotate(v) for v in mesh['normals']],
                    shapes=[dict(shape, offsets=[rotate(v) for v in shape['offsets']]) for shape in mesh['shapes']])
        if mesh.get('tangents') is not None:
            item['tangents'] = [rotate(v) for v in mesh['tangents']]
        meshes.append(item)
    return dict(document, bones=bones, meshes=meshes)


def prepare_mesh(obj, source):
    mesh = obj.data
    color = mesh.color_attributes.get('Color')
    if color is None:
        color = mesh.color_attributes.new(name='Color', type='FLOAT_COLOR', domain='POINT')
    native_colors = source.get('colors')
    for index, item in enumerate(color.data):
        item.color = native_colors[index] if native_colors else (1.0, 1.0, 1.0, 1.0)
    for uv in source.get('uvSets') or []:
        if uv['set'] == 0:
            continue
        if uv['set'] == 1:
            layer = mesh.uv_layers.get('UV1') or mesh.uv_layers.new(name='UV1')
            for loop in mesh.loops:
                values = uv['values'][loop.vertex_index]
                layer.data[loop.index].uv = (values[0], values[1] if len(values) > 1 else 0)
            continue
        attr = mesh.attributes.new(name=f"UV{uv['set']}", type='FLOAT_VECTOR', domain='POINT')
        for item, value in zip(attr.data, uv['values']):
            item.vector = tuple(list(value[:3]) + [0] * max(0, 3 - len(value)))
    tangent = mesh.attributes.new(name='ruri_tangent', type='FLOAT_VECTOR', domain='CORNER')
    sign = mesh.attributes.new(name='ruri_tangent_sign', type='FLOAT', domain='CORNER')
    native = source.get('tangents')
    if native:
        for loop in mesh.loops:
            tangent.data[loop.index].vector = native[loop.vertex_index][:3]
            sign.data[loop.index].value = -native[loop.vertex_index][3]
    elif mesh.uv_layers:
        mesh.calc_tangents(uvmap=mesh.uv_layers[0].name)
        for loop in mesh.loops:
            tangent.data[loop.index].vector = loop.tangent
            sign.data[loop.index].value = -loop.bitangent_sign
        mesh.free_tangents()


def finish_import(context, objects):
    from .vendor.ruri_npr import ruri_endfield as runtime
    # Exact names establish ownership even if upstream raises after creating a
    # root/clone but before attaching it to a modifier. Shared templates are
    # excluded, and pre-existing datablocks never acquire this import's token.
    before = {group.as_pointer() for group in bpy.data.node_groups}
    names = {}
    for obj in objects:
        token = obj.get('sora_instance')
        if not token:
            continue
        for stack in stacks():
            if stack.post is not None:
                continue
            names[stack.VTX_TREE_PREFIX + obj.name] = token
            for material in obj.data.materials:
                if material is not None and material.get('sora_instance') == token:
                    for prefix in (stack.CLONE_V_PREFIX, stack.CLONE_O_PREFIX):
                        if prefix:
                            names[prefix + material.name] = token
    try:
        for stack in stacks():
            if stack.post is None:
                stack._param_flush()
                stack.apply_vertex_stage(objects=objects, camera=context.scene.camera)
    finally:
        for group in bpy.data.node_groups:
            token = names.get(group.name)
            if token and group.as_pointer() not in before and group.get('sora_instance') in (None, token):
                group['sora_instance'] = token
                group.use_fake_user = False
    context.view_layer.update()
    update(context.scene, context.evaluated_depsgraph_get())
    update_view(context)
    register()


def update_view(context):
    view = None
    if context.screen:
        for area in context.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            rv = space.region_3d
            if region is None or rv is None:
                continue
            scale = abs(float(rv.window_matrix[1][1]))
            if scale < 1e-8:
                continue
            view = dict(projection='PERSPECTIVE' if rv.is_perspective else 'ORTHOGRAPHIC',
                        matrix_world=rv.view_matrix.inverted().copy(),
                        half_fov=math.atan(1 / scale), ortho_height=2 / scale,
                        width=region.width, height=region.height,
                        clip_start=space.clip_start, clip_end=space.clip_end)
            break
    for stack in stacks():
        if stack.post is None:
            stack.sync_outline_view(view=view, camera=None if view else context.scene.camera, rebuild=False)


def _view_timer():
    global _TIMER_BUSY
    if _TIMER_BUSY:
        return 0.2
    _TIMER_BUSY = True
    try:
        if _STACKS is not None and not bpy.app.is_job_running('RENDER'):
            refresh_engine(bpy.context.scene)
            update_view(bpy.context)
    finally:
        _TIMER_BUSY = False
    return 0.2


@bpy.app.handlers.persistent
def render_view(scene, *_):
    if scene != bpy.context.scene:
        with _scene_override(scene):
            return render_view(scene)
    refresh_engine(scene)
    # The table is shared across scenes: an already-built material still needs
    # the requested render scene's lights and evaluated rig basis before draw.
    update(scene)
    for stack in stacks():
        if stack.post is None:
            stack.sync_outline_view(camera=scene.camera, rebuild=False)
    flush_pending_tables(scene)


def flush_pending_tables(scene):
    """A render cannot wait for the interactive 0.1-second upload timer."""
    used = {slot.material.get('ruri_uber_stack') for obj in scene.objects
            for slot in obj.material_slots if slot.material is not None}
    return parameter_upload.flush_pending(stacks(), used)


@bpy.app.handlers.persistent
def restore(*_):
    from .vendor.ruri_npr import ruri_endfield as runtime
    from . import runtime_sync
    from .neutral_images import localize_material
    runtime_sync.reset()
    stacks()
    runtime.RIG_DRIVEN.clear()
    runtime.RIG_SCANNED[0] = False
    used = {m.get('ruri_uber_stack') for m in bpy.data.materials}
    for stack in stacks():
        if stack.post is None and stack.PANEL_KEY in used:
            stack.restore()
    for material in bpy.data.materials:
        if material.get('ruri_uber_stack') in {stack.PANEL_KEY for stack in stacks()}:
            localize_material(material)
    from .outline_migration import restore_outlines
    restore_outlines(stacks())
    update()


@bpy.app.handlers.persistent
def update(scene=None, depsgraph=None):
    global _SYNC_BUSY
    if _STACKS is None or _SYNC_BUSY:
        return
    from .vendor.ruri_npr import ruri_endfield as runtime
    from . import runtime_sync
    scene = scene if scene is not None else bpy.context.scene
    depsgraph = _matching_depsgraph(scene, depsgraph)
    if scene != bpy.context.scene:
        with _scene_override(scene, depsgraph):
            return update(scene, depsgraph)
    _SYNC_BUSY = True
    try:
        runtime_sync.push_rig_basis(runtime, _STACKS, scene, depsgraph)
        runtime_sync.refresh_lights(runtime, scene)
    finally:
        _SYNC_BUSY = False


def register():
    if restore not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(restore)
    if render_view not in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.append(render_view)
    if not bpy.app.background and not bpy.app.timers.is_registered(_view_timer):
        bpy.app.timers.register(_view_timer, first_interval=0.2, persistent=True)
    for handlers in (bpy.app.handlers.frame_change_post, bpy.app.handlers.depsgraph_update_post):
        if update not in handlers:
            handlers.append(update)


def unregister():
    for stack in _STACKS or ():
        parameter_upload.cancel(stack)
    if restore in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(restore)
    if render_view in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.remove(render_view)
    if bpy.app.timers.is_registered(_view_timer):
        bpy.app.timers.unregister(_view_timer)
    for handlers in (bpy.app.handlers.frame_change_post, bpy.app.handlers.depsgraph_update_post):
        if update in handlers:
            handlers.remove(update)


def release_instance(token):
    """Remove only unused instance geometry; shared shader templates stay cached."""
    pending = [group for group in bpy.data.node_groups if group.get('sora_instance') == token]
    while pending:
        unused = [group for group in pending if group.users == 0]
        if not unused:
            break
        for group in unused:
            pending.remove(group)
            bpy.data.node_groups.remove(group)
    if _STACKS is not None:
        for stack in _STACKS:
            if stack._mirror is not None:
                stack._mirror = None
                stack._next_col[0] = 1
                stack._mat_mirror()
                stack._param_flush()


def _matching_depsgraph(scene, depsgraph):
    """Never evaluate target-scene objects through another scene's graph."""
    if depsgraph is None:
        return None
    graph_scene = depsgraph.scene
    return depsgraph if getattr(graph_scene, 'original', graph_scene) == scene else None


def _scene_override(scene, depsgraph=None):
    depsgraph = _matching_depsgraph(scene, depsgraph)
    layer = (scene.view_layers.get(depsgraph.view_layer.name)
             if depsgraph is not None else None)
    if layer is None:
        layer = scene.view_layers[0]
    return bpy.context.temp_override(scene=scene, view_layer=layer)


def refresh_engine(scene):
    """Reassemble when engine/world inputs change, retaining material identity."""
    if _STACKS is None:
        return
    if scene != bpy.context.scene:
        with _scene_override(scene):
            return refresh_engine(scene)
    engine = scene.render.engine
    signature = capability_signature(scene)
    materials = {slot.material for obj in scene.objects for slot in obj.material_slots
                 if slot.material is not None}
    for material in materials:
        if material.get('sora_render_pipeline') not in {'ENDF NPR-Shader', 'ENDF-NPR', 'RuriNPR original'} or material.get('endf_npr_capability_signature') == signature:
            continue
        stack = next((item for item in _STACKS if item.PANEL_KEY == material.get('ruri_uber_stack')), None)
        if stack is not None and rewire_material(stack, material):
            material['sora_ruri_engine'] = engine
            material['endf_npr_capability_signature'] = signature


def capability_signature(scene):
    """Hash world graph values, not evaluation state or frame number."""
    world = scene.world
    state = [scene.render.engine, world.name_full if world else None]
    if world:
        state.extend([bool(world.use_nodes), list(world.color)])
        tree = world.node_tree if world.use_nodes else None
        if tree:
            for node in tree.nodes:
                values = []
                for socket in node.inputs:
                    value = getattr(socket, 'default_value', None)
                    if value is not None and not isinstance(value, (str, bool, int, float)):
                        try:
                            value = list(value)
                        except TypeError:
                            value = str(value)
                    values.append(value)
                state.append([node.name, node.bl_idname, values,
                              getattr(getattr(node, 'image', None), 'name_full', None),
                              getattr(node, 'operation', None), getattr(node, 'projection', None)])
            state.append([(link.from_node.name, link.from_socket.identifier,
                           link.to_node.name, link.to_socket.identifier) for link in tree.links])
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def audit_capability_links(material):
    """Read-only evidence of capability nodes shared with assembly consumers."""
    return [dict(source=link.from_node.name, capability=link.from_node.get('ruri_cap'),
                 destination=link.to_node.name, label=link.to_node.label,
                 socket=link.to_socket.name, destination_type=link.to_node.type)
            for link in material.node_tree.links
            if link.from_node.get('ruri_cap') is not None
            and link.to_node.get('ruri_cap') is None]


def rewire_material(stack, material):
    if material.get('endf_npr_transparent_base'):
        from .npc_customization import validate_transparent_graph
        validate_transparent_graph(material)
        material['endf_npr_capability_signature'] = capability_signature(bpy.context.scene)
        return True
    part = material.get('ruri_uber_part')
    if part not in stack.m['parts'] or material.get('ruri_uber_stack') != stack.PANEL_KEY:
        return False
    # G caches common expressions across capability and parameter assembly.
    # Deleting capability-tagged nodes can therefore sever parameter UV paths.
    # Replay the complete assembly into this same datablock instead.
    floats = dict(material.get('ruri_uber_floats') or {})
    meta = stack.PART_META[part]
    opaque = floats.get('_SurfaceType', 0.0) < 0.5 and not meta['transparent']
    cull = float(floats.get(stack.CULL_PROPERTY, 0.0)) if stack.CULL_PROPERTY else stack.CULL_FIXED
    images = stack._material_images(material)
    stack.build_material(material, part=part, opaque=opaque,
                         multiply_blend=bool(meta.get('multiply')), cull=cull, images=images)
    column = stack._param_write(material)
    texture_st = dict(material.get('ruri_uber_st') or {})
    st = texture_st.get(stack.ST_SLOT) or [1.0, 1.0, 0.0, 0.0]
    for node in material.node_tree.nodes:
        if node.label == 'RuriMatCol':
            node.outputs[0].default_value = float(column)
        elif node.label == stack.ST_NODE:
            node.inputs['Scale'].default_value = (float(st[0]), float(st[1]), 1.0)
            node.inputs['Location'].default_value = (float(st[2]), float(st[3]), 0.0)
    stack._param_flush()
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH' and any(slot.material == material for slot in obj.material_slots):
            armature = stack._rig_basis_armature(obj)
            if armature is not None:
                stack.rig_apply(material, armature, obj)
    material.node_tree.update_tag()
    material.update_tag()
    material['endf_npr_capability_signature'] = capability_signature(bpy.context.scene)
    return True
