import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

spec=importlib.util.spec_from_file_location('binding',Path(__file__).resolve().parents[1]/'endfield_bridge/action_binding.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ID:
    def __init__(self,name):self.name=name
    def as_pointer(self):return id(self)


class Animation:
    def __init__(self,owner,action=None,slot=None):self.owner,self._action,self._slot=owner,action,slot
    @property
    def action(self):return self._action
    @action.setter
    def action(self,action):
        self.owner.action_calls+=1
        self.owner.animation_data=Animation(self.owner,action,action.slots[0] if action and action.slots else None)
        self.stale=True
    @property
    def action_slot(self):
        if getattr(self,'stale',False):raise AssertionError('Stale AnimData wrapper')
        return self._slot
    @action_slot.setter
    def action_slot(self,slot):self.owner.slot_calls+=1;self._slot=slot


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.obj=ID('rig');self.obj.type='ARMATURE';self.obj.pose=NS(bones=[1])
        self.obj.action_calls=self.obj.slot_calls=0
        self.obj.animation_data=Animation(self.obj)
        self.action=ID('clip');self.action.slots=[NS(identifier='OBrig',handle=1),NS(identifier='OBother',handle=2)]
        self.bpy=NS(data=NS(objects={'rig':self.obj},actions={'clip':self.action}))
    def bind(self,wanted='OBrig'):
        m.bind_action(self.obj,self.action,wanted,select_slot=True,bpy_module=self.bpy)
    def test_auto_selected_matching_slot_never_reassigned_and_wrapper_reacquired(self):
        self.bind();self.bind()
        self.assertEqual(self.obj.action_calls,1);self.assertEqual(self.obj.slot_calls,0)
    def test_different_saved_slot_is_selected_when_required(self):
        self.bind('OBother')
        self.assertEqual(self.obj.slot_calls,1)
        self.assertEqual(self.obj.animation_data.action_slot.identifier,'OBother')
    def test_missing_saved_slot_fails_before_any_action_mutation(self):
        with self.assertRaises(ValueError):self.bind('missing')
        self.assertEqual(self.obj.action_calls,0);self.assertEqual(self.obj.slot_calls,0)
    def test_null_armature_pose_fails_before_action_or_slot_mutation(self):
        self.obj.pose=None
        with self.assertRaisesRegex(ValueError,'not initialized'):self.bind()
        self.assertEqual(self.obj.action_calls,0);self.assertEqual(self.obj.slot_calls,0)
    def test_same_name_different_owner_id_is_rejected(self):
        self.bpy.data.objects['rig']=ID('rig')
        with self.assertRaisesRegex(ValueError,'changed'):self.bind()
        self.assertEqual(self.obj.action_calls,0)
    def test_unassigned_previous_slot_is_preserved_when_explicitly_requested(self):
        self.bind(None)
        self.assertIsNone(self.obj.animation_data.action_slot)
        self.assertEqual(self.obj.slot_calls,1)
    def test_clear_action_does_not_invoke_redundant_slot_setter(self):
        self.bind()
        m.bind_action(self.obj,None,bpy_module=self.bpy)
        self.assertIsNone(self.obj.animation_data.action)
        self.assertEqual(self.obj.slot_calls,0)


if __name__=='__main__':unittest.main()
