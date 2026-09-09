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

    def as_pointer(self):
        return id(self)


def flat_graph():
    texture = Node('TEX_IMAGE')
    emission = Node('EMISSION')
    source = NS(from_node=texture, from_socket=NS(name='Color'))
    target = NS(to_node=emission, to_socket=NS(name='Color'))
    product = Node('MIX_RGB', bl_idname='ShaderNodeMixRGB', blend_type='MULTIPLY', use_clamp=False,
        inputs=[NS(is_linked=False, default_value=1.0), NS(links=[source]), NS(is_linked=False)],
        outputs={'Color': NS(links=[target])})
    return NS(nodes=[texture, emission, product, Node('BSDF_TRANSPARENT'), Node('MIX_SHADER')])


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
        mat.node_tree.nodes[2].blend_type = 'ADD'
        with self.assertRaisesRegex(RuntimeError, 'albedo contract changed'):
            self.npc.sync(mat)

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
            def instantiate(self, *args, **kwargs): return mat, 1
            def _shader_name(self, builder, props): return 'HGRP/CharacterNPR'
            def _transparent_base_output(self, material, image, props):
                material.node_tree = flat_graph()
                events.append('flat graph built')
            def _param_write(self, material):
                self.assert_marker = material.get('endf_npr_transparent_base')
                npc.sync(material)
                events.append('parameters synchronized')
                return 1
        provider = load_method(ROOT / 'vendor/ruri_npr/ruri_endfield.py', 'provider')
        override = load_method(ROOT / 'ruri_adapter.py', '_transparent_base_output')
        cls = ast.ClassDef(name='Candidate', bases=[ast.Name(id='Base', ctx=ast.Load())],
                           keywords=[], body=[provider, override], decorator_list=[])
        env = {'Base': Base, '__name__': 'contract_package.adapter', '__package__': 'contract_package',
               'bpy': self.bpy, '_mixed': lambda value: dict(value or {}), 'LINK_TEMPLATES_OPTION': 'link'}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), '<actual provider and override>', 'exec'), env)
        props = NS(name=mat.name, floats={npc.ENABLE: 0, '_SurfaceType': 1},
                   colors={name: [1, 1, 1, 1] for name in npc.COLORS}, texture_st={}, shader_ref={})
        stack = env['Candidate']()
        self.assertIs(stack.provider(NS(options={}), props), mat)
        self.assertTrue(stack.assert_marker)
        self.assertEqual(events, ['flat graph built', 'parameters synchronized'])

    def test_failed_provider_cleanup_preserves_prior_unused_and_shared_data(self):
        old = Material()
        failed = Material()
        shared = Material()
        shared.users = 1
        old_group = Material(endf_npc_customization=self.npc.STAMP)
        failed_group = Material(endf_npc_customization=self.npc.STAMP)
        shared_group = Material(endf_npr_source_group='shared')
        self.bpy.data.materials = Datablocks([old, failed, shared])
        self.bpy.data.node_groups = Datablocks([old_group, failed_group, shared_group])
        method = load_method(ROOT / 'ruri_adapter.py', '_discard_failed_materials')
        env = {'bpy': self.bpy}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual cleanup>', 'exec'), env)
        env['_discard_failed_materials']({old.as_pointer()}, {old_group.as_pointer()})
        self.assertEqual([id(x) for x in self.bpy.data.materials], [id(old), id(shared)])
        self.assertEqual([id(x) for x in self.bpy.data.node_groups], [id(old_group), id(shared_group)])


if __name__ == '__main__':
    unittest.main()
