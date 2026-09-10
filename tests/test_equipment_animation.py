import ast
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
spec=importlib.util.spec_from_file_location('equipment_animation_contract',ROOT/'equipment_animation_contract.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
tree=ast.parse((ROOT/'animation_actions.py').read_text(encoding='utf-8'))
ns={'math':math}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='samples'],type_ignores=[]),'<samples>','exec'),ns)


def fixture():
    identity={key:key+'-native' for key in m.IDENTITY_KEYS}
    identity.update(rigKind='native-equipment-source-path',manifestHash='manifest-native')
    selected={'name':'native','equipment':identity,'resourcePath':identity['resourcePath'],'cab':'CAB-clip','pathId':'123',
              'originalSourceId':'CAB-source:456','controllerChain':[identity['controllerId']]}
    rest=[float(r==c) for r in range(4) for c in range(4)]
    bones=[{'name':'root','sourcePath':'native/root','parent':-1,'restMatrix':rest,'sourceHash':12}]
    live=[{'index':0,'sourcePath':'native/root','parent':-1,'restMatrix':rest,'sourceHash':'12'}]
    times=[0.,1/60,2/60]
    tracks=[{'bone':0,'channel':channel,'keys':[{'time':t,'value':value} for t in times]}
            for channel,value in [('location',[.1,.2,.3]),('rotation',[0.,0.,0.,1.]),('scale',[1.,1.,1.])]]
    result={'clip':{'name':'native','fps':60,'duration':2/60,'tracks':tracks},'bones':bones,
            'equipment':{'identity':identity,'clipId':'CAB-clip:123','originalSourceId':'CAB-source:456',
                         'controllerChain':[identity['controllerId']],'times':times,'samples':3,'sampleRate':60,
                         'bindings':[{'bone':0,'sourcePath':'native/root'}]}}
    return identity,selected,result,live


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
        self.assertTrue(m.supported({'methods':['animation-clips','animation-import'],'equipmentAnimation':{'rig':'native-equipment-source-path'}}))

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
