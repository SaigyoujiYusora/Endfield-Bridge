"""Exercise the actual modal decision with a late database cancel and a terminal result."""
import ast
from contextlib import nullcontext
from pathlib import Path
import queue
from types import SimpleNamespace as NS
import unittest

class DatabaseCancelTests(unittest.TestCase):
    def run_modal(self, committed, database=True):
        source=Path(__file__).resolve().parents[1]/'endfield_bridge/tasks.py'
        tree=ast.parse(source.read_text(encoding='utf8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TaskOperator')
        completed=[];finished=[]
        env={'queue':queue,'CoreError':RuntimeError,'time':NS(monotonic=lambda:20),
             '_finish':lambda op,ctx,error=None:finished.append(error) or ({'CANCELLED'} if error else {'FINISHED'})}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),'<modal>','exec'),env)
        op=env['TaskOperator']();settings=NS(task_tick=0)
        context=NS(scene=NS(sora=settings),window=object(),view_layer=object(),mode='OBJECT',temp_override=lambda **kw:nullcontext())
        terminal={'ok':True,'committed':committed,'result':{'assets':3}}
        events=queue.Queue();events.put(terminal)
        op.__dict__.update(_closed=False,_database_build=database,_scene=context.scene,_window=context.window,
            _view_layer=context.view_layer,_mode=context.mode,_cancelled=True,_cancel_time=0,_steps=None,
            _task=NS(terminal=terminal,events=events,process=NS(poll=lambda:0),terminate=lambda:None),
            _override={},_complete=lambda result:completed.append(result))
        result=op.modal(context,NS(type='TIMER'))
        return result,completed,finished
    def test_committed_database_runs_completion_despite_late_cancel_timeout(self):
        result,completed,finished=self.run_modal(True)
        self.assertEqual(result,{'FINISHED'});self.assertEqual(completed,[{'assets':3}]);self.assertEqual(finished,[None])
    def test_uncommitted_cancel_does_not_complete(self):
        result,completed,_=self.run_modal(False)
        self.assertEqual(result,{'CANCELLED'});self.assertFalse(completed)
    def test_other_import_cancel_still_rolls_back(self):
        result,completed,finished=self.run_modal(True,database=False)
        self.assertEqual(result,{'CANCELLED'});self.assertFalse(completed);self.assertIn('rolled back',finished[0])

if __name__=='__main__':unittest.main()
