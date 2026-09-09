"""Save/reopen verification after blender_render_modes.py populated both caches."""
import bpy,json,pathlib
from endfield_bridge import render_modes as modes
ROOT=pathlib.Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020')
collection=modes.selected_collection(bpy.context)
assert collection and all('sora_render_BASIC_count' in o and 'sora_render_RURI_count' in o for o in modes.meshes(collection))
token=collection['sora_instance'];original=collection[modes.MODE]
expected={mode:[[o.get('sora_render_'+mode+'_'+str(i)).name if o.get('sora_render_'+mode+'_'+str(i)) else None for i in range(o['sora_render_'+mode+'_count'])] for o in modes.meshes(collection)] for mode in ('RURI','BASIC')}

def finish():
    collection=next(c for c in bpy.data.collections if c.get('sora_instance')==token)
    report={'cache_names_preserved':True,'switches':[]}
    try:
        for mode in ('BASIC','RURI',original):
            for _ in modes.switch_steps(bpy.context,collection,mode,None):pass
            actual=[[m.name if m else None for m in o.data.materials] for o in modes.meshes(collection)]
            assert actual==expected[mode], 'Cached material IDs or assignments did not survive save/reopen'
            report['switches'].append(mode)
        report['ok']=True
    except Exception as error:
        report['error']=str(error)
    (ROOT/'render-modes-reopened.json').write_text(json.dumps(report,indent=2))
    return None

@bpy.app.handlers.persistent
def reopened(_):
    bpy.app.handlers.load_post.remove(reopened)
    bpy.app.timers.register(finish,first_interval=0.5)

path=str(ROOT/'render-modes-roundtrip.blend')
bpy.ops.wm.save_as_mainfile(filepath=path,copy=True)
bpy.app.handlers.load_post.append(reopened)
bpy.ops.wm.open_mainfile(filepath=path)
