"""MCP live test: set LIFECYCLE_TEST_KIND='load' for the file-load variant."""
import bpy,json,pathlib,addon_utils
from endfield_bridge import tasks
from endfield_bridge.tasks import TaskOperator

KIND=globals().get('LIFECYCLE_TEST_KIND','disable')
ROOT=pathlib.Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020')
PROBE='v020 task lifecycle probe'
class SORA_OT_lifecycle_probe(TaskOperator,bpy.types.Operator):
    bl_idname='sora.lifecycle_probe'
    bl_label='Lifecycle regression probe'
    def execute(self,context):
        def work(_):
            obj=bpy.data.objects.new(PROBE,None)
            context.scene.collection.objects.link(obj)
            try:
                while True:yield {'stage':'Lifecycle cancellation probe'}
            finally:
                bpy.data.objects.remove(obj,do_unlink=True)
        return tasks.start_local(self,context,'Lifecycle probe',work)

bpy.utils.register_class(SORA_OT_lifecycle_probe)
report={'kind':KIND}
def inspect_result():
    report.update(busy=tasks.busy(),task_running=bpy.context.scene.sora.task_running,
                  probe_remaining=bpy.data.objects.get(PROBE) is not None,
                  active_cleared=tasks._active is None)
    report['ok']=not report['busy'] and not report['task_running'] and not report['probe_remaining']
    (ROOT/('task-lifecycle-'+KIND+'.json')).write_text(json.dumps(report,indent=2))
    bpy.utils.unregister_class(SORA_OT_lifecycle_probe)
    return None

@bpy.app.handlers.persistent
def after_file(_):
    bpy.app.handlers.load_post.remove(after_file)
    bpy.app.timers.register(inspect_result,first_interval=0.1)

def interrupt():
    report['active_before']=tasks.busy()
    report['probe_before']=bpy.data.objects.get(PROBE) is not None
    if not report['probe_before']:return 0.1
    if KIND=='load':
        bpy.app.handlers.load_post.append(after_file)
        bpy.ops.wm.open_mainfile(filepath=str(ROOT/'before-candidate-install.blend'))
    else:
        errors=[]
        addon_utils.disable('endfield_bridge',default_set=False,handle_error=lambda err:errors.append(str(err)))
        addon_utils.enable('endfield_bridge',default_set=False,persistent=False,handle_error=lambda err:errors.append(str(err)))
        report['errors']=errors
        bpy.app.timers.register(inspect_result,first_interval=0.1)
    return None

bpy.ops.sora.lifecycle_probe('EXEC_DEFAULT')
bpy.app.timers.register(interrupt,first_interval=0.3)
print('Lifecycle probe scheduled: '+KIND)
