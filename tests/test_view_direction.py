"""Contract failures must reject before changing an observation graph."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
spec=importlib.util.spec_from_file_location('view_direction',Path(__file__).resolve().parents[1]/'endfield_bridge/view_direction.py')
vd=importlib.util.module_from_spec(spec);spec.loader.exec_module(vd)
class Sockets(list):
    def __getitem__(self,key):return next(x for x in self if x.name==key) if isinstance(key,str) else super().__getitem__(key)
class Socket:
    def __init__(self,node,name):self.node=node;self.name=name;self.links=[];self.default_value=(0.,0.,0.)
    @property
    def is_linked(self):return bool(self.links)
class Node:
    def __init__(self,kind,name):
        self.bl_idname=kind;self.name=name;self.label='';self.operation='';self.vector_type='';self.convert_from='';self.convert_to=''
        self.inputs=Sockets(Socket(self,str(i)) for i in range(4))
        self.outputs=Sockets(Socket(self,x) for x in ('Incoming','1','2','3'))
class Nodes(list):
    def get(self,name):return next((n for n in self if n.name==name),None)
    def new(self,kind):
        if self.fail_after is not None:
            if self.fail_after==0:raise RuntimeError('allocation failure')
            self.fail_after-=1
        n=Node(kind,kind);self.append(n);return n
    fail_after=None
class Links(list):
    def remove(self,l):
        l.from_socket.links.remove(l);l.to_socket.links.remove(l);super().remove(l)
    def new(self,source,target):
        for l in list(target.links):self.remove(l)
        l=NS(from_socket=source,to_socket=target);self.append(l);source.links.append(l);target.links.append(l)
class Group(dict):
    def __init__(self,part,prefix='Ruri Endfield Uber '):
        super().__init__();self.name=prefix+part+' s0';self.library=None;self.nodes=Nodes();self.links=Links()
    def node(self,kind,name,**attrs):
        n=Node(kind,name);self.nodes.append(n)
        for k,v in attrs.items():setattr(n,k,v)
        return n

def fixture(part='Fur',prefix='Ruri Endfield Uber '):
    g=Group(part,prefix)
    camera=g.node('ShaderNodeVectorTransform','Vector Transform.003',vector_type='POINT',convert_from='CAMERA',convert_to='WORLD')
    obj=g.node('ShaderNodeVectorTransform','Vector Transform.004',vector_type='POINT',convert_from='WORLD',convert_to='OBJECT');g.links.new(camera.outputs[0],obj.inputs[0])
    surface=g.node('NodeGroupInput','Input');surface.outputs[0].name='input_positionWS'
    position=g.node('ShaderNodeVectorTransform','Vector Transform',vector_type='POINT',convert_from='WORLD',convert_to='OBJECT');g.links.new(surface.outputs[0],position.inputs[0])
    def swizzle(source,a,b):
        sep=g.node('ShaderNodeSeparateXYZ',a);combine=g.node('ShaderNodeCombineXYZ',b);g.links.new(source.outputs[0],sep.inputs[0])
        for i,j in enumerate((0,2,1)):g.links.new(sep.outputs[j],combine.inputs[i])
        return combine
    a=swizzle(obj,'Separate XYZ.003','Combine XYZ.004');b=swizzle(position,'Separate XYZ','Combine XYZ')
    sub=g.node('ShaderNodeVectorMath','Vector Math.005',operation='SUBTRACT');g.links.new(a.outputs[0],sub.inputs[0]);g.links.new(b.outputs[0],sub.inputs[1])
    norm=g.node('ShaderNodeVectorMath','Vector Math.006',operation='NORMALIZE');g.links.new(sub.outputs[0],norm.inputs[0])
    return g

def fingerprint(g):return ([n.name for n in g.nodes],sorted((l.from_socket.node.name,l.from_socket.name,l.to_socket.node.name,l.to_socket.name) for l in g.links),dict(g))
class ObservationContractTests(unittest.TestCase):
    def test_explicit_parts_aliases_and_idempotence(self):
        for part in ('Fur','VFX','LiquidAg','Eyes'):
            for prefix in ('Ruri Endfield Uber ','ENDF NPR-Shader Character '):
                with self.subTest(part=part,prefix=prefix):
                    g=fixture(part,prefix);self.assertEqual(vd.patch_group(g),1);state=fingerprint(g)
                    self.assertEqual(vd.patch_group(g),0);self.assertEqual(fingerprint(g),state)
    def test_reverse_sign_rejected_without_mutation(self):
        g=fixture();sub=g.nodes.get('Vector Math.005');a=sub.inputs[0].links[0].from_socket;b=sub.inputs[1].links[0].from_socket
        g.links.new(b,sub.inputs[0]);g.links.new(a,sub.inputs[1]);before=fingerprint(g)
        with self.assertRaises(ValueError):vd.patch_group(g)
        self.assertEqual(fingerprint(g),before)
    def test_wrong_axis_or_point_transform_rejected(self):
        for defect in ('axis','transform','camera-origin'):
            g=fixture('VFX')
            if defect=='axis':g.links.new(g.nodes.get('Separate XYZ').outputs[1],g.nodes.get('Combine XYZ').inputs[1])
            elif defect=='transform':g.nodes.get('Vector Transform.004').vector_type='VECTOR'
            else:g.nodes.get('Vector Transform.003').inputs[0].default_value=(1.,0.,0.)
            before=fingerprint(g)
            with self.assertRaises(ValueError):vd.patch_group(g)
            self.assertEqual(fingerprint(g),before)
    def test_linked_and_unknown_groups_not_modified(self):
        g=fixture();g.library=object();before=fingerprint(g)
        with self.assertRaises(ValueError):vd.patch_group(g)
        self.assertEqual(fingerprint(g),before)
        g=fixture('Eyebrow');before=fingerprint(g);self.assertEqual(vd.patch_group(g),0);self.assertEqual(fingerprint(g),before)
    def test_partial_node_allocation_failure_rolls_back(self):
        g=fixture('LiquidAg');before=fingerprint(g);g.nodes.fail_after=2
        with self.assertRaisesRegex(RuntimeError,'allocation'):vd.patch_group(g)
        self.assertEqual(fingerprint(g),before)
if __name__=='__main__':unittest.main()
