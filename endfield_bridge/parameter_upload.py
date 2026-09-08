"""Coalesce deferred table uploads and drain pending writes before rendering."""
import bpy


def schedule(stack):
    if bpy.app.background:
        stack._param_flush()
        return
    stack._flush_queued[0] = True
    callback = getattr(stack, '_endf_param_flush_callback', None)
    if callback is None:
        def callback():
            # An explicit render-boundary flush may have already uploaded this
            # mirror. A stale timer must not upload or tag the image again.
            if stack._flush_queued[0]:
                stack._param_flush()
            return None
        stack._endf_param_flush_callback = callback
    if not bpy.app.timers.is_registered(callback):
        bpy.app.timers.register(callback, first_interval=0.1)


def flush_pending(stacks, used_keys):
    changed = 0
    for stack in stacks:
        if stack.post is None and stack.PANEL_KEY in used_keys and stack._flush_queued[0]:
            stack._param_flush()
            changed += 1
    return changed


def cancel(stack):
    callback = getattr(stack, '_endf_param_flush_callback', None)
    if callback is not None and bpy.app.timers.is_registered(callback):
        bpy.app.timers.unregister(callback)
