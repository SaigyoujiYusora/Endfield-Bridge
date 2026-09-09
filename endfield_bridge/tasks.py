"""Responsive task runner: protocol IO in a worker, all RNA work on timer ticks."""
import queue
import time
import bpy
from .client import request_task, close_sessions, CoreError

_active = None


def busy():
    return _active is not None


def start(operator, context, method, parameters, complete):
    global _active
    if busy():
        raise CoreError('Wait for or cancel the current task')
    preferences = context.preferences.addons[__package__].preferences
    operator._task = request_task(bpy.path.abspath(preferences.executable), method, **parameters)
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
    settings = operator._scene.sora
    if operator._steps:
        try:
            with context.temp_override(**operator._override):
                operator._steps.close()
        except Exception as cleanup_error:
            error = str(error or '') + '; rollback: ' + str(cleanup_error)
    if error:
        operator._task.cancel()
        operator._task.terminate()
    context.window_manager.event_timer_remove(operator._timer)
    settings.task_running = False
    if error:
        settings.task_error = settings.task_stage + ': ' + str(error)
        settings.status = settings.task_error
        operator.report({'WARNING'} if operator._cancelled else {'ERROR'}, str(error))
    else:
        settings.task_stage = 'Complete'
    _active = None
    return {'CANCELLED'} if error else {'FINISHED'}


class TaskOperator:
    def modal(self, context, event):
        if event.type == 'ESC':
            cancel()
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        settings = self._scene.sora
        settings.task_tick = (settings.task_tick + 1) % 20000
        try:
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
    close_sessions()
    if _active:
        cancel()
        _active._task.terminate()
        if _active._steps:
            _active._steps.close()
