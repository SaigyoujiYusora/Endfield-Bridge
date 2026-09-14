"""Terminal state must be visible without forcing a draw or relying on another event."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]/'endfield_bridge'
tree=ast.parse((ROOT/'tasks.py').read_text(encoding='utf8'))
code=compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'_owns_active','_finish','_redraw_task_views'}],type_ignores=[]),'<terminal functions>','exec')

class Released:
    def __getattr__(self,key):raise ReferenceError('released RNA')

class TerminalRedrawTests(unittest.TestCase):
    def test_success_failure_and_cancel_request_draw_after_terminal_state(self):
        for error,cancelled in [(None,False),('decode failed',False),('Cancelled',True)]:
            with self.subTest(error=error):
                settings=NS(task_running=True,task_stage='decode',task_error='',status='old')
                seen=[];ns={};exec(code,ns)
                area=NS(type='VIEW_3D',tag_redraw=lambda:seen.append((settings.task_running,settings.task_stage,ns['_active'])))
                wm=NS(windows=[NS(screen=NS(areas=[area]))],event_timer_remove=lambda _:None)
                operator=NS(_closed=False,_steps=None,_task=NS(cancel=lambda:None,terminate=lambda:None),_timer=object(),_window_manager=wm,_scene=NS(sora=settings),_cancelled=cancelled,report=lambda *args:None)
                ns['_active']=operator;result=ns['_finish'](operator,NS(window_manager=wm),error)
                self.assertEqual(result,{'CANCELLED'} if error else {'FINISHED'})
                self.assertEqual(seen,[(False,'decode' if error else 'Complete',None)])
                self.assertEqual(settings.task_error,'decode: '+error if error else '')
    def test_released_scene_window_and_area_do_not_block_other_view_redraw(self):
        ns={};exec(code,ns);seen=[]
        area=NS(type='VIEW_3D',tag_redraw=lambda:seen.append(True))
        wm=NS(windows=[Released(),NS(screen=NS(areas=[Released(),NS(type='IMAGE_EDITOR'),area]))],event_timer_remove=lambda _:None)
        op=NS(_closed=False,_steps=None,_task=NS(cancel=lambda:None,terminate=lambda:None),_timer=None,_window_manager=wm,_scene=Released(),_cancelled=False,report=lambda *args:None)
        ns['_active']=op;self.assertEqual(ns['_finish'](op,NS(window_manager=wm)),{'CANCELLED'});self.assertEqual(seen,[True])
        ns['_redraw_task_views'](Released())
    def test_equipment_status_precedes_controls_even_for_other_owner(self):
        tree=ast.parse((ROOT/'equipment_animation.py').read_text(encoding='utf8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='draw')
        function.body=[n for n in function.body if not isinstance(n,ast.ImportFrom)]
        events=[]
        class Layout:
            def box(self):return self
            def row(self):return self
            def label(self,**kwargs):events.append(('label',kwargs['text']))
            def operator(self,name):events.append(('operator',name))
        settings=NS(status='当前后端不支持专用装备片段',owner=object())
        ns={'wrapped_label':lambda box,text,context:events.append(('wrapped',text)),'tasks':NS(busy=lambda:False),'eq':NS(owner_collection=lambda _:None)}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<draw>','exec'),ns)
        ns['draw'](Layout(),NS(scene=NS(sora_equipment_animation=settings,sora=NS(task_error='native error'))))
        first_operator=next(i for i,x in enumerate(events) if x[0]=='operator')
        self.assertLess(events.index(('wrapped',settings.status)),first_operator)
        self.assertLess(events.index(('wrapped','最近任务错误：native error')),first_operator)

if __name__=='__main__':unittest.main()
