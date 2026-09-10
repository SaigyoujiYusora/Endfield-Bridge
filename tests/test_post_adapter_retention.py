"""Host safety regressions for the destructive disable boundary."""
import importlib.util,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('post_graph_retention',Path(__file__).parents[1]/'endfield_bridge/post_graph.py')
graph=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'bpy':SimpleNamespace()}):spec.loader.exec_module(graph)
class Tree(dict):
    name='fixture'
    nodes=()
    animation_data=None
class RetentionTests(unittest.TestCase):
    def fixture(self):
        return Tree({graph.ADDED:json.dumps({'nodes':['adapter'],'mode':'appended'}),
                     graph.ADAPTER:'original',graph.COMPLETE:'complete','user property':42})
    def test_changed_adapter_preserves_without_node_removal(self):
        tree=self.fixture()
        with patch.object(graph,'signature',return_value='edited'):
            self.assertIs(graph.retain_edited_upstream(tree),tree)
        self.assertEqual(tree['user property'],42)
        self.assertIn('adapter retained',tree.name)
    def test_old_missing_baseline_preserves(self):
        tree=self.fixture();del tree[graph.ADAPTER]
        with patch.object(graph,'signature',side_effect=AssertionError('must not trust missing baseline')):
            graph.retain_edited_upstream(tree)
        self.assertEqual(tree['user property'],42)
    def test_full_graph_edit_detection(self):
        tree=self.fixture()
        with patch.object(graph,'signature',return_value='adapter socket edit'):
            self.assertTrue(graph.upstream_changed(tree))
    def test_adapter_animation_paths_are_recognized(self):
        tree=self.fixture();tree.nodes=[SimpleNamespace(name='adapter',path_from_id=lambda:'nodes["adapter"]')]
        with patch.object(graph,'_animation',return_value={'action':{'curves':[{'path':'nodes["adapter"].inputs[0].default_value'}]}}):
            self.assertTrue(graph._adapter_animated(tree,{'adapter'}))
if __name__=='__main__':unittest.main()
