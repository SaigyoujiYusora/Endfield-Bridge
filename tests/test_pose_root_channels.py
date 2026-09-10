"""Host regression: restore raw channels without matrix decomposition; keep old states."""
import ast
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'endfield_bridge/pose_controls.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
NAMES = {'matrix_list','root_parent_frame','root_channels','validate_root_channels','restore_root','snapshot','suspend'}
NODES = [node for node in TREE.body if isinstance(node,ast.FunctionDef) and node.name in NAMES or
         isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ROOT_CHANNEL_SIZES' for t in node.targets)]
ENV = {'math':math,'json':json,'matrix':lambda flat:[flat[i:i+4] for i in range(0,16,4)],
       'STATE':'state','ACTION':'action'}
exec(compile(ast.Module(body=NODES,type_ignores=[]),str(SOURCE),'exec'),ENV)
IDENTITY = [[float(r==c) for c in range(4)] for r in range(4)]


class Root(dict):
    def __init__(self, mode='XYZ'):
        super().__init__()
        self.rotation_mode=mode
        self.location=[1.25,-2.5,3.75]
        self.rotation_euler=[0.125,-0.25,3.1415927410125732]
        self.rotation_quaternion=[.7,.1,.2,.3]
        self.rotation_axis_angle=[.125,1.,0.,0.]
        self.scale=[1.25,.875,2.]
        self.delta_location=[.01,.02,.03]
        self.delta_rotation_euler=[.1,.2,.3]
        self.delta_rotation_quaternion=[.8,.2,.1,.3]
        self.delta_scale=[1.01,.99,1.1]
        self.parent=NS(name='parent')
        self.parent_type='OBJECT'
        self.parent_bone=''
        self.parent_vertices=[0,1,2]
        self.matrix_parent_inverse=copy.deepcopy(IDENTITY)
        self.matrix_parent_inverse[0][3]=-.5
        self._basis=copy.deepcopy(IDENTITY)
        self._world=copy.deepcopy(IDENTITY)
        self.basis_assignments=0
        self.world_assignments=0
        self.animation_data=None
        self.pose=NS(bones=[])
        self.constraints=[]

    @property
    def matrix_basis(self):return self._basis
    @matrix_basis.setter
    def matrix_basis(self,value):
        self.basis_assignments+=1
        self._basis=value
        self.rotation_euler[2]=3.141592502593994 # model the observed destructive decomposition
    @property
    def matrix_world(self):return self._world
    @matrix_world.setter
    def matrix_world(self,value):
        self.world_assignments+=1
        self._world=value
        self.rotation_euler[2]=3.141592502593994


class RootChannelTests(unittest.TestCase):
    def test_all_rotation_modes_restore_active_inactive_and_delta_channels_exactly(self):
        for mode in ('XYZ','ZYX','QUATERNION','AXIS_ANGLE'):
            with self.subTest(mode=mode):
                root=Root(mode)
                saved=ENV['root_channels'](root)
                frozen=copy.deepcopy(saved)
                for key in ENV['ROOT_CHANNEL_SIZES']:
                    setattr(root,key,[0.]*len(getattr(root,key)))
                root.rotation_mode='XZY'
                root.matrix_parent_inverse=copy.deepcopy(IDENTITY)
                ENV['restore_root'](root,{'rootChannels':saved})
                self.assertEqual(ENV['root_channels'](root),frozen)
                self.assertEqual(root.basis_assignments,0)
                self.assertEqual(root.world_assignments,0)
                self.assertEqual(saved,frozen)

    def test_legacy_matrix_snapshot_still_restores(self):
        root=Root()
        flat=ENV['matrix_list'](IDENTITY)
        ENV['restore_root'](root,{'rigBasis':flat})
        self.assertEqual(root.basis_assignments,1)
        self.assertEqual(root.matrix_basis,IDENTITY)

    def test_corrupt_raw_state_rejected_before_any_channel_write(self):
        for change in ('nan','length','mode','parent'):
            with self.subTest(change=change):
                root=Root()
                before=ENV['root_channels'](root)
                saved=copy.deepcopy(before)
                if change=='nan':saved['channels']['scale'][0]=float('nan')
                if change=='length':saved['channels']['rotation_quaternion'].pop()
                if change=='mode':saved['rotationMode']='INVALID'
                if change=='parent':saved['parentFrame']['parent']='other'
                with self.assertRaises(ValueError):ENV['restore_root'](root,{'rootChannels':saved})
                self.assertEqual(ENV['root_channels'](root),before)
                self.assertEqual(root.basis_assignments,0)

    def test_unchanged_root_suspend_does_not_assign_world(self):
        root=Root()
        before=ENV['root_channels'](root)
        context=NS(screen=None,view_layer=NS(update=lambda:None))
        ENV['suspend'](context,root)
        self.assertEqual(root.world_assignments,0)
        self.assertEqual(ENV['root_channels'](root),before)
        self.assertEqual(json.loads(root['state'])['rootChannels'],before)

    def test_muted_root_constraint_keeps_evaluated_world_then_raw_restore(self):
        root=Root()
        root.constraints=[NS(name='copy-location',mute=False)]
        root._world[0][3]=7.0
        before=ENV['root_channels'](root)
        calls=[]
        def update():
            calls.append(True)
            if len(calls)==1:
                self.assertTrue(root.constraints[0].mute)
                root._world=copy.deepcopy(IDENTITY)
        ENV['suspend'](NS(screen=None,view_layer=NS(update=update)),root)
        self.assertEqual(root.world_assignments,1)
        self.assertEqual(root.matrix_world[0][3],7.0)
        ENV['restore_root'](root,json.loads(root['state']))
        self.assertEqual(ENV['root_channels'](root),before)


if __name__=='__main__':unittest.main()
