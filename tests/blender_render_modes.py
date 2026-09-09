"""Run on a freshly imported canonical instance; changes only its render mode."""
import bpy,json,hashlib
from array import array
from pathlib import Path
from endfield_bridge import render_modes as modes
from endfield_bridge.client import request

collection=modes.selected_collection(bpy.context)
assert collection and collection.get('sora_render_canonical'),'Select a freshly imported canonical instance'
objects=modes.meshes(collection)
owner=next(o for o in collection.objects if o.get('sora_asset'))
exe=bpy.path.abspath(bpy.context.preferences.addons['endfield_bridge'].preferences.executable)
document=request(exe,'scene',path=owner['sora_database'],asset=owner['sora_asset'],root=bpy.path.abspath(bpy.context.scene.sora.game_root))

def exhaust(work):
    for _ in work:pass

def geometry():
    digest=hashlib.sha256()
    for obj in collection.objects:
        digest.update(str(tuple(v for row in obj.matrix_world for v in row)).encode())
        if obj.type=='MESH':
            data=array('f',[0])*(3*len(obj.data.vertices));obj.data.vertices.foreach_get('co',data);digest.update(data.tobytes())
            if obj.data.shape_keys:
                for key in obj.data.shape_keys.key_blocks:
                    digest.update(str(key.value).encode());key.data.foreach_get('co',data);digest.update(data.tobytes())
        elif obj.type=='ARMATURE':
            for bone in obj.data.bones:digest.update(str(tuple(v for row in bone.matrix_local for v in row)).encode())
            if obj.animation_data and obj.animation_data.action:digest.update(str(obj.animation_data.action.as_pointer()).encode())
    return digest.hexdigest()

baseline=geometry()
other={(o.name,i):m.as_pointer() if m else None for o in bpy.context.scene.objects if o.type=='MESH' and o.get('sora_instance')!=collection['sora_instance'] for i,m in enumerate(o.data.materials)}
scene=bpy.context.scene
def post_state():
    tree=scene.compositing_node_group
    return (scene.view_settings.view_transform,scene.view_settings.look,scene.view_settings.exposure,
            scene.view_settings.gamma,scene.render.use_compositing,tree.as_pointer() if tree else None)
post=post_state()
original=collection[modes.MODE];alternate='BASIC' if original=='RURI' else 'RURI'
report={'samples':[]}
try:
    if not all('sora_render_'+alternate+'_count' in o for o in objects):
        before=(len(bpy.data.materials),len(bpy.data.images),len(bpy.data.node_groups))
        job=modes.switch_steps(bpy.context,collection,alternate,document)
        next(job);next(job);job.close()
        assert collection[modes.MODE]==original and geometry()==baseline
        report['cancel_counts']={'before':before,'after':(len(bpy.data.materials),len(bpy.data.images),len(bpy.data.node_groups))}
        assert report['cancel_counts']['before']==report['cancel_counts']['after'], 'Cancelled switch leaked owned data'
    material_ids={}
    for mode in (alternate,original,alternate,original):
        exhaust(modes.switch_steps(bpy.context,collection,mode,document))
        assert geometry()==baseline,'Geometry, rest, action or Shape Keys changed'
        ids=[m.as_pointer() if m else None for o in objects for m in o.data.materials]
        if mode in material_ids:assert ids==material_ids[mode],'Cached materials were rebuilt'
        material_ids[mode]=ids
        assert other=={(o.name,i):m.as_pointer() if m else None for o in bpy.context.scene.objects if o.type=='MESH' and o.get('sora_instance')!=collection['sora_instance'] for i,m in enumerate(o.data.materials)}
        assert post==post_state(), 'Instance switch changed scene compositor or color management'
        report['samples'].append({'mode':mode,'geometry':baseline,'materials':len(ids),'data_counts':(len(bpy.data.materials),len(bpy.data.images),len(bpy.data.node_groups))})
    report['ok']=True
finally:
    if collection[modes.MODE]!=original:exhaust(modes.switch_steps(bpy.context,collection,original,document))
    Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020/render-mode-regression.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
