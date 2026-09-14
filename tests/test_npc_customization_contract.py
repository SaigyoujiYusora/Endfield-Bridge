"""Host regressions execute the actual provider order with a small node contract fake."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'endfield_bridge'


class Node(dict):
    def __init__(self, kind, **attrs):
        super().__init__()
        self.type = kind
        self.bl_idname = attrs.pop('bl_idname', '')
        self.label = ''
        self.__dict__.update(attrs)


class Datablocks(list):
    def remove(self, item):
        del self[next(i for i, value in enumerate(self) if value is item)]


class Material(dict):
    name = 'Character transparent cloth'
    users = 0
    library = None
    surface_render_method = 'BLENDED'

    def as_pointer(self):
        return id(self)


class Sockets(list):
    def __getitem__(self, key):
        return next(x for x in self if x.name == key) if isinstance(key, str) else super().__getitem__(key)


def flat_graph():
    kinds = ('TEX_COORD', 'MAPPING', 'TEX_IMAGE', 'MIX_RGB', 'EMISSION',
             'MATH', 'BSDF_TRANSPARENT', 'MIX_SHADER', 'OUTPUT_MATERIAL')
    inputs = [[], ['Vector', 'Scale', 'Location'], ['Vector'], ['Fac', 'Color1', 'Color2'],
              ['Color'], ['Value', 'Value2'], [], ['Fac', 'Shader1', 'Shader2'], ['Surface']]
    outputs = [['UV'], ['Vector'], ['Color', 'Alpha'], ['Color'], ['Emission'], ['Value'], ['BSDF'], ['Shader'], []]
    nodes = [Node(k, inputs=Sockets(NS(name=x, default_value=1.0, links=[]) for x in ins),
                  outputs=Sockets(NS(name=x, links=[]) for x in outs)) for k, ins, outs in zip(kinds, inputs, outputs)]
    uv, mapping, image, tint, emission, alpha, transparent, mix, output = nodes
    image.label = '_BaseMap'
    image.image = NS(name='BaseMap')
    mapping.vector_type = 'POINT'
    tint.blend_type = alpha.operation = 'MULTIPLY'
    tint.use_clamp = alpha.use_clamp = False
    pairs = [(uv.outputs[0], mapping.inputs[0]), (mapping.outputs[0], image.inputs[0]),
             (image.outputs[0], tint.inputs[1]), (tint.outputs[0], emission.inputs[0]),
             (image.outputs[1], alpha.inputs[0]), (alpha.outputs[0], mix.inputs[0]),
             (transparent.outputs[0], mix.inputs[1]), (emission.outputs[0], mix.inputs[2]),
             (mix.outputs[0], output.inputs[0])]
    return NS(nodes=nodes, links=[NS(from_socket=a, to_socket=b) for a, b in pairs])


def load_method(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.bpy = ModuleType('bpy')
        self.bpy.data = NS(materials=NS(get=lambda name: None), node_groups=[])
        self.modules = patch.dict(sys.modules, {'bpy': self.bpy})
        self.modules.start()
        spec = importlib.util.spec_from_file_location('contract_package.npc_customization', ROOT / 'npc_customization.py')
        self.npc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.npc)
        sys.modules[spec.name] = self.npc

    def tearDown(self):
        self.modules.stop()

    def material(self, enable=0.0, transparent=True):
        mat = Material(ruri_uber_part='Standard', ruri_uber_floats={self.npc.ENABLE: enable},
            ruri_uber_colors={name: [1, 1, 1, 1] for name in self.npc.COLORS})
        mat['sora_native_shader_ref'] = '{"fileId": 1, "pathId": "-7822190029627442914"}'
        mat['sora_native_shader_id'] = '{"cab": "CAB-8e64a7d61483ea16539b04f304be9ed7", "pathId": "-7822190029627442914"}'
        mat.node_tree = flat_graph()
        if transparent:
            mat['endf_npr_transparent_base'] = True
        return mat

    def test_disabled_transparent_accepts_exact_graph(self):
        self.npc.sync(self.material())

    def test_enabled_transparent_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(RuntimeError, 'enabled customization is unsupported'):
            self.npc.sync(self.material(1))

    def test_flat_graph_without_lifecycle_marker_is_not_misidentified(self):
        with self.assertRaisesRegex(RuntimeError, 'expected one Standard s1'):
            self.npc.sync(self.material(transparent=False))

    def test_bad_marker_does_not_hide_missing_graph(self):
        mat = self.material()
        mat.node_tree.nodes.clear()
        with self.assertRaisesRegex(RuntimeError, 'graph contract changed'):
            self.npc.sync(mat)

    def test_wrong_albedo_does_not_pass_node_type_check(self):
        mat = self.material()
        mat.node_tree.nodes[3].blend_type = 'ADD'
        with self.assertRaisesRegex(RuntimeError, 'graph contract changed'):
            self.npc.sync(mat)

    def test_every_required_link_is_checked(self):
        for i in range(9):
            mat = self.material()
            del mat.node_tree.links[i]
            with self.assertRaisesRegex(RuntimeError, 'graph contract changed'):
                self.npc.validate_transparent_graph(mat)

    def test_transparent_color_and_alpha_refresh(self):
        mat = self.material()
        mat['ruri_uber_colors']['_BaseColor'] = [2.0, 0.3, 0.1, 0.4]
        self.npc.sync_transparent(mat)
        tint, alpha = self.npc.validate_transparent_graph(mat)
        self.assertEqual(tint.inputs[2].default_value, [2.0, 0.3, 0.1, 0.4])
        self.assertEqual(alpha.inputs[1].default_value, 0.4)

    def test_unknown_shader_reference_rejects_customization(self):
        mat = self.material(1, transparent=False)
        mat['sora_native_shader_id'] = '{"cab":"other", "pathId":1}'
        with self.assertRaisesRegex(RuntimeError, 'native shader identity'):
            self.npc.sync(mat)

    def test_relative_file_index_does_not_override_resolved_identity(self):
        mat = self.material(1, transparent=False)
        mat['sora_native_shader_ref'] = '{"fileId": 17, "pathId": "-7822190029627442914"}'
        self.npc.validate_shader_identity(mat)

    def test_unresolved_source_reference_is_not_a_cab_identity(self):
        mat = self.material(1, transparent=False)
        del mat['sora_native_shader_id']
        with self.assertRaisesRegex(RuntimeError, 'native shader identity'):
            self.npc.validate_shader_identity(mat)

    def test_nan_enable_is_rejected_even_for_transparent(self):
        with self.assertRaisesRegex(ValueError, 'invalid'):
            self.npc.sync(self.material(float('nan')))

    def test_enabled_standard_controls_follow_contract_for_any_asset_name(self):
        for name in ('Playable character', 'NPC engineer'):
            mat = self.material(1, transparent=False)
            mat.name = name
            tree = Material({self.npc.MARKER: self.npc.STAMP})
            tree.users = 1
            inputs = {self.npc.ENABLE: NS(default_value=0.0), 'F0_BaseMap': NS()}
            inputs.update({color: NS(default_value=(0.0, 0.0, 0.0)) for color in self.npc.COLORS})
            node = Node('GROUP', node_tree=tree, inputs=inputs)
            node['ruri_inst'] = 1
            mat.node_tree = NS(nodes=[node], update_tag=lambda: None)
            mat.update_tag = lambda: None
            self.npc.sync(mat)
            self.assertEqual(inputs[self.npc.ENABLE].default_value, 1.0)
            self.assertEqual(inputs[self.npc.COLORS[0]].default_value, (1.0, 1.0, 1.0))
            self.assertIs(node.node_tree, tree)

    def test_real_provider_marks_replacement_before_parameter_callback(self):
        npc = self.npc
        mat = self.material(transparent=False)
        events = []
        class Base:
            PART_META = {'Standard': {'transparent': False}}
            ST_SLOT = '_BaseMap'
            ST_NODE = 'ST'
            MATERIAL_NAME = 'default'
            host = {}
            def _variant(self, builder, props): return 'Standard', 0
            def _load_images(self, builder, props): return {'_BaseMap': NS(name='BaseMap')}
            def _cull_mode(self, props): return 0
            def _check_existing_templates(self, groups_only=False): return None
            def instantiate_steps(self, name, part, images=None, opaque=True, multiply_blend=False, cull=2.0, ownership=None):
                if ownership is not None:
                    ownership.material(mat)
                yield {'stage': 'Instantiating NPR material', 'detail': name}
                return mat, 1
            def _shader_name(self, builder, props): return 'HGRP/CharacterNPR'
            def _transparent_base_output(self, material, image, props):
                material.node_tree = flat_graph()
                events.append('flat graph built')
            def _param_write(self, material, ownership=None):
                self.assert_marker = material.get('endf_npr_transparent_base')
                npc.sync(material)
                events.append('parameters synchronized')
                return 1
        provider = load_method(ROOT / 'vendor/ruri_npr/ruri_endfield.py', 'provider')
        vendor_steps = load_method(ROOT / 'vendor/ruri_npr/ruri_endfield.py', 'provider_steps')
        adapter_steps = load_method(ROOT / 'ruri_adapter.py', 'provider_steps')
        override = load_method(ROOT / 'ruri_adapter.py', '_transparent_base_output')
        vendor = ast.ClassDef(name='Vendor', bases=[ast.Name(id='Base', ctx=ast.Load())],
                              keywords=[], body=[provider, vendor_steps], decorator_list=[])
        candidate = ast.ClassDef(name='Candidate', bases=[ast.Name(id='Vendor', ctx=ast.Load())],
                                 keywords=[], body=[adapter_steps, override], decorator_list=[])
        env = {'Base': Base, '__name__': 'contract_package.adapter', '__package__': 'contract_package',
               'bpy': self.bpy, '_mixed': lambda value: dict(value or {}), 'LINK_TEMPLATES_OPTION': 'link'}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[vendor, candidate], type_ignores=[])), '<actual provider and override>', 'exec'), env)
        props = NS(name=mat.name, floats={npc.ENABLE: 0, '_SurfaceType': 1},
                   colors={name: [1, 1, 1, 1] for name in npc.COLORS}, texture_st={}, shader_ref={})
        stack = env['Candidate']()
        self.assertIs(stack.provider(NS(options={}), props), mat)
        self.assertTrue(stack.assert_marker)
        self.assertEqual(events, ['flat graph built', 'parameters synchronized'])

    def test_panel_refusals_happen_before_vendor_mutation(self):
        method = load_method(ROOT / 'ruri_adapter.py', 'panel_write')
        env = {'__name__': 'contract_package.adapter', '__package__': 'contract_package'}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual panel guard>', 'exec'), env)
        mat = self.material()
        mat['ruri_uber_floats']['_SurfaceType'] = 1.0
        snapshot = dict(mat['ruri_uber_floats'])
        for row, value in [({'name': '_SurfaceType'}, 0), ({'name': self.npc.ENABLE}, 1)]:
            with self.assertRaises(ValueError):
                env['panel_write'](NS(), mat, row, value)
            self.assertEqual(dict(mat['ruri_uber_floats']), snapshot)

    def test_stale_cache_preflight_preserves_ids_and_names(self):
        method = load_method(ROOT / 'ruri_adapter.py', '_check_existing_templates')
        env = {'bpy': self.bpy}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual cache guard>', 'exec'), env)
        stack = NS(TEMPLATE_MAT='Template ', STAMP_KEY='stamp', STAMP='current', group_names=['Source'])
        old = Material(stamp='old')
        old.name = 'Template Standard'
        old.node_tree = flat_graph()
        self.bpy.data.materials = [old]
        with self.assertRaisesRegex(RuntimeError, 'explicit migration'):
            env['_check_existing_templates'](stack)
        self.assertEqual(old.name, 'Template Standard')
        self.bpy.data.materials = []
        group = Material(ruri_stamp='old')
        group.name, group.library = 'Source', None
        self.bpy.data.node_groups = [group]
        with self.assertRaisesRegex(RuntimeError, 'explicit migration'):
            env['_check_existing_templates'](stack)
        self.assertEqual(group.name, 'Source')

    def test_failed_provider_cleanup_preserves_prior_unused_and_shared_data(self):
        old = Material()
        failed = Material()
        shared = Material()
        shared.users = 1
        old_group = Material(endf_npc_customization=self.npc.STAMP)
        failed_group = Material(endf_npc_customization=self.npc.STAMP)
        shared_group = Material(endf_npr_source_group='shared')
        shared_group.users = 1
        self.bpy.data.materials = Datablocks([old, failed, shared])
        self.bpy.data.node_groups = Datablocks([old_group, failed_group, shared_group])
        ownership_class = next(node for node in ast.walk(
            ast.parse((ROOT / 'ruri_adapter.py').read_text(encoding='utf-8')))
            if isinstance(node, ast.ClassDef) and node.name == 'BuildOwnership')
        env = {'bpy': self.bpy}
        exec(compile(ast.Module(body=[ownership_class], type_ignores=[]), '<actual cleanup>', 'exec'), env)
        # cleanup is now the per-build ownership record: it only frees datablocks
        # this attempt created, and only while unused and not from a library.
        ownership = env['BuildOwnership']()
        ownership.material(failed)
        ownership.group(failed_group)
        # A datablock the failed attempt did create but another consumer already
        # shares must survive the rollback, exactly like the old protected set.
        ownership.material(shared)
        ownership.group(shared_group)
        ownership.release()
        self.assertEqual([id(x) for x in self.bpy.data.materials], [id(old), id(shared)])
        self.assertEqual([id(x) for x in self.bpy.data.node_groups], [id(old_group), id(shared_group)])


if __name__ == '__main__':
    unittest.main()
