import importlib.util
from pathlib import Path
import math
import unittest
spec=importlib.util.spec_from_file_location('geometry',Path(__file__).resolve().parents[1]/'endfield_bridge/pose_geometry.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class GeometryTests(unittest.TestCase):
    def test_t_targets_horizontal_and_opposite(self):
        left,right=m.arm_directions((-1,0,2),(1,0,2),(0,0,0),(0,0,3),0)
        self.assertEqual(left,(-1,0,0));self.assertEqual(right,(1,0,0))
    def test_a_targets_down_45_degrees_without_scale_dependence(self):
        first=m.arm_directions((-1,0,2),(1,0,2),(0,0,0),(0,0,3),45)
        scaled=m.arm_directions((-10,0,20),(10,0,20),(0,0,0),(0,0,30),45)
        self.assertEqual(first,scaled)
        for direction in first:
            self.assertAlmostEqual(sum(x*x for x in direction),1)
            self.assertAlmostEqual(direction[2],-math.sqrt(.5))
    def test_body_rotation_is_respected(self):
        left,right=m.arm_directions((0,-1,2),(0,1,2),(0,0,0),(0,0,3),0)
        self.assertEqual(left,(0,-1,0));self.assertEqual(right,(0,1,0))
    def test_degenerate_or_unapproved_angle_rejected(self):
        for args in [((0,0,0),(0,0,1),(0,0,0),(0,0,3),0),((-1,0,2),(1,0,2),(0,0,0),(0,0,3),12)]:
            with self.assertRaises(ValueError):m.arm_directions(*args)

if __name__=='__main__':unittest.main()
