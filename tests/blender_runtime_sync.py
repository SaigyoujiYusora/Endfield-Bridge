"""MCP regression helpers; run exercise_lights(), exercise_rig(mesh) after reload.

Temporary light scenes are deleted and the active scene's light table restored.
Rig names and pose are restored in finally; use a disposable validation import.
"""
import bpy
from endfield_bridge import ruri_adapter as adapter, runtime_sync as sync
from endfield_bridge.vendor.ruri_npr import ruri_endfield as runtime


def exercise_lights():
    adapter.stacks()
    original_scene = bpy.context.scene
    scenes, objects, lights = [], [], []
    counts = {'upload': 0, 'store': 0}
    uploaded, stored = runtime._image_uploaded, runtime._image_stored
    def upload(image):
        counts['upload'] += 1
        return uploaded(image)
    def store(image):
        counts['store'] += 1
        return stored(image)
    runtime._image_uploaded, runtime._image_stored = upload, store
    def payload():
        return tuple(runtime._light_table_image().pixels)
    try:
        for index in range(2):
            scene = bpy.data.scenes.new('ENDF sync regression ' + str(index))
            scenes.append(scene)
            for kind in ('SUN', 'POINT'):
                data = bpy.data.lights.new('ENDF sync ' + kind, kind)
                data.energy = 2.0 + index
                obj = bpy.data.objects.new(data.name, data)
                scene.collection.objects.link(obj)
                objects.append(obj)
                lights.append(data)
        results = []
        for scene in (scenes[0], scenes[1], scenes[0]):
            with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                bpy.context.view_layer.update()
                sync.refresh_lights(runtime, scene)
                first = payload()
                before = dict(counts)
                for _ in range(5):
                    adapter.update(scene, bpy.context.evaluated_depsgraph_get())
                assert counts == before, ('unchanged uploaded', before, counts)
                sun = next(o for o in scene.objects if o.data.type == 'SUN')
                point = next(o for o in scene.objects if o.data.type == 'POINT')
                for obj, change in ((sun, lambda o: setattr(o.data, 'energy', o.data.energy + 1)),
                                    (sun, lambda o: setattr(o.data, 'color', (0.3, 0.6, 0.9))),
                                    (sun, lambda o: setattr(o, 'rotation_euler', (0.2, 0.4, 0.1))),
                                    (point, lambda o: setattr(o, 'location', (1, 2, 3))),
                                    (point, lambda o: setattr(o, 'hide_viewport', True)),
                                    (point, lambda o: setattr(o, 'hide_viewport', False))):
                    old = payload()
                    # Repeat visit starts from the previous edits: reset values
                    # before each mutation to make every case a real change.
                    if scene == scenes[0] and len(results) == 2:
                        break
                    change(obj)
                    bpy.context.view_layer.update()
                    sync.refresh_lights(runtime, scene)
                    assert payload() != old, ('light did not respond', obj.name)
                scene.collection.objects.unlink(point)
                bpy.context.view_layer.update()
                sync.refresh_lights(runtime, scene)
                removed = payload()
                scene.collection.objects.link(point)
                bpy.context.view_layer.update()
                sync.refresh_lights(runtime, scene)
                assert payload() != removed, 'removed/new light did not respond'
                results.append({'scene': scene.name, 'unchanged_uploads': 0,
                                'initial_payload_sum': sum(first)})
        assert results[0]['initial_payload_sum'] != results[1]['initial_payload_sum']
        return {'scenes': results, 'calls': counts}
    finally:
        runtime._image_uploaded, runtime._image_stored = uploaded, stored
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for light in lights:
            bpy.data.lights.remove(light)
        for scene in scenes:
            bpy.data.scenes.remove(scene)
        sync.reset()
        with bpy.context.temp_override(scene=original_scene, view_layer=original_scene.view_layers[0]):
            sync.refresh_lights(runtime, original_scene)


def exercise_rig(mesh):
    scene = bpy.context.scene
    stack = next(s for s in adapter.stacks() for m in mesh.data.materials
                 if m and m.get('ruri_uber_stack') == s.PANEL_KEY
                 and m.get('ruri_uber_part') in (s.RIG.get('parts') or []))
    mat = next(m for m in mesh.data.materials if m and m.get('ruri_uber_stack') == stack.PANEL_KEY
               and m.get('ruri_uber_part') in stack.RIG['parts'])
    arm = stack._rig_basis_armature(mesh)
    bone = arm.data.bones[stack.rig_resolve_bone(arm, stack.rig_bone_of(mat))]
    names = (mesh.name, arm.name, bone.name)
    pose = arm.pose.bones[bone.name]
    old_basis = pose.matrix_basis.copy()
    def values(obj):
        return tuple(tuple(obj[runtime.RIG_OBJECT_PROP + str(i)]) for i in range(3))
    inactive = {o: values(o) for o in bpy.data.objects
                if o.name not in scene.objects and runtime.RIG_OBJECT_PROP + '0' in o}
    try:
        adapter.update(scene, bpy.context.evaluated_depsgraph_get())

        before = values(mesh)
        mesh.name += ' sync rename'
        arm.name += ' sync rename'
        bone.name += ' sync rename'
        from mathutils import Matrix
        pose.matrix_basis = old_basis @ Matrix.Rotation(0.3, 4, 'X')
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        adapter.update(scene, graph)
        after = values(mesh)
        assert after != before, 'renamed head basis froze'
        assert values(mesh.evaluated_get(graph)) == after, 'evaluated basis lags original'
        assert all(values(o) == expected for o, expected in inactive.items()), 'inactive rig was written'
        return {'mesh': mesh.name, 'renamed_basis_live': True, 'inactive_checked': len(inactive)}
    finally:
        pose.matrix_basis = old_basis
        mesh.name, arm.name, bone.name = names
        bpy.context.view_layer.update()
        adapter.update(scene, bpy.context.evaluated_depsgraph_get())


def exercise_other_scene():
    """Exercise an explicit other-scene render callback from the active scene.

Uses a copy of a real imported material so actual capability assembly runs.
The passed depsgraph deliberately belongs to the original scene.
"""
    original_scene = bpy.context.scene
    original_window_scene = bpy.context.window.scene if bpy.context.window else None
    original_graph = bpy.context.evaluated_depsgraph_get()
    source = next(m for m in bpy.data.materials
                  if m.get('sora_render_pipeline') == 'ENDF NPR-Shader'
                  and not m.get('endf_npr_transparent_base')
                  and any(m.get('ruri_uber_stack') == s.PANEL_KEY
                          and m.get('ruri_uber_part') in s.m['parts'] for s in adapter.stacks()))
    original_materials = {m: (m.get('endf_npr_capability_signature'),
                              tuple(n.as_pointer() for n in m.node_tree.nodes))
                          for o in original_scene.objects for slot in o.material_slots
                          if (m := slot.material) is not None and m.node_tree}
    original_props = {o: tuple(tuple(o[runtime.RIG_OBJECT_PROP + str(i)]) for i in range(3))
                      for o in original_scene.objects if runtime.RIG_OBJECT_PROP + '0' in o}
    scene = bpy.data.scenes.new('ENDF cross-scene regression')
    world = bpy.data.worlds.new('ENDF cross-scene world')
    scene.world = world
    world.color = (0.07, 0.19, 0.31)
    scene.render.engine = original_scene.render.engine
    light = bpy.data.lights.new('ENDF cross-scene sun', 'SUN')
    light.energy = 7.25
    sun = bpy.data.objects.new(light.name, light)
    scene.collection.objects.link(sun)
    mesh = bpy.data.meshes.new('ENDF cross-scene mesh')
    obj = bpy.data.objects.new(mesh.name, mesh)
    scene.collection.objects.link(obj)
    material = source.copy()
    mesh.materials.append(material)
    material['endf_npr_capability_signature'] = 'force cross-scene rebuild'
    observed = []
    original_rewire = adapter.rewire_material
    def rewire(stack, target):
        observed.append((target, bpy.context.scene, bpy.context.scene.world,
                         runtime._scene_sun(), adapter.capability_signature(bpy.context.scene)))
        return original_rewire(stack, target)
    adapter.rewire_material = rewire
    try:
        assert adapter._matching_depsgraph(scene, original_graph) is None
        # Ensure fresh visibility before the callback. The callback itself must
        # establish its own context after this temporary override has ended.
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            bpy.context.view_layer.update()
        adapter.render_view(scene)
        adapter.update(scene, original_graph)
        assert observed and all(m == material and s == scene and w == world and l == sun
                                for m, s, w, l, signature in observed), observed
        assert material['endf_npr_capability_signature'] == adapter.capability_signature(scene)
        image = runtime._light_table_image()
        assert abs(image.pixels[runtime.LIGHT_TABLE_COLS * 4] - 7.25) < 1e-6
        assert bpy.context.scene == original_scene
        assert not bpy.context.window or bpy.context.window.scene == original_window_scene
        for mat, expected in original_materials.items():
            assert (mat.get('endf_npr_capability_signature'),
                    tuple(n.as_pointer() for n in mat.node_tree.nodes)) == expected, mat.name
        for target, expected in original_props.items():
            assert tuple(tuple(target[runtime.RIG_OBJECT_PROP + str(i)]) for i in range(3)) == expected
        return {'other_scene_world_sun': True, 'wrong_depsgraph_rejected': True,
                'active_materials_preserved': len(original_materials),
                'active_rigs_preserved': len(original_props), 'context_restored': True}
    finally:
        adapter.rewire_material = original_rewire
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.objects.remove(sun, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.data.materials.remove(material)
        bpy.data.lights.remove(light)
        bpy.data.scenes.remove(scene)
        bpy.data.worlds.remove(world)
        sync.reset()
        adapter.update(original_scene, original_graph)
        adapter.update_view(bpy.context)
