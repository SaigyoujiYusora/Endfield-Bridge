import importlib.util
from pathlib import Path
import sys
import types
import unittest


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.previous = sys.modules.get('bpy')
        self.callbacks = set()
        self.registrations = []
        def register(callback, **options):
            self.registrations.append(callback)
            self.callbacks.add(callback)
        self.bpy = types.SimpleNamespace(app=types.SimpleNamespace(background=False, timers=types.SimpleNamespace(
            register=register, unregister=self.callbacks.remove, is_registered=self.callbacks.__contains__)))
        sys.modules['bpy'] = self.bpy
        spec = importlib.util.spec_from_file_location('parameter_upload_test', Path(__file__).resolve().parents[1] / 'endfield_bridge/parameter_upload.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.stack = types.SimpleNamespace(_flush_queued=[False], post=None, PANEL_KEY='face', writes=0, mirror=0, pixels=0)
        def flush():
            self.stack.writes += 1
            self.stack.pixels = self.stack.mirror
            self.stack._flush_queued[0] = False
        self.stack._param_flush = flush

    def tearDown(self):
        if self.previous is None:
            del sys.modules['bpy']
        else:
            sys.modules['bpy'] = self.previous

    def test_immediate_boundary_upload_and_stale_callback_are_coalesced(self):
        self.stack.mirror = 2
        self.module.schedule(self.stack)
        self.module.schedule(self.stack)
        self.assertEqual(len(self.registrations), 1)
        self.assertEqual(self.stack.pixels, 0)
        self.assertEqual(self.module.flush_pending([self.stack], {'face'}), 1)
        self.assertEqual(self.stack.pixels, 2)
        callback = self.registrations[0]
        callback()
        self.assertEqual(self.module.flush_pending([self.stack], {'face'}), 0)
        self.assertEqual(self.stack.writes, 1)
        # A new edit before the old timer fires reuses that stable callback.
        self.stack.mirror = 3
        self.module.schedule(self.stack)
        self.assertEqual(len(self.registrations), 1)
        callback()
        self.assertEqual(self.stack.pixels, 3)
        self.assertEqual(self.stack.writes, 2)
        self.callbacks.remove(callback)  # Timer dispatcher removes a None result.
        self.module.schedule(self.stack)
        self.assertEqual(len(self.registrations), 2)
        self.assertIs(self.registrations[1], callback)

    def test_unused_stack_is_not_uploaded_and_cancel_unregisters(self):
        self.module.schedule(self.stack)
        self.assertEqual(self.module.flush_pending([self.stack], {'other'}), 0)
        self.assertTrue(self.stack._flush_queued[0])
        self.module.cancel(self.stack)
        self.assertFalse(self.callbacks)
        self.assertEqual(self.stack.writes, 0)

    def test_background_upload_is_immediate(self):
        self.bpy.app.background = True
        self.stack.mirror = 4
        self.module.schedule(self.stack)
        self.assertEqual(self.stack.pixels, 4)
        self.assertFalse(self.registrations)


if __name__ == '__main__':
    unittest.main()
