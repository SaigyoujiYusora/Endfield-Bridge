"""Run through MCP on a freshly imported candidate character; restores test additions."""
import json
from pathlib import Path
import bpy
from endfield_bridge import pose_controls as pose
from endfield_bridge.animation_panel import target

rig=target(bpy.context)
assert rig is not None and pose.IMPORT in rig, 'Select a freshly imported candidate character'
assert pose.STATE not in rig, 'Restore existing pose session before running regression'
root=Path('F:/Games/Endfield-unpack/ENDF-DR/audit/v020')
mapping=json.loads((root/'localized-catalog-smoke.json').read_text())['poseMap']['response']['result']
pose.resolve(rig,mapping)
old_map=rig.get(pose.MAP)
rig[pose.MAP]=json.dumps(mapping)
body=pose.resolve(rig,mapping)
original_action=rig.animation_data.action if rig.animation_data else None
original_slot=rig.animation_data.action_slot if rig.animation_data else None
had_animation=rig.animation_data is not None
saved_bases={b.name:b.matrix_basis.copy() for b in rig.pose.bones}
saved_modes={b.name:b.rotation_mode for b in rig.pose.bones}
face_properties={k:rig[k] for k in rig.keys() if k.startswith(('sora_face','face_')) and isinstance(rig[k],(int,float,bool,str))}
shape_values={(o.name,k.name):k.value for o in bpy.context.scene.objects if o.type=='MESH' and o.get('sora_instance')==rig.get('sora_instance') and o.data.shape_keys for k in o.data.shape_keys.key_blocks}
constraint=None;track=None;action=None;equipment=None;driver=None
report={'samples':[],'playback_before':bpy.context.screen.is_animation_playing}
try:
    animation=rig.animation_data_create()
    action=bpy.data.actions.new('v020 pose regression')
    slot=action.slots.new(id_type='OBJECT',name=rig.name)
    bag=action.layers.new('Probe').strips.new(type='KEYFRAME').channelbags.new(slot)
    curve=bag.fcurves.new(data_path=body[14].path_from_id('location'),index=0)
    curve.keyframe_points.insert(1,0);curve.keyframe_points.insert(20,0.01)
    animation.action=action;animation.action_slot=slot
    track=animation.nla_tracks.new();track.name='v020 pose probe'
    strip=track.strips.new('v020 pose probe',1,action)
    constraint=body[14].constraints.new('LIMIT_ROTATION');constraint.name='v020 pose probe'
    rig['v020_pose_probe']=0.25
    driver=rig.driver_add('["v020_pose_probe"]');driver.driver.expression='0.25'
    equipment=bpy.data.objects.new('v020 pose attachment probe',None)
    bpy.context.scene.collection.objects.link(equipment)
    equipment.parent=rig;equipment.parent_type='BONE';equipment.parent_bone=body[18].name
    bpy.context.view_layer.update()
    equipment_before=equipment.matrix_world.copy()
    for mode in ('T','A','ORIGINAL','T','A'):
        pose.apply_pose(bpy.context,rig,mode)
        assert animation.action is None and track.mute and constraint.mute and driver.mute
        assert all(rig[k]==v for k,v in face_properties.items()), 'Face control properties changed'
        assert all(bpy.data.objects[o].data.shape_keys.key_blocks[k].value==v for (o,k),v in shape_values.items()), 'Shape Keys changed'
        report['samples'].append({'mode':mode,'measurements':json.loads(rig['sora_pose_measurements'])})
    pose.restore(bpy.context,rig)
    assert animation.action==action and not track.mute and not constraint.mute and not driver.mute
    assert pose.STATE not in rig
    bpy.context.view_layer.update()
    report['attachment_restore_max_error']=max(abs(equipment.matrix_world[r][c]-equipment_before[r][c]) for r in range(4) for c in range(4))
    assert report['attachment_restore_max_error']<1e-4
    report['playback_after']=bpy.context.screen.is_animation_playing
    assert report['playback_after']==report['playback_before']
    report['ok']=True
finally:
    if pose.STATE in rig:pose.restore(bpy.context,rig)
    if equipment:bpy.data.objects.remove(equipment,do_unlink=True)
    if driver:rig.driver_remove('["v020_pose_probe"]')
    if 'v020_pose_probe' in rig:del rig['v020_pose_probe']
    if constraint:body[14].constraints.remove(constraint)
    if track:rig.animation_data.nla_tracks.remove(track)
    if had_animation:
        rig.animation_data.action=original_action
        if original_slot:rig.animation_data.action_slot=original_slot
    else:rig.animation_data_clear()
    if action:bpy.data.actions.remove(action)
    for bone in rig.pose.bones:
        bone.rotation_mode=saved_modes[bone.name];bone.matrix_basis=saved_bases[bone.name]
    if old_map is None:del rig[pose.MAP]
    else:rig[pose.MAP]=old_map
    bpy.context.view_layer.update()
    (root/'pose-controls-regression.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
