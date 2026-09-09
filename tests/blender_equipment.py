"""Parent-run MCP regression on a fresh owner with source-index metadata."""
import bpy,json
from pathlib import Path
from endfield_bridge import equipment as equipment
from endfield_bridge.client import request

collection=equipment.owner_collection(bpy.context)
rig=equipment.owner_rig(collection) if collection else None
assert rig and rig.get('sora_asset'),'Select the imported owner'
exe=globals().get('EQUIPMENT_CORE',bpy.path.abspath(bpy.context.preferences.addons['endfield_bridge'].preferences.executable))
assembly=request(exe,'equipment-assembly',root=bpy.path.abspath(bpy.context.scene.sora.game_root),path=rig['sora_database'],asset=rig['sora_asset'])
report={'character':assembly['characterId'],'states':[]}
original_frame=bpy.context.scene.frame_current
original_action=rig.animation_data.action if rig.animation_data else None
before_owner=[(o.name,o.as_pointer()) for o in collection.objects]
if not equipment.owned_children(collection,'dedicated'):
    before_collections={c.as_pointer() for c in bpy.data.collections}
    work=equipment.create_dedicated_steps(bpy.context,collection,rig,assembly,collection.get('sora_render_mode'))
    next(work);next(work);work.close()
    assert before_collections=={c.as_pointer() for c in bpy.data.collections},'Cancelled dedicated import left a collection'
    assert before_owner==[(o.name,o.as_pointer()) for o in collection.objects]
    for _ in equipment.create_dedicated_steps(bpy.context,collection,rig,assembly,collection.get('sora_render_mode')):pass
children=equipment.owned_children(collection,'dedicated')
assert len({child['sora_instance'] for child in children})==len(children),'Slots share an instance token'
assert all(child['sora_owner_collection']==collection for child in children)
roots={}
for child in children:
    attachment=next(o for o in child.objects if o.get('sora_attachment_root'))
    roots.update({o.name:equipment.flatten(o.matrix_basis) for o in child.objects if o.parent==attachment})
prior_state=collection[equipment.STATE]
try:
    for state in ('fight','idle','fight','idle'):
        if not assembly['canBindStates'][state]:
            report['states'].append({'state':state,'blocked':True});continue
        equipment.apply_state(bpy.context,collection,state)
        slots={s['slotId']:s for s in assembly['slots']}
        for frame in (original_frame,original_frame+5,original_frame+15):
            bpy.context.scene.frame_set(frame)
            errors=[]
            for child in children:
                target=slots[child['sora_equipment_slot']][state]
                assert child.hide_render==(not target['visible'])
                if target['visible']:
                    attachment=next(o for o in child.objects if o.get('sora_attachment_root'))
                    bone=equipment.resolve_bone(rig,target)
                    expected=rig.matrix_world@bone.matrix@equipment.mat(target['localMatrix'])
                    error=max(abs(a-b) for a,b in zip(equipment.flatten(expected),equipment.flatten(attachment.matrix_world)))
                    assert error<0.002,(child.name,state,frame,error)
                    errors.append(error)
            report['states'].append({'state':state,'frame':frame,'maxAttachmentError':max(errors,default=0)})
        assert all(roots[name]==equipment.flatten(bpy.data.objects[name].matrix_basis) for name in roots),'Equipment source root basis changed'
    assert before_owner==[(o.name,o.as_pointer()) for o in collection.objects]
    assert (rig.animation_data.action if rig.animation_data else None)==original_action
    report['ok']=True
finally:
    equipment.apply_state(bpy.context,collection,prior_state)
    bpy.context.scene.frame_set(original_frame)
    Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020/equipment-static-regression.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
