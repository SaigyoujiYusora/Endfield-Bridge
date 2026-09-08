import importlib.util
import copy
from pathlib import Path
import sys
import types
import unittest


class AnimationDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous = sys.modules.get('bpy')
        sys.modules['bpy'] = types.ModuleType('bpy')
        try:
            spec = importlib.util.spec_from_file_location('animation_actions_data_test', Path(__file__).resolve().parents[1]/'endfield_bridge/animation_actions.py')
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)
        finally:
            if previous is None:
                del sys.modules['bpy']
            else:
                sys.modules['bpy'] = previous

    def test_quaternion_hemisphere_and_fractional_timing(self):
        track = {'keys':[{'time':0,'value':[0,0,0,1]},{'time':.5,'value':[0,0,0,-1]}]}
        original = copy.deepcopy(track)
        frames, values = self.module.samples(track,4,29.97,.5,True)
        self.assertEqual(frames,[1,15.985])
        self.assertEqual(values,[[1,0,0,0],[1,0,0,0]])
        self.assertEqual(track,original)

    def test_bad_keys_reject_before_action_creation(self):
        for keys in ([],[{'time':0,'value':[float('nan'),0,0]}],
                     [{'time':.5,'value':[0,0,0]},{'time':.25,'value':[0,0,0]}]):
            with self.assertRaises(ValueError):
                self.module.samples({'keys':keys},3,60,1)

    def test_raw_unsigned_animator_scalar_identity_is_preserved(self):
        source = {'path':0,'typeId':95,'customType':0,'attribute':4294967295,'sampleRate':2,'values':[1,.25,-2]}
        clip = {'native':{'customScalars':[source]}}
        result = self.module.scalar_tracks(clip)[0]
        self.assertEqual(result['name'],'Animator_0_95_0_4294967295')
        self.assertEqual(result['values'],source['values'])
        self.assertEqual([k['time'] for k in result['keys']],[0,.5,1])
        self.assertTrue(self.module.scalar_property(result['name']).startswith('sora_anim_scalar_'))
        other = self.module.scalar_tracks({'native':{'customScalars':[dict(source,path=1)]}})[0]
        self.assertNotEqual(self.module.scalar_property(result['name']),self.module.scalar_property(other['name']))

    def test_renamed_native_path_and_hash_join(self):
        bone = type('Bone',(dict,),{})(sora_source_path='Root/Face',sora_source_hash='4294967295')
        bone.name = 'User renamed face'
        pose = object()
        rig = types.SimpleNamespace(data=types.SimpleNamespace(bones=[bone]),pose=types.SimpleNamespace(bones={bone.name:pose}))
        source = {'name':'Original face','sourcePath':'Root/Face','sourceHash':4294967295}
        self.assertEqual(self.module.resolve_bones(rig,['Original face'],[source]),[pose])
        with self.assertRaises(ValueError):
            self.module.resolve_bones(rig,['Original face'],[dict(source,sourceHash=124)])


if __name__ == '__main__':
    unittest.main()
