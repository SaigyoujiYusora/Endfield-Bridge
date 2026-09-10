import ast
import copy
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'endfield_bridge'
spec = importlib.util.spec_from_file_location('initial_pose', ROOT / 'equipment_initial_pose.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def fixture():
    bones = [{'name': 'Root', 'sourcePath': 'Root'}, {'name': 'Child', 'sourcePath': 'Root/Child'}]
    pose = dict(resourceId='resource', resourcePath='equipment.prefab', animatorId='cab:1',
                controllerId='cab:2', clipId='cab:3', clipName='idle', animatorSourcePath='Root',
                time=0, status='native-controller-default-at-zero', scope=identity.SCOPE,
                bones=[dict(bone=i, sourcePath=b['sourcePath'], basisMatrix=I[:]) for i, b in enumerate(bones)])
    return dict(resourceId='resource', resourcePath='equipment.prefab', defaultPose=pose,
                scene=dict(bones=bones, nodes=[dict(sourcePath='Root')]),
                controllers=[dict(animatorId='cab:1', controllerId='cab:2', clips=[dict(sourceId='cab:3', name='idle')])])


class IdentityTests(unittest.TestCase):
    def test_matching_contract_and_source_rows(self):
        resource = fixture()
        sources, rows = identity.validate_initial_pose_identity(resource, resource['defaultPose'])
        self.assertEqual(len(sources), 2)
        self.assertEqual(rows[1]['sourcePath'], 'Root/Child')

    def test_stale_native_provenance_and_resource_ids_rejected(self):
        for field in ('resourceId', 'resourcePath', 'animatorId', 'controllerId', 'clipId', 'clipName', 'animatorSourcePath', 'scope'):
            resource = fixture()
            resource['defaultPose'][field] = 'stale'
            with self.subTest(field=field), self.assertRaises(ValueError):
                identity.validate_initial_pose_identity(resource, resource['defaultPose'])

    def test_empty_colliding_paths_and_wrong_indices_rejected(self):
        for value in ('', 'Root'):
            resource = fixture()
            resource['scene']['bones'][1]['sourcePath'] = value
            resource['defaultPose']['bones'][1]['sourcePath'] = value
            with self.assertRaises(ValueError):
                identity.validate_initial_pose_identity(resource, resource['defaultPose'])
        resource = fixture()
        resource['defaultPose']['bones'][1]['bone'] = True
        with self.assertRaises(ValueError):
            identity.validate_initial_pose_identity(resource, resource['defaultPose'])

    def test_application_preflights_all_bones_before_any_write(self):
        # Execute the real function, with identity-only matrix doubles. This tests
        # validation/write ordering, not Blender matrix decomposition or playback.
        class Mat:
            def __getitem__(self, row): return I[row*4:row*4+4]
            def decompose(self): return (None, None, None)
            @staticmethod
            def LocRotScale(*args): return Mat()
        tree = ast.parse((ROOT / 'equipment.py').read_text(encoding='utf8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'apply_initial_equipment_pose')
        namespace = dict(json=json, math=math, Matrix=Mat, mat=lambda values: Mat(),
                         validate_initial_pose_identity=identity.validate_initial_pose_identity)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'equipment.py', 'exec'), namespace)
        class Rig(dict): pass
        rig = Rig(sora_equipment_resource='resource', sora_resource_path='equipment.prefab')
        rig.animation_data = None
        root = SimpleNamespace(bone={'sora_source_path': 'Root', 'sora_source_index': 0}, matrix_basis='untouched')
        child = SimpleNamespace(bone={'sora_source_path': 'Root/Child', 'sora_source_index': 99}, matrix_basis='untouched')
        rig.pose = SimpleNamespace(bones={'Root': root, 'Child': child})
        apply = namespace['apply_initial_equipment_pose']
        with self.assertRaises(ValueError): apply(rig, fixture())
        self.assertEqual(root.matrix_basis, 'untouched')
        self.assertEqual(child.matrix_basis, 'untouched')
        child.bone['sora_source_index'] = 1
        apply(rig, fixture())
        self.assertIsInstance(root.matrix_basis, Mat)
        self.assertIsInstance(child.matrix_basis, Mat)
        rig.animation_data = SimpleNamespace(action=object(), nla_tracks=[])
        with self.assertRaises(ValueError): apply(rig, fixture())


if __name__ == '__main__':
    unittest.main()
