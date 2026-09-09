import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('maths',Path(__file__).resolve().parents[1]/'endfield_bridge/equipment_math.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
I=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
R=[-1,0,0,0,0,-1,0,0,0,0,1,0,0,0,0,1]
def translation(x,y,z):
    a=list(I);a[3]=x;a[7]=y;a[11]=z;return a

class EquipmentMathTests(unittest.TestCase):
    def test_bone_tail_convention_is_cancelled_for_arbitrary_pose(self):
        pose=[0,-2,0,4,3,0,0,5,0,0,4,6,0,0,0,1]
        local=translation(0.3,0.7,-0.2)
        parent=m.multiply(pose,translation(0,0.123,0))
        actual=m.multiply(parent,m.tail_compensated(local,0.123))
        expected=m.multiply(pose,local)
        for a,b in zip(actual,expected):self.assertAlmostEqual(a,b)

    def test_owner_and_equipment_npr_compensation_are_not_reapplied(self):
        bone=translation(1,2,3);local=translation(0.4,0.5,0.6)
        actual=m.multiply(m.multiply(m.multiply(m.multiply(R,m.multiply(R,bone)),local),R),R)
        self.assertEqual(actual,m.multiply(bone,local))

    def test_wire_scale_invariant_and_corruption_detection(self):
        bone=translation(0,0,1);mount=translation(2,0,1)
        scale=[2,0,0,0,0,2,0,0,0,0,2,0,0,0,0,1]
        local=m.multiply(translation(2,0,0),scale)
        state={'parentBoneRestMatrix':bone,'nativeMountWorldInScene':mount,'localMatrix':local}
        self.assertEqual(m.validate_wire(state,2),0)
        state['localMatrix'][3]+=0.1
        with self.assertRaises(ValueError):m.validate_wire(state,2)

if __name__=='__main__':unittest.main()
