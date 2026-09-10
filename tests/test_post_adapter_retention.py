"""Host safety regressions for the destructive disable boundary."""
import importlib.util,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('post_graph_retention',Path(__file__).parents[1]/'endfield_bridge/post_graph.py')
graph=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'bpy':SimpleNamespace(types=SimpleNamespace(ID=type('ID',(),{})))}):spec.loader.exec_module(graph)
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

class AdapterScopeTests(unittest.TestCase):
    def fixture(self):
        class Node(dict):
            def path_from_id(self):return 'nodes['+json.dumps(self.name)+']'
            def __eq__(self,other):return self is other
        class Nodes(list):
            def get(self,name):return next((n for n in self if n.name==name),None)
        tree=Tree({'driven_value':.2})
        tree.interface=SimpleNamespace(items_tree=[]);tree.links=[]
        tree.nodes=Nodes()
        for name in ('upstream','adapter'):
            node=Node();node.name=name;node.bl_idname='FixtureNode';node.mute=False
            node.inputs=[];node.outputs=[];node.location=[0,0];node.width=100
            node.label='';node.hide=False;node.parent=None
            tree.nodes.append(node)
        return tree

    def curve(self,node):
        return SimpleNamespace(data_path='nodes['+json.dumps(node)+'].inputs[0].default_value',
                               array_index=0,keyframe_points=[],modifiers=[])

    def action(self,*nodes,layered=False):
        curves=[self.curve(n) for n in nodes]
        bag=SimpleNamespace(fcurves=curves)
        layer=SimpleNamespace(strips=[SimpleNamespace(channelbags=[bag])])
        return SimpleNamespace(fcurves=[] if layered else curves,layers=[layer] if layered else [])

    def animation(self,action=None,drivers=(),nla=()):
        return SimpleNamespace(action=action,drivers=drivers,
            nla_tracks=[SimpleNamespace(strips=[SimpleNamespace(action=a)]) for a in nla])

    def driver(self,destination,target):
        curve=self.curve(destination)
        variable=SimpleNamespace(targets=[SimpleNamespace(id=None,data_path=self.curve(target).data_path)])
        curve.driver=SimpleNamespace(variables=[variable])
        return curve

    def test_tree_property_and_interface_edits_do_not_change_adapter_signature(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}),patch.object(graph.bpy,'types',SimpleNamespace(ID=type('ID',(),{})),create=True):
            baseline=graph.adapter_signature(tree,{'adapter'})
            whole=graph.signature(tree)
            tree['driven_value']=.6
            tree.interface.items_tree.append(SimpleNamespace(name='User output',in_out='OUTPUT',socket_type='NodeSocketFloat'))
            tree.nodes[0].label='User edited upstream'
            self.assertEqual(graph.adapter_signature(tree,{'adapter'}),baseline)
            self.assertNotEqual(graph.signature(tree),whole)
            tree.nodes[1]['edited']=42
            self.assertNotEqual(graph.adapter_signature(tree,{'adapter'}),baseline)

    def test_upstream_legacy_layered_nla_and_drivers_are_not_adapter_animation(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}):
            for layered in (False,True):
                tree.animation_data=self.animation(self.action('upstream',layered=layered),
                    [self.driver('upstream','adapter')],[self.action('upstream',layered=layered)])
                self.assertFalse(graph._adapter_animated(tree,{'adapter'}))

    def test_mixed_action_curves_detect_adapter_destinations(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}):
            for layered in (False,True):
                tree.animation_data=self.animation(self.action('upstream','adapter',layered=layered))
                self.assertTrue(graph._adapter_animated(tree,{'adapter'}))

    def test_mixed_nla_curves_detect_adapter_destinations(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}):
            tree.animation_data=self.animation(self.action('upstream'),nla=[self.action('upstream','adapter',layered=True)])
            self.assertTrue(graph._adapter_animated(tree,{'adapter'}))

    def test_mixed_driver_destinations_detect_adapter_even_with_upstream_target(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}):
            tree.animation_data=self.animation(self.action('upstream'),
                [self.driver('upstream','upstream'),self.driver('adapter','upstream')])
            self.assertTrue(graph._adapter_animated(tree,{'adapter'}))

    def test_legacy_adapter_digest_is_not_reinterpreted(self):
        tree=Tree({graph.ADDED:json.dumps({'nodes':['adapter']}),
                   'endf_npr_post_adapter_signature':'legacy digest'})
        with patch.object(graph,'adapter_signature',side_effect=AssertionError('legacy scope is unknown')):
            graph.retain_edited_upstream(tree)
        self.assertIn('adapter retained',tree.name)

    def test_disable_removes_unchanged_adapter_with_upstream_property_and_animation_edits(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}):
            tree[graph.ADAPTER]=graph.adapter_signature(tree,{'adapter'})
            tree[graph.ADDED]=json.dumps({'nodes':['adapter'],'output':['missing','Image']})
            tree['driven_value']=.6;tree.nodes[0].label='Edited upstream'
            tree.animation_data=self.animation(self.action('upstream',layered=True),
                [self.driver('upstream','upstream')],[self.action('upstream')])
            animation_before=graph._animation(tree)
            graph.retain_edited_upstream(tree)
            self.assertEqual([n.name for n in tree.nodes],['upstream'])
            self.assertEqual(tree['driven_value'],.6)
            self.assertEqual(tree.nodes[0].label,'Edited upstream')
            self.assertEqual(graph._animation(tree),animation_before)

    def test_disable_retains_adapter_with_mixed_animation(self):
        tree=self.fixture()
        with patch.object(graph,'_settings',return_value={}):
            tree[graph.ADAPTER]=graph.adapter_signature(tree,{'adapter'})
            tree[graph.ADDED]=json.dumps({'nodes':['adapter'],'output':['missing','Image']})
            tree.animation_data=self.animation(self.action('upstream','adapter',layered=True),
                [self.driver('upstream','upstream'),self.driver('adapter','upstream')])
            animation_before=graph._animation(tree)
            graph.retain_edited_upstream(tree)
            self.assertEqual([n.name for n in tree.nodes],['upstream','adapter'])
            self.assertEqual(graph._animation(tree),animation_before)
            self.assertIn('adapter retained',tree.name)
if __name__=='__main__':unittest.main()
