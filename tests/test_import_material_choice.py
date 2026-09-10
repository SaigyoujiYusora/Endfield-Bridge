"""Import mode remains available while no instance conversion operator is exposed."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'endfield_bridge'


class Layout:
    def __init__(self):
        self.labels = []

    def label(self, **kwargs):
        self.labels.append(kwargs['text'])


class ImportMaterialChoiceTests(unittest.TestCase):
    def test_material_page_is_read_only_for_mode(self):
        tree = ast.parse((ROOT / 'render_modes.py').read_text(encoding='utf8'))
        draw = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'draw')
        namespace = {'selected_collection': lambda context: {'sora_render_mode': 'BASIC'}, 'MODE': 'sora_render_mode'}
        exec(compile(ast.Module(body=[draw], type_ignores=[]), '<material-status>', 'exec'), namespace)
        layout = Layout()
        namespace['draw'](layout, None)
        self.assertEqual(layout.labels, ['Current instance: BASIC'])
        self.assertFalse(any(isinstance(node, ast.ClassDef) for node in tree.body))

    def test_import_choices_and_builder_handoff_are_preserved(self):
        tree = ast.parse((ROOT / '__init__.py').read_text(encoding='utf8'))
        settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'SORA_Settings')
        prop = next(node.annotation for node in settings.body if isinstance(node, ast.AnnAssign) and node.target.id == 'material_mode')
        args = {keyword.arg: ast.literal_eval(keyword.value) for keyword in prop.keywords}
        self.assertEqual(args['default'], 'RURI')
        self.assertEqual({item[0] for item in args['items']}, {'RURI', 'BASIC'})
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertFalse(any(isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == 'render_modes' and call.func.attr in {'register', 'unregister'} for call in calls))
        self.assertTrue(any(isinstance(call.func, ast.Attribute) and call.func.attr == 'prop' and len(call.args) > 1 and isinstance(call.args[1], ast.Constant) and call.args[1].value == 'material_mode' for call in calls))
        scene = ast.parse((ROOT / 'scene.py').read_text(encoding='utf8'))
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'initialize' for node in ast.walk(scene)))


if __name__ == '__main__':
    unittest.main()
