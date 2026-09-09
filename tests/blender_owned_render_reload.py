"""Parent-run owned-tree cache save/reopen check after generic/render regression."""
import bpy,json,pathlib
from endfield_bridge import render_modes as modes
ROOT=pathlib.Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020')
root=modes.selected_collection(bpy.context);tree=modes.owned_tree(root)
assert all('sora_render_BASIC_count' in o and 'sora_render_RURI_count' in o for c in tree for o in modes.meshes(c))
root_name=root.name
prior={c.name:c[modes.MODE] for c in tree}
expected={c.name:{mode:[[o.get('sora_render_'+mode+'_'+str(i)).name if o.get('sora_render_'+mode+'_'+str(i)) else None for i in range(o['sora_render_'+mode+'_count'])] for o in modes.meshes(c)] for mode in ('RURI','BASIC')} for c in tree}

def finish():
    root=bpy.data.collections[root_name];report={'members':len(expected),'switches':[]}
    try:
        for mode in ('BASIC','RURI'):
            for _ in modes.switch_tree_steps(bpy.context,root,mode,{}):pass
            for collection in modes.owned_tree(root):
                actual=[[m.name if m else None for m in o.data.materials] for o in modes.meshes(collection)]
                assert actual==expected[collection.name][mode]
            report['switches'].append(mode)
        for collection in modes.owned_tree(root):
            for _ in modes.switch_steps(bpy.context,collection,prior[collection.name],None):pass
        report['ok']=True
    except Exception as error:report['error']=str(error)
    (ROOT/'owned-render-tree-reopened.json').write_text(json.dumps(report,indent=2))
    return None

@bpy.app.handlers.persistent
def reopened(_):
    bpy.app.handlers.load_post.remove(reopened)
    bpy.app.timers.register(finish,first_interval=0.5)
path=str(ROOT/'owned-render-tree-roundtrip.blend')
bpy.ops.wm.save_as_mainfile(filepath=path,copy=True)
bpy.app.handlers.load_post.append(reopened)
bpy.ops.wm.open_mainfile(filepath=path)
