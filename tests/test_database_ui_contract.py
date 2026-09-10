import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
ns={'CoreError':RuntimeError}
tree=ast.parse((ROOT/'__init__.py').read_text(encoding='utf-8'))
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'invalidate','invalidate_source','database_budget','database_budget_lines','database_parameters','database_complete'}],type_ignores=[]),'<database contract>','exec'),ns)
tree=ast.parse((ROOT/'animation_panel.py').read_text())
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='set_status'],type_ignores=[]),'<animation status>','exec'),ns)

def settings(database='test.sredb'):
    return NS(game_root='game',database=database,assets=['stale'],selected=0,result_database='old',offset=30,total=99,source_details=False,status='Imported old asset')

class DatabaseUiContractTests(unittest.TestCase):
    def compatibility(self, **changes):
        result = dict(contractVersion=1, mode='standalone-scene', databaseVersion='v1',
            versionMatches=True, assetCount=1, cachedSceneCount=1, allAssetsHaveCachedScenes=True,
            canImportCachedScenes=True, canResolveIndexedAssets=False)
        result.update(changes)
        return result
    def test_standalone_validation_reports_cached_compatibility_without_claiming_match(self):
        for same_version in (True, False):
            value=settings()
            ns['database_complete'](value,None,'game-validate',True,dict(version='v2',matches=False,
                databaseCompatibility=self.compatibility(versionMatches=same_version)))
            self.assertIn('1/1 cached scenes',value.status)
            self.assertIn('resource match unverified' if same_version else 'game version differs',value.status)
            self.assertTrue(value.source_details)
            self.assertEqual(value.selected,-1)
    def test_partial_standalone_does_not_claim_complete_import(self):
        value=settings()
        ns['database_complete'](value,None,'game-validate',True,dict(matches=False,
            databaseCompatibility=self.compatibility(assetCount=2,allAssetsHaveCachedScenes=False)))
        self.assertIn('Uncached records cannot be imported',value.status)
    def test_unknown_or_incomplete_compatibility_fails_closed(self):
        for changes in ({'contractVersion':2},{'contractVersion':True},{'mode':'future'},
                {'cachedSceneCount':0},{'assetCount':0},{'allAssetsHaveCachedScenes':False},
                {'canImportCachedScenes':False},{'canResolveIndexedAssets':True},{'versionMatches':None}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                ns['database_complete'](settings(),None,'game-validate',True,dict(matches=False,
                    databaseCompatibility=self.compatibility(**changes)))
    def test_legacy_snapshot_and_mismatched_index_remain_blocked(self):
        for mode in ('legacy-snapshot','indexed-game'):
            with self.assertRaises(RuntimeError):
                ns['database_complete'](settings(),None,'game-validate',True,dict(matches=False,
                    databaseCompatibility=self.compatibility(mode=mode)))
    def test_complete_matched_index_keeps_existing_success(self):
        value=settings()
        ns['database_complete'](value,None,'game-validate',True,dict(version='v1',matches=True,
            databaseCompatibility=self.compatibility(mode='indexed-game',canResolveIndexedAssets=True)))
        self.assertIn('Game/database matched',value.status)
    def test_missing_field_cannot_bypass_index_validation(self):
        for field in self.compatibility():
            contract=self.compatibility(mode='indexed-game',canResolveIndexedAssets=True)
            del contract[field]
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                ns['database_complete'](settings(),None,'game-validate',True,dict(matches=True,databaseCompatibility=contract))
    def test_validated_budget_and_compact_lines(self):
        value=settings()
        ns['database_complete'](value,None,'database-build',True,{'gameVersion':'v9','assets':4321,'formatVersion':3,'payloadBytes':240649994,'maxPayloadBytes':268435456})
        self.assertEqual(value.payload_bytes,240649994)
        self.assertEqual(value.max_payload_bytes-value.payload_bytes,27785462)
        self.assertEqual(ns['database_budget_lines'](value),('JSON 229.5 / 256.0 MiB','余量 26.5 MiB · v3'))
    def test_missing_or_invalid_optional_budget_clears_old_values(self):
        value=settings()
        for result in ({},{'formatVersion':3,'payloadBytes':True,'maxPayloadBytes':10},{'formatVersion':3,'payloadBytes':11,'maxPayloadBytes':10},{'formatVersion':3,'payloadBytes':1.5,'maxPayloadBytes':10}):
            value.payload_bytes=12;value.budget_database=value.database
            ns['database_budget'](value,result)
            self.assertEqual(value.payload_bytes,-1);self.assertEqual(value.budget_database,'')
    def test_source_change_clears_budget_but_filter_change_keeps_it(self):
        value=settings();ns['database_budget'](value,{'formatVersion':2,'payloadBytes':0,'maxPayloadBytes':268435456})
        ns['invalidate'](value,None);self.assertEqual(value.payload_bytes,0)
        ns['invalidate_source'](value,None);self.assertEqual(value.payload_bytes,-1)
        self.assertEqual(ns['database_budget_lines'](value),('库容量：尚未获取',))
    def test_mismatch_retains_valid_container_budget_without_enabling_import(self):
        value=settings()
        with self.assertRaises(RuntimeError):ns['database_complete'](value,None,'game-validate',True,{'matches':False,'formatVersion':3,'payloadBytes':40,'maxPayloadBytes':50})
        self.assertEqual(value.payload_bytes,40);self.assertEqual(value.selected,-1)
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
