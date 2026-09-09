"""Task protocol checks independent of Blender and a game installation."""
import importlib.util
import io
import json
from pathlib import Path
import queue
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('task_client', Path(__file__).resolve().parents[1] / 'endfield_bridge/client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class FakeProcess:
    def __init__(self, events):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(''.join(json.dumps(event) + '\n' for event in events))
        self.stderr = io.StringIO()
    def wait(self): return 0
    def poll(self): return 0


class TaskClientTests(unittest.TestCase):
    def run_task(self, events):
        process = FakeProcess(events)
        with patch.object(client.subprocess, 'Popen', return_value=process), patch('uuid.uuid4', return_value=types.SimpleNamespace(hex='known')):
            return client.CoreTask(str(Path(__file__).resolve()), 'search', path='test.sredb')

    def test_progress_then_result_preserves_actual_counts(self):
        task = self.run_task([
            {'protocol':1,'id':'known','event':'progress','stage':'Reading','completed':3,'total':17},
            {'protocol':1,'id':'known','ok':True,'result':{'rows':[]}}])
        self.assertEqual(task.events.get(timeout=2)['completed'], 3)
        self.assertEqual(task.events.get(timeout=2)['result'], {'rows':[]})

    def test_missing_terminal_result_is_failure(self):
        task = self.run_task([])
        self.assertIn('without a result', task.events.get(timeout=2)['error']['message'])

    def test_wrong_identity_is_rejected(self):
        task = self.run_task([{'protocol':1,'id':'another','ok':True,'result':{}}])
        self.assertIn('mismatch', task.events.get(timeout=2)['error']['message'])

    def test_backend_cancellation_remains_failure(self):
        task = self.run_task([{'protocol':1,'id':'known','ok':False,'error':{'code':'cancelled','message':'Cancelled'}}])
        result = task.events.get(timeout=2)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'cancelled')


if __name__ == '__main__': unittest.main()
