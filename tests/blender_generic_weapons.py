"""MCP regression on a fresh formally imported owner with dedicated slots loaded."""
import bpy,json
from pathlib import Path
from endfield_bridge import equipment as eq,generic_weapons as generic,render_modes as modes
from endfield_bridge.client import request
ROOT=Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020')
owner=eq.owner_collection(bpy.context)
assert owner and eq.CONTRACT in owner,'Use a formally imported owner with native equipment ownership'
assert not eq.owned_children(owner,'generic'),'Use a fresh owner without user-equipped generic weapons'
character=json.loads(owner[eq.CONTRACT])['characterId']
assembly=json.loads((ROOT/(character+'-generic-assembly.json')).read_text())
weapon=assembly['genericSlots'][0]['resourceId']
rig=eq.owner_rig(owner);prior_state=owner.get(eq.STATE,'idle')
original_action=rig.animation_data.action if rig.animation_data else None
frame=bpy.context.scene.frame_current
before_dedicated={c.name:c.as_pointer() for c in eq.owned_children(owner,'dedicated')}
report={'character':character,'states':[]}
def exhaust(work):
    for _ in work:pass
try:
    exhaust(generic.replace_steps(bpy.context,owner,weapon,assembly))
    current={c.name:c.as_pointer() for c in eq.owned_children(owner,'generic')}
    real_bind=generic.bind
    def fail_bind(*args,**kwargs):raise ValueError('Injected generic bind failure')
    generic.bind=fail_bind
    try:
        try:exhaust(generic.replace_steps(bpy.context,owner,weapon,assembly))
        except ValueError as error:assert 'Injected' in str(error)
        else:raise AssertionError('Expected generic bind failure')
    finally:generic.bind=real_bind
    assert current=={c.name:c.as_pointer() for c in eq.owned_children(owner,'generic')},'Failed replacement lost the old weapon'
    for state in ('fight','idle','fight'):
        eq.apply_state(bpy.context,owner,state)
        for f in (frame,frame+5):
            bpy.context.scene.frame_set(f)
            for slot in assembly['genericSlots']:
                target=slot[state]
                if not target['visible']:continue
                child=next(c for c in eq.owned_children(owner,'generic') if c['sora_equipment_slot']==slot['slotId'])
                attachment=next(o for o in child.objects if o.get('sora_attachment_root'))
                destination=generic.target_collection(owner,target)
                if target['targetKind']=='bone-head':
                    target_rig=eq.owner_rig(destination);bone=eq.resolve_bone(target_rig,target)
                    expected=target_rig.matrix_world@bone.matrix@eq.mat(target['localMatrix'])
                    error=max(abs(a-b) for a,b in zip(eq.flatten(expected),eq.flatten(attachment.matrix_world)))
                    assert error<0.002
                    report['states'].append({'state':state,'frame':f,'slot':slot['slotId'],'targetRole':target['targetRole'],'error':error})
    assert before_dedicated=={c.name:c.as_pointer() for c in eq.owned_children(owner,'dedicated')}
    assert original_action==(rig.animation_data.action if rig.animation_data else None)
    # Exercise the whole tree with exact current DTO sources, including generic children.
    exe=bpy.path.abspath(bpy.context.preferences.addons['endfield_bridge'].preferences.executable)
    packet=request(exe,'equipment-assembly',root=bpy.path.abspath(bpy.context.scene.sora.game_root),path=rig['sora_database'],asset=rig['sora_asset'],includeOwner=True)
    documents={owner.as_pointer():packet['scene']};resources={r['resourceId']:r['scene'] for r in packet['equipment']['resources']}
    for child in eq.owned_children(owner,'dedicated'):documents[child.as_pointer()]=resources[child['sora_equipment_resource']]
    for child in eq.owned_children(owner,'generic'):documents[child.as_pointer()]=assembly['scene']
    prior_modes={c.as_pointer():c[modes.MODE] for c in modes.owned_tree(owner)}
    for mode in ('BASIC','RURI'):
        exhaust(modes.switch_tree_steps(bpy.context,owner,mode,documents))
        assert all(c[modes.MODE]==mode for c in modes.owned_tree(owner))
    for child in modes.owned_tree(owner):exhaust(modes.switch_steps(bpy.context,child,prior_modes[child.as_pointer()],None))
    report['ok']=True
finally:
    eq.apply_state(bpy.context,owner,prior_state)
    bpy.context.scene.frame_set(frame)
    if not globals().get('KEEP_GENERIC',False):
        bpy.ops.sora.unequip_weapon('EXEC_DEFAULT',True)
        assert not eq.owned_children(owner,'generic')
        assert before_dedicated=={c.name:c.as_pointer() for c in eq.owned_children(owner,'dedicated')}
    (ROOT/'generic-weapon-frontend-regression.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
