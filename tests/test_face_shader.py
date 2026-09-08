"""Pure-Python tests of shader ownership, change suppression and restoration."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


class Owned(dict):
    def as_pointer(self):
        return id(self)

    __hash__ = object.__hash__


class ShaderTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(sys.modules)
        bpy = types.ModuleType('bpy')
        handlers = types.ModuleType('bpy.app.handlers')
        handlers.persistent = lambda function: function
        bpy.types = types.SimpleNamespace(Operator=object)
        bpy.data = types.SimpleNamespace(materials=[], objects=[])
        sys.modules['bpy'] = bpy
        sys.modules['bpy.app.handlers'] = handlers
        package = types.ModuleType('face_test_package')
        package.__path__ = []
        sys.modules[package.__name__] = package
        controls = types.ModuleType(package.__name__ + '.face_controls')
        controls.DATA = 'descriptor'
        controls.descriptor = json.loads
        controls.property_name = lambda i: 'ctrl' + str(i)
        controls.curves = lambda action: []
        sys.modules[controls.__name__] = controls
        panel = types.ModuleType(package.__name__ + '.material_panel')
        self.writes = []
        stack = types.SimpleNamespace(panel_read=lambda mat: {'values': mat['values']})
        def write(mat, row, value):
            self.writes.append((mat.name, row['name'], value))
            mat['values'][row['name']] = value
        stack.panel_write = write
        panel.stack_for = lambda mat: stack
        panel.groups_for = lambda stack, mat: [{'rows': [{'name': name, 'kind': 'VALUE'} for name in mat['values']]}]
        panel.editable = lambda mat: True
        panel.value_of = lambda stack, mat, row, snapshot: snapshot['values'][row['name']]
        panel.resolve = lambda material, parameter: (next(m for m in bpy.data.materials if m.name == material), stack, {'name': parameter, 'kind': 'VALUE'})
        sys.modules[panel.__name__] = panel
        path = Path(__file__).resolve().parents[1] / 'endfield_bridge/face_shader.py'
        spec = importlib.util.spec_from_file_location(package.__name__ + '.face_shader', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        data = {'controls': [{'name': name, 'partType': 32, 'shader': {'parameter': name, 'rendererMask': -1,
                'defaultValue': 0, 'blendMode': 0, 'vectorIndex': 0}} for name in ('_EmotionBlend', '_EmotionIndex')]}
        self.rig = Owned(sora_instance='first', descriptor=json.dumps(data), ctrl0=.25, ctrl1=2.0)
        self.first = Owned(sora_instance='first', values={'_EmotionBlend': .7, '_EmotionIndex': 3.0})
        self.first.name = 'First'
        self.other = Owned(sora_instance='second', values={'_EmotionBlend': .8, '_EmotionIndex': 4.0})
        self.other.name = 'Other'
        bpy.data.materials.extend((self.first, self.other))
        self.bpy = bpy

    def tearDown(self):
        for name in list(sys.modules):
            if name not in self.saved:
                del sys.modules[name]
        sys.modules.update(self.saved)

    def test_changed_values_only_and_restore(self):
        m = self.module
        m.set_enabled(self.rig, True)
        self.assertEqual(self.first['values'], {'_EmotionBlend': .25, '_EmotionIndex': 2.0})
        self.assertEqual(self.other['values'], {'_EmotionBlend': .8, '_EmotionIndex': 4.0})
        count = len(self.writes)
        for _ in range(5):
            self.assertEqual(m.sync(self.rig), 0)
        self.assertEqual(len(self.writes), count)
        self.rig['ctrl0'] = .5
        self.assertEqual(m.sync(self.rig), 1)
        m.set_enabled(self.rig, False)
        self.assertEqual(self.first['values'], {'_EmotionBlend': .7, '_EmotionIndex': 3.0})
        self.assertNotIn(m.SNAPSHOT, self.first)

    def test_unknown_mapping_rejects_before_writes(self):
        data = json.loads(self.rig['descriptor'])
        data['controls'][0]['shader']['blendMode'] = 5
        self.rig['descriptor'] = json.dumps(data)
        with self.assertRaises(ValueError):
            self.module.set_enabled(self.rig, True)
        self.assertFalse(self.writes)
        self.assertNotIn(self.module.SNAPSHOT, self.first)

    def test_stale_evaluated_copy_cannot_revert_unanimated_controls(self):
        self.rig.evaluated_get = lambda graph: Owned(ctrl0=0.0, ctrl1=0.0)
        m = self.module
        m.set_enabled(self.rig, True)
        count = len(self.writes)
        for _ in range(5):
            self.assertEqual(m.sync(self.rig, object()), 0)
            self.assertEqual(m.sync(self.rig), 0)
        self.assertEqual(len(self.writes), count)
        self.assertEqual(self.first['values'], {'_EmotionBlend': .25, '_EmotionIndex': 2.0})

    def test_only_animated_shader_property_uses_evaluated_value(self):
        self.rig.animation_data = types.SimpleNamespace(
            drivers=[types.SimpleNamespace(data_path='["ctrl0"]', mute=False)], action=None, nla_tracks=[])
        self.rig.evaluated_get = lambda graph: Owned(ctrl0=.625, ctrl1=0.0)
        self.bpy.context = types.SimpleNamespace(evaluated_depsgraph_get=lambda: object())
        m = self.module
        m.set_enabled(self.rig, True)
        count = len(self.writes)
        for _ in range(5):
            self.assertEqual(m.sync(self.rig, object()), 0)
            self.assertEqual(m.sync(self.rig), 0)
        self.assertEqual(len(self.writes), count)
        self.assertEqual(self.first['values'], {'_EmotionBlend': .625, '_EmotionIndex': 2.0})

    def test_shared_material_rejects_before_writes(self):
        outside = Owned(sora_instance='second')
        outside.type = 'MESH'
        outside.data = types.SimpleNamespace(materials=[self.first])
        self.bpy.data.objects.append(outside)
        with self.assertRaises(ValueError):
            self.module.set_enabled(self.rig, True)
        self.assertFalse(self.writes)

    def test_external_parameter_edit_is_reapplied_without_repeated_writes(self):
        m = self.module
        m.set_enabled(self.rig, True)
        m.sync(self.rig)
        self.first['values']['_EmotionBlend'] = .91
        self.assertEqual(m.sync(self.rig), 1)
        self.assertEqual(self.first['values']['_EmotionBlend'], .25)
        count = len(self.writes)
        for _ in range(5):
            self.assertEqual(m.sync(self.rig), 0)
        self.assertEqual(len(self.writes), count)

    def test_restore_preflights_all_rows_before_any_write(self):
        self.other['sora_instance'] = 'first'
        m = self.module
        m.set_enabled(self.rig, True)
        del self.other['values']['_EmotionIndex']
        count = len(self.writes)
        with self.assertRaises((ValueError, KeyError)):
            m.set_enabled(self.rig, False)
        self.assertEqual(len(self.writes), count)
        self.assertTrue(self.rig[m.ENABLED])
        self.assertIn(m.SNAPSHOT, self.first)
        self.assertIn(m.SNAPSHOT, self.other)

    def test_missing_restore_target_preserves_recoverable_state(self):
        self.other['sora_instance'] = 'first'
        m = self.module
        m.set_enabled(self.rig, True)
        self.bpy.data.materials.remove(self.other)
        count = len(self.writes)
        with self.assertRaises(ValueError):
            m.set_enabled(self.rig, False)
        self.assertEqual(len(self.writes), count)
        self.assertTrue(self.rig[m.ENABLED])
        self.assertIn(m.TARGETS, self.rig)

    def test_renamed_material_restores_by_persistent_identity(self):
        m = self.module
        m.set_enabled(self.rig, True)
        self.first.name = 'Renamed by user'
        m.set_enabled(self.rig, False)
        self.assertEqual(self.first['values'], {'_EmotionBlend': .7, '_EmotionIndex': 3.0})

    def test_linked_restore_target_rejects_before_any_write(self):
        self.other['sora_instance'] = 'first'
        m = self.module
        m.set_enabled(self.rig, True)
        self.other['readonly'] = True
        sys.modules['face_test_package.material_panel'].editable = lambda mat: not mat.get('readonly')
        count = len(self.writes)
        with self.assertRaises(ValueError):
            m.set_enabled(self.rig, False)
        self.assertEqual(len(self.writes), count)
        self.assertTrue(self.rig[m.ENABLED])

    def test_new_shared_user_rejects_restore_before_any_write(self):
        m = self.module
        m.set_enabled(self.rig, True)
        outside = Owned(sora_instance='second')
        outside.type = 'MESH'
        outside.data = types.SimpleNamespace(materials=[self.first])
        self.bpy.data.objects.append(outside)
        count = len(self.writes)
        with self.assertRaises(ValueError):
            m.set_enabled(self.rig, False)
        self.assertEqual(len(self.writes), count)
        self.assertIn(m.SNAPSHOT, self.first)


if __name__ == '__main__':
    unittest.main()
