"""Host-independent checks for exact component transport and unchanged writes."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location(
    'cycles_uniforms', Path(__file__).resolve().parents[1] / 'endfield_bridge/cycles_uniforms.py')
uniforms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uniforms)


class Node(dict):
    def __init__(self, kind):
        super().__init__({uniforms.KIND: kind})
        self.inputs = [SimpleNamespace(default_value=0.0) for _ in range(3)]
        self.outputs = [SimpleNamespace(default_value=0.0)]


class UniformTests(unittest.TestCase):
    def test_signed_hdr_vector_is_exact_and_second_write_is_unchanged(self):
        node = Node('XYZ')
        cell = [3.5, -0.25, 0.125, 0.625]
        self.assertTrue(uniforms._assign(node, cell, 0))
        self.assertEqual([s.default_value for s in node.inputs], cell[:3])
        self.assertFalse(uniforms._assign(node, cell, 0))

    def test_packed_float_and_alpha_use_explicit_component(self):
        for kind, component in [('F', 2), ('W', 3)]:
            node = Node(kind)
            cell = [100.0, 200.0, -0.5, 0.625]
            self.assertTrue(uniforms._assign(node, cell, component))
            self.assertEqual(node.outputs[0].default_value, cell[component])
            self.assertFalse(uniforms._assign(node, cell, component))


if __name__ == '__main__':
    unittest.main()
