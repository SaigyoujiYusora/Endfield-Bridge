"""Caller regressions: an explicitly unassigned slot must remain inactive."""
import ast,json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import test_action_binding as binding

SOURCE=Path(__file__).resolve().parents[1]/'endfield_bridge'

class Rig(dict):
    name='rig'
    type='ARMATURE'
    pose=NS(bones={})
    constraints=[]
    def as_pointer(self):return id(self)

class Actions(dict):
    def remove(self,action):del self[action.name]

class CallerTests(unittest.TestCase):
    def setUp(self):
        self.rig=Rig();self.rig.action_calls=self.rig.slot_calls=0
        self.action=binding.ID('previous');self.action.slots=[NS(identifier='OBrig',handle=1)]
        self.rig.animation_data=binding.Animation(self.rig)
        self.rig.animation_data.nla_tracks=[];self.rig.animation_data.drivers=[]
        self.actions=Actions(previous=self.action)
        self.bpy=NS(data=NS(objects={'rig':self.rig},actions=self.actions))
        self.bind=lambda *a,**kw:binding.m.bind_action(*a,**kw,bpy_module=self.bpy)
        self.evaluated=[]
        scene=NS(frame_current=1,frame_subframe=0.)
        scene.frame_set=lambda *a,**kw:self.evaluated.append(self.rig.animation_data.action_slot is not None)
        self.context=NS(scene=scene)

    def restore(self,slot_marker):
        state={'hadAction':True,'bones':{},'nla':[],'drivers':[],'constraints':[]}
        if slot_marker!='ABSENT':state['slot']=slot_marker
        self.rig['state']=json.dumps(state);self.rig['action']=self.action
        tree=ast.parse((SOURCE/'pose_controls.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='restore')
        env={'json':json,'STATE':'state','ACTION':'action','bind_action':self.bind,'restore_root':lambda *a:None}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<production restore>','exec'),env)
        env['restore'](self.context,self.rig)

    def test_explicit_null_does_not_activate_slot_at_frame_evaluation(self):
        self.restore(None)
        self.assertIs(self.rig.animation_data.action,self.action)
        self.assertIsNone(self.rig.animation_data.action_slot)
        self.assertEqual(self.evaluated,[False])
        self.assertEqual(self.rig.slot_calls,1)

    def test_missing_legacy_field_retains_automatic_slot(self):
        self.restore('ABSENT')
        self.assertIs(self.rig.animation_data.action_slot,self.action.slots[0])
        self.assertEqual(self.rig.slot_calls,0)

    def test_saved_slot_preserves_binding_without_redundant_setter(self):
        self.restore('OBrig')
        self.assertIs(self.rig.animation_data.action_slot,self.action.slots[0])
        self.assertEqual(self.rig.slot_calls,0)

    def test_missing_saved_slot_retains_state_before_binding(self):
        with self.assertRaisesRegex(ValueError,'slot was removed'):self.restore('OBremoved')
        self.assertIsNone(self.rig.animation_data.action)
        self.assertIn('state',self.rig)
        self.assertEqual(self.rig.action_calls,0)

    def test_clip_failure_restores_explicit_null_and_removes_candidate(self):
        candidate=binding.ID('candidate');candidate.slots=[NS(identifier='OBnew',handle=2)]
        self.actions[candidate.name]=candidate
        self.rig.animation_data=binding.Animation(self.rig,candidate,candidate.slots[0])
        tree=ast.parse((SOURCE/'animation_actions.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='apply_clip_steps')
        handler=next(n for n in ast.walk(fn) if isinstance(n,ast.ExceptHandler) and isinstance(n.type,ast.Name) and n.type.id=='BaseException')
        wrapper=ast.parse('try:\n raise RuntimeError("injected clip failure")\nexcept BaseException:\n pass')
        wrapper.body[0].handlers[0].body=handler.body
        env={'rig':self.rig,'animation':self.rig.animation_data,'previous_action':self.action,'previous_slot':None,
             'bind_action':self.bind,'bpy':self.bpy,'action':candidate,'timeline':{},'pose_state':[],
             'property_state':{},'registry_state':None,'old_face_mask':None,'previous_override':None,'queue_tracks':[],
             '_restore_face_mask':lambda *a:None,'_restore_properties':lambda *a:None}
        with self.assertRaisesRegex(RuntimeError,'injected clip failure'):
            exec(compile(wrapper,'<production clip rollback>','exec'),env)
        self.assertIs(self.rig.animation_data.action,self.action)
        self.assertIsNone(self.rig.animation_data.action_slot)
        self.assertNotIn('candidate',self.actions)

if __name__=='__main__':unittest.main()
