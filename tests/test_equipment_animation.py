import ast
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import unittest
import tempfile
import os
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
spec=importlib.util.spec_from_file_location('equipment_animation_contract',ROOT/'equipment_animation_contract.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
tree=ast.parse((ROOT/'animation_actions.py').read_text(encoding='utf-8'))
ns={'math':math}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='samples'],type_ignores=[]),'<samples>','exec'),ns)


def capabilities():
    return {'product':'Sora-Core','methods':['animation-clips','animation-import'],
            'equipmentAnimation':{'rig':'native-equipment-source-path',
                'sampling':'non-ACL scalar blocks or ACL transform tracks on the authored native frame grid',
                'transport':'animation-clips / animation-import with equipment selector',
                'runtime':'single controller layer; trigger and exit-time transitions with local TRS crossfade; no visibility or damping','proofContract':m.PROOF_CONTRACT}}


def fixture():
    identity={key:key+'-native' for key in m.IDENTITY_KEYS}
    identity.update(rigKind='native-equipment-source-path',manifestHash='manifest-native',animatorSourcePath='native/root')
    native_source={'resourcePath':identity['resourcePath'],'cab':'CAB-clip','pathId':'123','manifestHash':identity['manifestHash']}
    schema=[{'pathHash':0,'attribute':a,'typeId':4,'customType':0,'isPPtrCurve':0,'sourcePath':'native/root','resolution':'native-path'} for a in (1,2)]
    selected={'name':'native','equipment':identity,'resourcePath':identity['resourcePath'],'cab':'CAB-clip','pathId':'123',
              'originalSourceId':'CAB-source:456','controllerChain':[identity['controllerId']],
              'bindingSchema':schema,'bindingSchemaSource':native_source,'proofContract':m.PROOF_CONTRACT}
    expected=dict(identity,nodeSourcePaths=['native/root'],controllerClips=[{'sourceId':'CAB-clip:123','name':'native',
                'originalSourceId':selected['originalSourceId'],'controllerChain':selected['controllerChain'],'bindingPaths':[0]}])
    rest=[float(r==c) for r in range(4) for c in range(4)]
    bones=[{'name':'root','sourcePath':'native/root','parent':-1,'restMatrix':rest,'sourceHash':12}]
    live=[{'index':0,'sourcePath':'native/root','parent':-1,'restMatrix':rest,'sourceHash':'12'}]
    times=[0.,1/60,2/60]
    tracks=[{'bone':0,'channel':channel,'keys':[{'time':t,'value':value} for t in times]}
            for channel,value in [('location',[.1,.2,.3]),('rotation',[0.,0.,0.,1.]),('scale',[1.,1.,1.])]]
    result={'clip':{'name':'native','fps':60,'duration':2/60,'tracks':tracks,'native':{'source':native_source}},'bones':bones,
            'equipment':{'identity':identity,'clipId':'CAB-clip:123','originalSourceId':'CAB-source:456',
                         'controllerChain':[identity['controllerId']],'times':times,'samples':3,'sampleRate':60,
                         'proofContract':m.PROOF_CONTRACT,'scope':'single-native-generic-clip; no controller transitions, events, visibility or damping',
                         'bindings':[{'bone':0,'sourcePath':'native/root','pathHash':0,'attribute':a} for a in (1,2)]}}
    return expected,selected,result,live


class ContractTests(unittest.TestCase):
    def test_full_native_contract_accepted_and_cross_owner_controller_clip_rejected(self):
        expected,selected,result,live=fixture()
        self.assertEqual(m.validate_import(result,expected,selected,live)[0],result['clip'])
        for key in ('ownerAssetId','slotId','resourceId','controllerId'):
            altered=deepcopy(result);altered['equipment']['identity'][key]='wrong'
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_import(altered,expected,selected,live)
        result['equipment']['clipId']='CAB-other:1'
        with self.assertRaises(ValueError):m.validate_import(result,expected,selected,live)

    def test_source_path_index_rest_and_complete_key_grid_required(self):
        expected,selected,result,live=fixture()
        for mutation in ('path','index','rest','missing-track','off-grid','missing-sample'):
            r,l=deepcopy(result),deepcopy(live)
            if mutation=='path':l[0]['sourcePath']='foreign/root'
            if mutation=='index':l[0]['index']=3
            if mutation=='rest':l[0]['restMatrix'][3]=.01
            if mutation=='missing-track':r['clip']['tracks'].pop()
            if mutation=='off-grid':r['clip']['tracks'][0]['keys'][1]['time']+=.001
            if mutation=='missing-sample':r['clip']['tracks'][0]['keys'].pop()
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):m.validate_import(r,expected,selected,l)

    def test_canonical_shader_frame_checked_without_changing_rest(self):
        expected,selected,result,live=fixture()
        live[0]['restMatrix']=[-v if i<8 else v for i,v in enumerate(live[0]['restMatrix'])]
        before=deepcopy(live)
        m.validate_import(result,expected,selected,live,canonical=True)
        self.assertEqual(live,before)

    def test_old_capabilities_are_explicitly_unsupported(self):
        self.assertFalse(m.supported({'methods':['animation-clips','animation-import']}))
        self.assertTrue(m.supported(capabilities()))

    def test_native_seconds_map_to_current_fps_origin_without_resampling_values(self):
        track={'keys':[{'time':0.,'value':[0.,0.,0.,1.]},{'time':1/60,'value':[0.,0.,1.,0.]}]}
        original=deepcopy(track)
        frames,values=ns['samples'](track,4,24,1,True,frame_origin=43.25)
        self.assertEqual(frames,[43.25,43.65])
        self.assertEqual(values,[[1.,0.,0.,0.],[0.,0.,0.,1.]])
        self.assertEqual(track,original)
        self.assertEqual(ns['samples'](track,4,60,1,True)[0],[1.,2.])
        self.assertEqual(m.frame_grid([0.,1/60],24,43.25)[0],43.25)
        with self.assertRaisesRegex(ValueError,'distinct'):m.frame_grid([0.,1/60],.001,1000000)


    def test_complete_discovery_schema_not_just_bone_or_distinct_path_is_required(self):
        expected,selected,result,live=fixture()
        for mutation in ('attribute','hash','bool-attribute','duplicate','missing-attribute','extra-attribute','empty'):
            r=deepcopy(result);rows=r['equipment']['bindings']
            if mutation=='attribute':rows[0]['attribute']=9
            if mutation=='hash':rows[0]['pathHash']=123
            if mutation=='bool-attribute':rows[0]['attribute']=True
            if mutation=='duplicate':rows.append(deepcopy(rows[0]))
            if mutation=='missing-attribute':rows.pop()
            if mutation=='extra-attribute':rows.append(dict(rows[0],attribute=3))
            if mutation=='empty':rows.clear()
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):m.validate_import(r,expected,selected,live)

    def test_manifest_animator_and_clip_source_are_independently_checked(self):
        expected,selected,result,live=fixture()
        for key in ('manifestHash','animatorSourcePath'):
            r,s=deepcopy(result),deepcopy(selected)
            r['equipment']['identity'][key]=s['equipment'][key]='wrong'
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_import(r,expected,s,live)
        for key in ('resourcePath','cab','pathId','manifestHash'):
            r=deepcopy(result);r['clip']['native']['source'][key]='wrong'
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_import(r,expected,selected,live)
        with self.assertRaises(ValueError):m.validate_import(result,dict(expected,manifestHash='new-catalog'),selected,live)

    def test_schema_source_coverage_and_unresolved_mappings_are_not_self_certified_by_import(self):
        expected,selected,result,live=fixture()
        for mutation in ('source','coverage','duplicate','unresolved','path','version'):
            s=deepcopy(selected)
            if mutation=='source':s['bindingSchemaSource']['cab']='other'
            if mutation=='coverage':s['bindingSchema'][0]['pathHash']=12
            if mutation=='duplicate':s['bindingSchema'].append(deepcopy(s['bindingSchema'][0]))
            if mutation=='unresolved':s['bindingSchema'][0].update(sourcePath=None,resolution='unmapped-path-hash')
            if mutation=='path':s['bindingSchema'][0]['sourcePath']='other/path'
            if mutation=='version':s['proofContract']='old'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):m.validate_import(result,expected,s,live)
        # Lack of an older initial-pose record is legal: discovery is authoritative,
        # and its Animator/path/schema must still belong to the imported hierarchy.
        m.validate_import(result,dict(expected,animatorSourcePath=None),selected,live)

    def test_capabilities_require_the_proof_sampling_and_runtime_contract(self):
        for field in ('rig','sampling','proofContract','transport','runtime'):
            caps=capabilities();caps['equipmentAnimation'][field]='wrong'
            with self.subTest(field=field):self.assertFalse(m.supported(caps))
        self.assertFalse(m.supported(None))
        self.assertEqual(m.catalog_manifest({'catalogSource':{'manifestHash':'native'}}),'native')
        with self.assertRaises(ValueError):m.catalog_manifest({'catalogSource':None})

    def test_same_path_managed_binary_change_invalidates_apphost_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);exe=path/'Sora-Core.exe';dll=path/'Sora.Core.dll'
            exe.write_bytes(b'unchanged-apphost');dll.write_bytes(b'old1');stamp=dll.stat()
            before=m.backend_fingerprint(exe)
            dll.write_bytes(b'new1');os.utime(dll,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            after=m.backend_fingerprint(exe)
            self.assertEqual(before[str(exe.resolve())],after[str(exe.resolve())]);self.assertNotEqual(before,after)


class ConstraintGuardTests(unittest.TestCase):
    def functions(self):
        class ID:pass
        module=ast.parse((ROOT/'equipment_animation.py').read_text(encoding='utf-8'))
        names={'clone','editable_rna','constraint_state','paused'}
        namespace={'bpy':SimpleNamespace(types=SimpleNamespace(ID=ID))}
        exec(compile(ast.Module(body=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<constraint guards>','exec'),namespace)
        return namespace,ID
    def test_constraint_target_influence_and_order_edits_are_detected_without_writes(self):
        functions,ID=self.functions();target=ID();other=ID()
        fields=[SimpleNamespace(identifier=k,type=t,is_readonly=False) for k,t in [('name','STRING'),('influence','FLOAT'),('target','POINTER'),('subtarget','STRING'),('mute','BOOLEAN')]]
        fields.append(SimpleNamespace(identifier='error_location',type='FLOAT',is_readonly=True))
        def constraint(name):return SimpleNamespace(bl_rna=SimpleNamespace(properties=fields,identifier='SyntheticConstraint'),name=name,influence=1.,target=target,subtarget='Bone',mute=False,error_location=0.)
        c=constraint('Copy');rig=SimpleNamespace(constraints=[c],pose=SimpleNamespace(bones=[]))
        capture=functions['constraint_state'];before=capture(rig)
        c.error_location=2.;self.assertEqual(before,capture(rig))
        for key,value in [('influence',.5),('target',other),('subtarget','Other'),('mute',True)]:
            previous=getattr(c,key);setattr(c,key,value);self.assertNotEqual(before,capture(rig));self.assertEqual(getattr(c,key),value);setattr(c,key,previous)
        rig.constraints.append(constraint('Second'));order=capture(rig);rig.constraints.reverse();self.assertNotEqual(order,capture(rig))
    def test_unchanged_constraints_do_not_forbid_legal_manual_action_loading(self):
        functions,_=self.functions();rig=SimpleNamespace(get=lambda key:None,animation_data=None,constraints=[object()])
        context=SimpleNamespace(screen=SimpleNamespace(is_animation_playing=False),mode='OBJECT')
        functions['paused'](context,rig)


class CancellationTests(unittest.TestCase):
    def run_work(self,user_edit=False,cancel=False,fail=False):
        state={'value':0};restores=[]
        def check():
            if state['value']!=0:raise ValueError('User changed target')
        def capture():return dict(state)
        def restore(saved):state.update(saved);restores.append(saved)
        def work():
            try:
                yield 'building'
                state['value']=3
                if fail:raise RuntimeError('binding failed')
                return 'new action'
            finally:
                if state['value']!=3 or fail:state['value']=.00001 # existing matrix rollback is not raw-exact
        runner=m.guarded_steps(work,check,capture,restore)
        self.assertEqual(next(runner),'building')
        if user_edit:state['value']=7
        if cancel:runner.close()
        elif user_edit:
            with self.assertRaises(ValueError):next(runner)
        elif fail:
            with self.assertRaises(RuntimeError):next(runner)
        else:
            with self.assertRaises(StopIteration) as finished:next(runner)
            self.assertEqual(finished.exception.value,'new action')
        return state,restores
    def test_cancel_and_failed_binding_restore_exact_original(self):
        for kwargs in ({'cancel':True},{'fail':True}):
            state,restores=self.run_work(**kwargs)
            self.assertEqual(state['value'],0);self.assertEqual(len(restores),1)
    def test_user_edit_is_not_overwritten_on_guard_failure_or_cancel(self):
        for cancel in (False,True):
            state,restores=self.run_work(user_edit=True,cancel=cancel)
            self.assertEqual(state['value'],7);self.assertEqual(restores,[{'value':7}])
    def test_success_keeps_new_action_state(self):
        state,restores=self.run_work()
        self.assertEqual(state['value'],3);self.assertEqual(restores,[])


if __name__=='__main__':unittest.main()
