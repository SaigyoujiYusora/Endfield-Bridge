import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
ns={'CoreError':RuntimeError}
tree=ast.parse((ROOT/'__init__.py').read_text(encoding='utf-8'))
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'invalidate','database_parameters','database_complete'}],type_ignores=[]),'<database contract>','exec'),ns)
tree=ast.parse((ROOT/'animation_panel.py').read_text())
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='set_status'],type_ignores=[]),'<animation status>','exec'),ns)

def settings(database='test.sredb'):
    return NS(game_root='game',database=database,assets=['stale'],selected=0,result_database='old',offset=30,total=99,source_details=False,status='Imported old asset')

class DatabaseUiContractTests(unittest.TestCase):
    def test_root_only_validation_never_resolves_empty_database(self):
        calls=[]
        def resolve(value):calls.append(value);return '/resolved/'+value
        self.assertEqual(ns['database_parameters'](settings(''),'game-validate',resolve),{'root':'/resolved/game'})
        self.assertEqual(calls,['game'])
    def test_build_requires_an_output_path(self):
        with self.assertRaises(RuntimeError):ns['database_parameters'](settings(''),'database-build',lambda p:p)
    def test_mismatch_fails_and_keeps_details_open(self):
        value=settings()
        with self.assertRaisesRegex(RuntimeError,'update or rebuild'):
            ns['database_complete'](value,None,'game-validate',True,{'version':'v2','matches':False})
        self.assertTrue(value.source_details)
        self.assertEqual(value.assets,[]);self.assertEqual(value.selected,-1);self.assertEqual(value.total,0)
        self.assertEqual(value.result_database,'')
    def test_build_reports_real_version_and_count(self):
        value=settings()
        ns['database_complete'](value,None,'database-build',True,{'gameVersion':'v9','assets':4321})
        self.assertIn('v9',value.status);self.assertIn('4321 indexed assets',value.status)
        self.assertFalse(value.source_details)
    def test_root_only_success_keeps_database_configuration_available(self):
        value=settings('')
        ns['database_complete'](value,None,'game-validate',False,{'version':'v9','matches':True,'manifestRevision':7})
        self.assertTrue(value.source_details);self.assertIn('revision 7',value.status)
    def test_animation_completion_replaces_shared_old_status(self):
        animation=NS(status='old');shared=settings();context=NS(scene=NS(sora=shared))
        ns['set_status'](context,animation,'Loaded native idle')
        self.assertEqual(animation.status,shared.status)
        self.assertEqual(shared.status,'Loaded native idle')

if __name__=='__main__':unittest.main()
