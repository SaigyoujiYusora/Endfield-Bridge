import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('scene_nodes',Path(__file__).resolve().parents[1]/'endfield_bridge/scene_nodes.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
I=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]

class Obj(dict): pass
class Matrix:
    @staticmethod
    def Identity(_): return tuple(I)
    @staticmethod
    def Diagonal(values): return tuple(values[r] if r==c else 0 for r in range(4) for c in range(4))

def point(matrix,p):
    return [sum(matrix[r*4+c]*p[c] for c in range(4)) for r in range(4)]

class NodeTests(unittest.TestCase):
    def test_shader_compensation_is_local_below_native_transform(self):
        obj=Obj();node={'sora_node_id':'cab:1'}
        with patch.dict(sys.modules,{'mathutils':NS(Matrix=Matrix)}):
            m.bind_mesh_node(obj,{'node':0,'coordinateSpace':'node'},[node],True)
        self.assertIs(obj.parent,node)
        transform=[0,-2,0,4,3,0,0,5,0,0,4,6,0,0,0,1]
        p=[1,2,3,1];rotated=[-1,-2,3,1]
        self.assertEqual(point(transform,point(obj.matrix_basis,rotated)),point(transform,p))

    def test_scene_space_skin_does_not_inherit_node_transform(self):
        obj=Obj();obj.parent='rig'
        with patch.dict(sys.modules,{'mathutils':NS(Matrix=Matrix)}):
            m.bind_mesh_node(obj,{'node':0,'coordinateSpace':'scene'},[{'sora_node_id':'cab:1'}],True)
        self.assertEqual(obj.parent,'rig')
        self.assertFalse(hasattr(obj,'matrix_basis'))

    def test_invalid_hierarchy_and_double_transform_contract_rejected(self):
        node={'id':'cab:1','parent':-1,'localMatrix':I}
        self.assertEqual(m.validate_nodes({'nodes':[node],'meshes':[]}),[node])
        for bad in (dict(node,parent=0),dict(node,localMatrix=[0]*16)):
            with self.assertRaises(ValueError): m.validate_nodes({'nodes':[bad],'meshes':[]})
        with self.assertRaises(ValueError):
            m.validate_nodes({'nodes':[node],'meshes':[{'node':0,'coordinateSpace':'node','weights':[{}]}]})

    def test_legacy_scene_without_node_contract_remains_valid(self):
        self.assertEqual(m.validate_nodes({'meshes':[{}]}),[])

if __name__=='__main__':unittest.main()
