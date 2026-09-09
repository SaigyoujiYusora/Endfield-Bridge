import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest

spec=importlib.util.spec_from_file_location('registration',Path(__file__).resolve().parents[1]/'endfield_bridge/registration.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class RegistrationTests(unittest.TestCase):
    def test_failed_registration_rolls_back_only_new_metadata(self):
        module=ModuleType('test_owned_addon')
        exec('class Existing: is_registered=True\nclass Added: is_registered=False\ndef handler(): pass\ndef timer(): pass\n',module.__dict__)
        sys.modules[module.__name__]=module
        scheduled=set()
        handlers=[]
        scene=type('Scene',(),{})
        bpy=NS(app=NS(timers=NS(is_registered=lambda f:f in scheduled,unregister=scheduled.remove),
                      handlers=NS(load_post=handlers),driver_namespace={}),
               utils=NS(unregister_class=lambda cls:setattr(cls,'is_registered',False)))
        try:
            transaction=m.RegistrationTransaction(bpy,module.__name__,[(scene,'sora')])
            module.Added.is_registered=True
            scene.sora=object()
            scheduled.add(module.timer)
            handlers.append(module.handler)
            bpy.app.driver_namespace['owned']=module.handler
            self.assertEqual(transaction.rollback(),[])
            self.assertTrue(module.Existing.is_registered)
            self.assertFalse(module.Added.is_registered)
            self.assertFalse(hasattr(scene,'sora'))
            self.assertEqual(scheduled,set())
            self.assertEqual(handlers,[])
            self.assertEqual(bpy.app.driver_namespace,{})
        finally:
            del sys.modules[module.__name__]

    def test_register_defers_data_migration(self):
        import ast
        tree=ast.parse((Path(__file__).resolve().parents[1]/'endfield_bridge/__init__.py').read_text(encoding='utf-8'))
        register=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='register')
        direct=[n.func.id for n in ast.walk(register) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertNotIn('migrate_saved_sources',direct)
        self.assertTrue(any(isinstance(n,ast.Name) and n.id=='_migrate_sources_timer' for n in ast.walk(register)))


if __name__=='__main__': unittest.main()
