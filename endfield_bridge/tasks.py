"""Responsive task runner: protocol IO in a worker, all RNA work on timer ticks."""
import queue
import time
import bpy
from bpy.app.handlers import persistent
from .client import request_task, close_sessions, CoreError

_active = None


def busy():
    return _active is not None


def start(operator, context, method, parameters, complete):
    global _active
    if busy():
        raise CoreError('Wait for or cancel the current task')
    preferences = context.preferences.addons[__package__].preferences
    task = request_task(bpy.path.abspath(preferences.executable), method, **parameters)
    return _attach(operator, context, method, complete, task)


def start_local(operator, context, stage, complete):
    from types import SimpleNamespace
    if busy():
        raise CoreError('Wait for or cancel the current task')
    events = queue.Queue()
    events.put({'ok': True, 'result': None})
    task = SimpleNamespace(events=events, process=SimpleNamespace(poll=lambda: 0),
                           cancel=lambda: None, terminate=lambda: None)
    return _attach(operator, context, stage, complete, task)


def start_batch(operator, context, requests, complete):
    from .client import BatchTask
    if busy():raise CoreError('Wait for or cancel the current task')
    preferences=context.preferences.addons[__package__].preferences
    task=BatchTask(bpy.path.abspath(preferences.executable),requests)
    return _attach(operator,context,'Loading owned render sources',complete,task)


def _attach(operator, context, method, complete, task):
    global _active
    operator._closed = False
    operator._window_manager = context.window_manager
    operator._task = task
    operator._complete = complete
    operator._steps = None
    operator._cancelled = False
    operator._cancel_time = None
    operator._mode = context.mode
    operator._scene = context.scene
    operator._window = context.window
    operator._view_layer = context.view_layer
    operator._override = dict(window=context.window, scene=context.scene, view_layer=context.view_layer)
    operator._timer = context.window_manager.event_timer_add(0.05, window=context.window)
    _active = operator
    settings = context.scene.sora
    settings.task_running = True
    settings.task_stage = 'Starting ' + method
    settings.status = settings.task_stage
    settings.task_detail = ''
    settings.task_tick = 0
    settings.task_total = 0
    settings.task_completed = 0
    settings.task_error = ''
    context.window_manager.modal_handler_add(operator)
    return {'RUNNING_MODAL'}


def cancel():
    if _active and not _active._cancelled:
        _active._cancelled = True
        _active._cancel_time = time.monotonic()
        _active._task.cancel()


def _finish(operator, context, error=None):
    global _active
    if getattr(operator, '_closed', False):
        return {'CANCELLED'}
    operator._closed = True
    errors = [str(error)] if error else []
    try:
        if operator._steps:
            try:
                with context.temp_override(**operator._override):
                    operator._steps.close()
            except Exception as cleanup_error:
                errors.append('rollback: ' + str(cleanup_error))
            finally:
                operator._steps = None
        if errors:
            try:
                operator._task.cancel()
                operator._task.terminate()
            except Exception as cleanup_error:
                errors.append('process cleanup: ' + str(cleanup_error))
        try:
            operator._window_manager.event_timer_remove(operator._timer)
        except Exception as cleanup_error:
            errors.append('timer cleanup: ' + str(cleanup_error))
        try:
            settings = operator._scene.sora
            settings.task_running = False
            if errors:
                settings.task_error = settings.task_stage + ': ' + '; '.join(errors)
                settings.status = settings.task_error
            else:
                settings.task_stage = 'Complete'
        except (AttributeError, ReferenceError) as cleanup_error:
            errors.append('task scene unavailable: ' + str(cleanup_error))
    finally:
        if _active is operator:
            _active = None
    if errors:
        message = '; '.join(errors)
        print('[Endfield-Bridge task] ' + message)
        try:
            operator.report({'WARNING'} if operator._cancelled else {'ERROR'}, message)
        except Exception as report_error:
            print('[Endfield-Bridge task report unavailable] ' + str(report_error))
    return {'CANCELLED'} if errors else {'FINISHED'}


class TaskOperator:
    def modal(self, context, event):
        if getattr(self, '_closed', False):
            return {'CANCELLED'}
        if event.type == 'ESC':
            cancel()
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        try:
            settings = self._scene.sora
            settings.task_tick = (settings.task_tick + 1) % 20000
            if (context.scene != self._scene or context.window != self._window
                    or context.view_layer != self._view_layer or context.mode != self._mode):
                self._cancelled = True
                return _finish(self, context, 'Scene, window, view layer or mode changed; task cancelled')
            if self._cancelled and (self._steps is not None or self._task.process.poll() is not None):
                return _finish(self, context, 'Cancelled; unfinished import rolled back')
            if self._cancelled and time.monotonic() - self._cancel_time > 3:
                self._task.terminate()
                return _finish(self, context, 'Cancelled')
            if self._steps is not None:
                try:
                    with context.temp_override(**self._override):
                        progress = next(self._steps)
                    settings.task_stage = progress['stage']
                    settings.task_detail = str(progress.get('detail') or '')
                    settings.task_completed = progress.get('completed', 0)
                    settings.task_total = progress.get('total', 0)
                except StopIteration:
                    self._steps = None
                    return _finish(self, context)
            else:
                while True:
                    try:
                        message = self._task.events.get_nowait()
                    except queue.Empty:
                        break
                    if message.get('event') == 'progress':
                        settings.task_stage = message.get('stage', 'Working')
                        settings.task_detail = str(message.get('detail') or '')
                        settings.task_completed = message.get('completed') or 0
                        settings.task_total = message.get('total') or 0
                    elif message.get('ok'):
                        if self._cancelled:
                            return _finish(self, context, 'Cancelled')
                        with context.temp_override(**self._override):
                            self._steps = self._complete(message['result'])
                        if self._steps is None:
                            return _finish(self, context)
                        break
                    else:
                        raise CoreError(message.get('error', {}).get('message', 'Task failed'))
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
            return {'RUNNING_MODAL'}
        except Exception as error:
            return _finish(self, context, error)


def shutdown():
    operator = _active
    if operator is not None:
        operator._cancelled = True
        _finish(operator, bpy.context, 'Task cancelled before addon unload or file load')
    close_sessions()


@persistent
def _before_load(_):
    shutdown()


@persistent
def _after_load(_):
    if not busy():
        for scene in bpy.data.scenes:
            settings = getattr(scene, 'sora', None)
            if settings is not None:
                settings.task_running = False


@persistent
def _before_save(_):
    shutdown()


def register():
    if _before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_before_save)
    if _before_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_before_load)
    if _after_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_after_load)


def unregister():
    shutdown()
    if _before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_before_save)
    if _before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_before_load)
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)
