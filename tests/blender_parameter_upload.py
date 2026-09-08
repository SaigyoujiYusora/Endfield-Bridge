"""Parent-run EEVEE dirty-pixel and clean/stale callback regression."""
import bpy
from endfield_bridge import face_controls as fc, face_shader as fs, ruri_adapter


def run(rig):
    scene = bpy.context.scene
    assert scene.render.engine == 'BLENDER_EEVEE'
    ruri_adapter.refresh_engine(scene)
    was_enabled = bool(rig.get(fs.ENABLED))
    controls = fs.mappings(rig)
    previous = {fc.property_name(i): rig[fc.property_name(i)] for i, _ in controls}
    try:
        fs.set_enabled(rig, True)
        for i, name in controls:
            rig[fc.property_name(i)] = .375 if previous[fc.property_name(i)] != .375 else .625
        rig.update_tag()
        bpy.context.view_layer.update()
        fs.sync(rig)
        material_users = {slot.material for obj in scene.objects for slot in obj.material_slots if slot.material}
        rows = [(mat, stack, row, index) for mat, stack, row, index in fs.targets(rig, controls) if mat in material_users]
        assert rows
        used_stacks = {stack for _, stack, _, _ in rows}
        assert any(stack._flush_queued[0] for stack in used_stacks)
        def sample(material, stack, row):
            definition = next(p for p in stack.part(material['ruri_uber_part'])['params'] if p[0] == row['name'])
            _, _, texel, component, _, _ = definition
            column = int(material['ruri_param_col'])
            images = {n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.label == 'RuriMatParam' and n.image}
            assert images, 'EEVEE material has no bound parameter image'
            return [(image.pixels[(texel*image.size[0]+column)*4+component], float(stack._mirror[texel,column,component])) for image in images]
        assert any(abs(image-cpu) > 1e-6 for mat, stack, row, _ in rows for image,cpu in sample(mat,stack,row))
        ruri_adapter.render_view(scene)  # Same callback: no opportunity for timers.
        assert all(abs(image-cpu) < 1e-6 for mat, stack, row, _ in rows for image,cpu in sample(mat,stack,row))
        for stack in used_stacks:
            assert not stack._flush_queued[0]
            def unexpected():
                raise AssertionError('Clean/stale callback uploaded an unchanged table')
            stack._param_flush = unexpected
            try:
                stack._endf_param_flush_callback()
                assert ruri_adapter.flush_pending_tables(scene) == 0
            finally:
                del stack._param_flush
        print('EEVEE_UPLOAD_BOUNDARY_OK: dirty pixels uploaded; clean and stale callbacks write nothing')
    finally:
        for prop, value in previous.items():
            rig[prop] = value
        rig.update_tag()
        fs.sync(rig)
        if not was_enabled:
            fs.set_enabled(rig, False)
        ruri_adapter.flush_pending_tables(scene)
