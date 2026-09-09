import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

source=ast.parse((Path(__file__).resolve().parents[1]/'endfield_bridge/render_modes.py').read_text())
namespace={'json':json,'hashlib':hashlib,'MODE':'sora_render_mode'}
exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in {'cache','activate','source_signature'}],type_ignores=[]),'<render-cache>','exec'),namespace)

class Mods(list):
    def get(self,name):return next((m for m in self if m.name==name),None)
class Obj(dict): pass

def obj():
    o=Obj(sora_instance='one')
    o.data=NS(materials=[{'name':'NPR user setting'}],polygons=[NS(material_index=0)])
    o.modifiers=Mods([NS(name='NPR vertex',type='NODES',node_group={'sora_instance':'one'},show_viewport=True,show_render=True)])
    return o

class RenderCacheTests(unittest.TestCase):
    def test_round_trip_retains_material_identity_and_modifier_flags(self):
        o=obj();npr=o.data.materials[0]
        namespace['cache'](o,'RURI')
        basic={'name':'Basic user setting'};o.data.materials[:]=[basic]
        namespace['cache'](o,'BASIC')
        for _ in range(3):
            namespace['activate'](o,'BASIC')
            self.assertIs(o.data.materials[0],basic);self.assertFalse(o.modifiers[0].show_render)
            namespace['activate'](o,'RURI')
            self.assertIs(o.data.materials[0],npr);self.assertTrue(o.modifiers[0].show_render)

    def test_deleted_cached_material_fails_before_slot_mutation(self):
        o=obj();namespace['cache'](o,'RURI');old=list(o.data.materials)
        del o['sora_render_RURI_0']
        with self.assertRaises(ValueError):namespace['activate'](o,'RURI')
        self.assertEqual(o.data.materials,old)

    def test_texture_payload_and_material_changes_invalidate_source(self):
        document={'materials':[{'name':'m'}],'textures':[{'name':'t','png':'YWJj'}],'meshes':[{'name':'mesh','material':0}]}
        original=namespace['source_signature'](document)
        document['textures'][0]['png']='eHl6'
        self.assertNotEqual(original,namespace['source_signature'](document))
        document['textures'][0]['png']='YWJj';document['materials'][0]['name']='new'
        self.assertNotEqual(original,namespace['source_signature'](document))

if __name__=='__main__':unittest.main()
