import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
class Collection(dict):
    def __init__(self,token):super().__init__(sora_instance=token,sora_render_mode='RURI',sora_render_canonical=True);self.children=[]
    def as_pointer(self):return id(self)

class ReviewContractTests(unittest.TestCase):
    def test_attemptable_uncached_asset_is_enabled(self):
        tree=ast.parse((ROOT/'__init__.py').read_text(encoding='utf-8'))
        ns={'tasks':NS(busy=lambda:False),'bpy':NS(path=NS(abspath=lambda p:p))}
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='import_reason'],type_ignores=[]),'<poll>','exec'),ns)
        row=NS(contract_version=1,can_import=False,can_attempt_import=True,database_mode='indexed-game',reason='Native parser can attempt')
        settings=NS(database='db',result_database='db',selected=0,assets=[row])
        self.assertEqual(ns['import_reason'](NS(scene=NS(sora=settings),mode='OBJECT')),'')
        row.can_attempt_import=False
        self.assertEqual(ns['import_reason'](NS(scene=NS(sora=settings),mode='OBJECT')),row.reason)

    def test_later_tree_failure_restores_already_switched_parent(self):
        tree=ast.parse((ROOT/'render_modes.py').read_text())
        root=Collection('root');child=Collection('child');child['sora_owner_collection']=root;root.children=[child]
        def switch(context,collection,mode,document):
            if collection is child and mode=='BASIC':raise ValueError('injected child failure')
            collection['sora_render_mode']=mode
            yield {'stage':'switched'}
        ns={'MODE':'sora_render_mode','SIGNATURE':'signature','bpy':NS(data=NS(materials=[],images=[],node_groups=[])),
            'meshes':lambda c:[],'switch_steps':switch}
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'owned_tree','switch_tree_steps'}],type_ignores=[]),'<tree>','exec'),ns)
        with self.assertRaisesRegex(ValueError,'injected'):
            list(ns['switch_tree_steps'](None,root,'BASIC',{}))
        self.assertEqual(root['sora_render_mode'],'RURI');self.assertEqual(child['sora_render_mode'],'RURI')

if __name__=='__main__':unittest.main()
