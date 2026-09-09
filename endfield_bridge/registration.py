"""Rollback registration metadata without touching restricted Blender data."""
import inspect
import sys


def owned_values(prefix):
    seen = set()
    for name, module in tuple(sys.modules.items()):
        if name == prefix or name.startswith(prefix + '.'):
            for value in vars(module).values():
                if (inspect.isclass(value) or inspect.isfunction(value)) and id(value) not in seen:
                    if (getattr(value, '__module__', '') or '').startswith(prefix):
                        seen.add(id(value))
                        yield value


class RegistrationTransaction:
    def __init__(self, bpy, prefix, properties):
        self.bpy, self.prefix, self.properties = bpy, prefix, properties
        self.registered = {value for value in owned_values(prefix)
                           if inspect.isclass(value) and getattr(value, 'is_registered', False)}
        self.timers = {value for value in owned_values(prefix)
                       if inspect.isfunction(value) and bpy.app.timers.is_registered(value)}
        self.handlers = [(value, list(value)) for name in dir(bpy.app.handlers)
                         if isinstance(value := getattr(bpy.app.handlers, name), list)]
        self.namespace = dict(bpy.app.driver_namespace)
        self.props = [(owner, name, hasattr(owner, name)) for owner, name in properties]

    def rollback(self):
        errors = []
        def attempt(callback):
            try:
                callback()
            except Exception as error:
                errors.append(str(error))
        for value in owned_values(self.prefix):
            if inspect.isfunction(value) and value not in self.timers and self.bpy.app.timers.is_registered(value):
                attempt(lambda value=value: self.bpy.app.timers.unregister(value))
        for handlers, previous in self.handlers:
            for value in list(handlers):
                if value not in previous and (getattr(value, '__module__', '') or '').startswith(self.prefix):
                    handlers.remove(value)
        for owner, name, existed in self.props:
            if not existed and hasattr(owner, name):
                attempt(lambda owner=owner, name=name: delattr(owner, name))
        classes = [value for value in owned_values(self.prefix)
                   if inspect.isclass(value) and value not in self.registered and getattr(value, 'is_registered', False)]
        for value in reversed(classes):
            attempt(lambda value=value: self.bpy.utils.unregister_class(value))
        namespace = self.bpy.app.driver_namespace
        for key, value in list(namespace.items()):
            if (getattr(value, '__module__', '') or '').startswith(self.prefix):
                if key in self.namespace:
                    namespace[key] = self.namespace[key]
                else:
                    del namespace[key]
        return errors
