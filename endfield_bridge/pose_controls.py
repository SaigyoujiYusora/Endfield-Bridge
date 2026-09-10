"""Instance-local constructed body poses using verified native human identities."""
import json
import math
import bpy
from bpy.props import StringProperty
from mathutils import Matrix, Vector
from . import tasks
from .tasks import TaskOperator

MAP = 'sora_pose_map'
IMPORT = 'sora_import_pose'
STATE = 'sora_pose_resume'
ACTION = 'sora_pose_resume_action'
ROOT_CHANNEL_SIZES = {'location':3, 'rotation_euler':3, 'rotation_quaternion':4,
                      'rotation_axis_angle':4, 'scale':3, 'delta_location':3,
                      'delta_rotation_euler':3, 'delta_rotation_quaternion':4, 'delta_scale':3}


def matrix_list(value):
    return [float(value[r][c]) for r in range(4) for c in range(4)]


def matrix(value):
    return Matrix([value[r*4:r*4+4] for r in range(4)])


def root_parent_frame(rig):
    return {'parent':rig.parent.name if rig.parent else None,
            'type':rig.parent_type, 'bone':rig.parent_bone,
            'vertices':list(rig.parent_vertices)}


def root_channels(rig):
    # Matrix assignment decomposes TRS and can alter an Euler float even when
    # restoring the same pose. Retain all raw channels, including inactive modes.
    return {'version':1, 'rotationMode':rig.rotation_mode,
            'channels':{key:list(getattr(rig,key)) for key in ROOT_CHANNEL_SIZES},
            'parentFrame':root_parent_frame(rig),
            'parentInverse':matrix_list(rig.matrix_parent_inverse)}


def validate_root_channels(rig, saved):
    if (not isinstance(saved,dict) or saved.get('version') != 1 or
        saved.get('rotationMode') not in {'QUATERNION','AXIS_ANGLE','XYZ','XZY','YXZ','YZX','ZXY','ZYX'} or
        saved.get('parentFrame') != root_parent_frame(rig)):
        raise ValueError('Suspended root transform frame changed or is invalid; state retained')
    channels = saved.get('channels',{})
    for key,size in {**ROOT_CHANNEL_SIZES,'parentInverse':16}.items():
        values = saved.get(key) if key == 'parentInverse' else channels.get(key)
        if (not isinstance(values,list) or len(values) != size or
            any(type(value) not in (int,float) or not math.isfinite(value) for value in values)):
            raise ValueError('Suspended raw root channels are invalid; state retained')


def restore_root(rig, state):
    saved = state.get('rootChannels')
    if saved is None:
        # Older saved pose sessions have no recoverable raw Euler/quaternion
        # channels. Retain their existing matrix-based compatibility behavior.
        rig.matrix_basis = matrix(state['rigBasis'])
        return
    validate_root_channels(rig,saved)
    rig.matrix_parent_inverse = matrix(saved['parentInverse'])
    rig.rotation_mode = saved['rotationMode']
    for key in ROOT_CHANNEL_SIZES:
        setattr(rig,key,saved['channels'][key])


def record_import_pose(rig):
    rig[IMPORT] = json.dumps({b.name: {'basis':matrix_list(b.matrix_basis), 'mode':b.rotation_mode}
                              for b in rig.pose.bones})


def resolve(rig, descriptor):
    result = {}
    for source in descriptor['bones']:
        matches = [b for b in rig.pose.bones if b.bone.get('sora_source_path') == source['path']
                   and str(b.bone.get('sora_source_hash')) == str(source['hash'])]
        if len(matches) != 1:
            raise ValueError('Humanoid bone path/hash is missing or ambiguous: ' + source['path'])
        result[source['slot']] = matches[0]
    if any(slot not in result for slot in range(22)):
        raise ValueError('The complete native25 body mapping is required')
    return result


def snapshot(rig):
    animation = rig.animation_data
    return {'bones':{b.name:{'basis':matrix_list(b.matrix_basis),'matrix':matrix_list(b.matrix),'mode':b.rotation_mode}
                     for b in rig.pose.bones},
            'rigBasis':matrix_list(rig.matrix_basis),'rigWorld':matrix_list(rig.matrix_world),
            'rootChannels':root_channels(rig),
            'slot':animation.action_slot.identifier if animation and animation.action_slot else None,
            'nla':[(t.name,t.mute) for t in animation.nla_tracks] if animation else [],
            'drivers':[(c.data_path,c.array_index,c.mute) for c in animation.drivers] if animation else [],
            'constraints':[(b.name,c.name,c.mute) for b in rig.pose.bones for c in b.constraints]
                          + [('',c.name,c.mute) for c in rig.constraints]}


def suspend(context, rig):
    if STATE in rig:
        return
    state = snapshot(rig)
    state['hadAction'] = bool(rig.animation_data and rig.animation_data.action)
    state['wasPlaying'] = bool(context.screen and context.screen.is_animation_playing)
    animation = rig.animation_data
    if animation and animation.action:
        rig[ACTION] = animation.action
    rig[STATE] = json.dumps(state)
    if animation:
        animation.action = None
        for track in animation.nla_tracks: track.mute = True
        for curve in animation.drivers: curve.mute = True
    for bone in rig.pose.bones:
        for constraint in bone.constraints: constraint.mute = True
    for constraint in rig.constraints: constraint.mute = True
    # Muting a root constraint/driver/Action can change its evaluated world
    # transform. Hold that evaluated frame only when needed; an unchanged root
    # must never undergo a gratuitous matrix -> Euler decomposition.
    context.view_layer.update()
    if matrix_list(rig.matrix_world) != state['rigWorld']:
        rig.matrix_world = matrix(state['rigWorld'])
        context.view_layer.update()
    for bone in rig.pose.bones:
        bone.matrix = matrix(state['bones'][bone.name]['matrix'])
    context.view_layer.update()


def restore(context, rig):
    if STATE not in rig:
        raise ValueError('No suspended animation state on this instance')
    state = json.loads(rig[STATE])
    animation = rig.animation_data
    nla = {t.name:t for t in animation.nla_tracks} if animation else {}
    drivers = {(c.data_path,c.array_index):c for c in animation.drivers} if animation else {}
    if animation and animation.action is not None:
        raise ValueError('An Action was assigned while pose mode was active; restore state retained')
    if state.get('hadAction') and not rig.get(ACTION):
        raise ValueError('The suspended Action was removed; restore state retained')
    if any(rig.pose.bones.get(name) is None for name in state['bones']):
        raise ValueError('Suspended skeleton changed; state retained')
    constraints = []
    for bone_name, name, mute in state['constraints']:
        owner = rig.pose.bones.get(bone_name) if bone_name else rig
        constraint = owner.constraints.get(name) if owner else None
        if constraint is None:
            raise ValueError('A suspended constraint was removed; animation state retained for recovery')
        if not constraint.mute:
            raise ValueError('A suspended constraint was changed; restore state retained')
        constraints.append((constraint,mute))
    if any(name not in nla for name,_ in state['nla']) or any((p,a) not in drivers for p,a,_ in state['drivers']):
        raise ValueError('Suspended NLA or driver identities changed; state retained for recovery')
    if any(not nla[name].mute for name,_ in state['nla']) or any(not drivers[(p,a)].mute for p,a,_ in state['drivers']):
        raise ValueError('Suspended NLA or driver mute state was changed; restore state retained')
    saved_action=rig.get(ACTION)
    saved_slot=next((s for s in saved_action.slots if s.identifier==state['slot']),None) if saved_action and state['slot'] else None
    if saved_action and state['slot'] and saved_slot is None:
        raise ValueError('Suspended Action slot was removed; restore state retained')
    if state.get('rootChannels') is not None:
        validate_root_channels(rig,state['rootChannels'])
    for name, saved in state['bones'].items():
        bone = rig.pose.bones.get(name)
        if bone is None: raise ValueError('Suspended skeleton changed; state retained')
        bone.rotation_mode = saved['mode']
        bone.matrix_basis = matrix(saved['basis'])
    restore_root(rig,state)
    if animation:
        animation.action = rig.get(ACTION)
        if animation.action and state['slot']:
            slot = next((s for s in animation.action.slots if s.identifier == state['slot']), None)
            if slot: animation.action_slot = slot
        for name, mute in state['nla']: nla[name].mute = mute
        for path, axis, mute in state['drivers']: drivers[(path,axis)].mute = mute
    for constraint,mute in constraints: constraint.mute = mute
    del rig[STATE]
    if ACTION in rig: del rig[ACTION]
    rig['sora_constructed_pose'] = ''
    context.scene.frame_set(context.scene.frame_current,subframe=context.scene.frame_subframe)


def aim(context, bone, child, direction):
    current = child.head - bone.head
    if current.length < 1e-6:
        raise ValueError('Degenerate humanoid arm segment')
    rotation = current.normalized().rotation_difference(direction)
    pivot = bone.head.copy()
    bone.matrix = Matrix.Translation(pivot) @ rotation.to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ bone.matrix
    context.view_layer.update()


def apply_pose(context, rig, mode):
    if IMPORT not in rig:
        raise ValueError('Reimport this instance to record its initial pose')
    body = resolve(rig,json.loads(rig[MAP])) if MAP in rig else None
    if mode not in {'ORIGINAL','A','T'}:
        raise ValueError('Unknown body pose')
    if body is None:
        raise ValueError('Load and validate native humanoid mapping first')
    previous = snapshot(rig)
    already = STATE in rig
    try:
        suspend(context,rig)
        # Only verified native body slots are reset. Facial bones, finger
        # controls, Shape Keys and their custom-property values are untouched.
        body_names={bone.name for bone in body.values()}
        for name,saved in json.loads(rig[IMPORT]).items():
            if name in body_names:
                bone=rig.pose.bones[name]
                bone.rotation_mode=saved['mode']
                bone.matrix_basis=matrix(saved['basis'])
        context.view_layer.update()
        measurements=[]
        if mode in {'A','T'}:
            from .pose_geometry import arm_directions
            directions=arm_directions(body[14].head,body[15].head,body[0].head,body[11].head,45 if mode=='A' else 0)
            for direction,slots in zip(directions,((14,16,18),(15,17,19))):
                direction=Vector(direction)
                lengths=[(body[slots[i+1]].head-body[slots[i]].head).length for i in range(2)]
                aim(context,body[slots[0]],body[slots[1]],direction)
                aim(context,body[slots[1]],body[slots[2]],direction)
                for i in range(2):
                    segment=body[slots[i+1]].head-body[slots[i]].head
                    alignment=segment.normalized().dot(direction)
                    ratio=segment.length/lengths[i]
                    if alignment<0.999 or abs(ratio-1)>0.001:
                        raise ValueError('Constructed arm pose failed direction/length validation')
                    measurements.append({'slot':slots[i],'alignment':alignment,'lengthRatio':ratio})
        rig['sora_constructed_pose']=mode
        rig['sora_pose_measurements']=json.dumps(measurements)
    except Exception:
        if not already and STATE in rig:
            restore(context,rig)
        else:
            for name,saved in previous['bones'].items():
                rig.pose.bones[name].rotation_mode=saved['mode']
                rig.pose.bones[name].matrix_basis=matrix(saved['basis'])
            context.view_layer.update()
        raise


class SORA_OT_pose_mapping(TaskOperator,bpy.types.Operator):
    bl_idname='sora.pose_mapping'
    bl_label='Validate native humanoid mapping'
    def execute(self,context):
        from .animation_panel import target
        rig=target(context)
        if rig is None:
            self.report({'ERROR'},'Select an imported character armature')
            return {'CANCELLED'}
        def complete(result):
            resolve(rig,result)
            rig[MAP]=json.dumps(result)
            context.scene.sora.status='Native humanoid mapping validated'
        try:
            return tasks.start(self,context,'pose-map',{'path':rig['sora_database'],'asset':rig['sora_asset'],
                'root':bpy.path.abspath(context.scene.sora.game_root)},complete)
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_pose(bpy.types.Operator):
    bl_idname='sora.body_pose'
    bl_label='Set body pose'
    bl_options={'REGISTER','UNDO'}
    mode:StringProperty(default='ORIGINAL')
    def execute(self,context):
        from .animation_panel import target
        rig=target(context)
        try:
            if rig is None: raise ValueError('Select an imported character armature')
            if self.mode=='RESUME': restore(context,rig)
            else: apply_pose(context,rig,self.mode)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_pose_display(bpy.types.Operator):
    bl_idname='sora.pose_display'
    bl_label='Toggle verified body-chain display'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        from .animation_panel import target
        rig=target(context)
        if rig is None or MAP not in rig or context.mode!='OBJECT':
            self.report({'ERROR'},'Select a mapped character in Object Mode')
            return {'CANCELLED'}
        existing=next((o for o in bpy.data.objects if o.get('sora_display_source')==rig),None)
        if existing:
            existing.hide_set(not existing.hide_get())
            return {'FINISHED'}
        helper=None;data=None
        active=context.view_layer.objects.active
        selected=list(context.selected_objects)
        try:
            body=resolve(rig,json.loads(rig[MAP]))
            edges=[(a,b) for chain in ((0,7,8,9,10,11),(12,14,16,18),(13,15,17,19),(0,1,3,5,20),(0,2,4,6,21))
                   for a,b in zip(chain,chain[1:])]
            data=bpy.data.armatures.new(rig.name+' Body Display')
            helper=bpy.data.objects.new(rig.name+' Body Display',data)
            helper['sora_instance']=rig['sora_instance']
            helper['sora_display_source']=rig
            helper.hide_render=True
            helper.parent=rig
            collection=next(c for c in rig.users_collection if c.get('sora_instance')==rig['sora_instance'])
            collection.objects.link(helper)
            for obj in selected:obj.select_set(False)
            helper.select_set(True);context.view_layer.objects.active=helper
            bpy.ops.object.mode_set(mode='EDIT')
            for a,b in edges:
                bone=data.edit_bones.new(f'Human {a} -> {b}')
                bone.head=body[a].head
                bone.tail=body[b].head
                bone.use_deform=False
            bpy.ops.object.mode_set(mode='OBJECT')
            for a,b in edges:
                bone=helper.pose.bones[f'Human {a} -> {b}']
                location=bone.constraints.new('COPY_LOCATION')
                location.target=rig;location.subtarget=body[a].name
                location.owner_space='POSE';location.target_space='POSE'
                stretch=bone.constraints.new('STRETCH_TO')
                stretch.target=rig;stretch.subtarget=body[b].name
                stretch.head_tail=0.0
                stretch.rest_length=bone.bone.length
            data.display_type='STICK';helper.show_in_front=True
            return {'FINISHED'}
        except Exception as error:
            if helper and helper.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            if helper:bpy.data.objects.remove(helper,do_unlink=True)
            if data and data.users==0:bpy.data.armatures.remove(data)
            self.report({'ERROR'},str(error));return {'CANCELLED'}
        finally:
            for obj in list(context.selected_objects):obj.select_set(False)
            for obj in selected:obj.select_set(True)
            context.view_layer.objects.active=active


def draw(layout,context):
    from .animation_panel import target
    rig=target(context)
    layout.operator('sora.pose_mapping')
    row=layout.row(align=True)
    row.enabled=rig is not None and IMPORT in rig and MAP in rig
    row.operator('sora.body_pose',text='Original body').mode='ORIGINAL'
    row=row.row(align=True)
    row.enabled=rig is not None and MAP in rig
    row.operator('sora.body_pose',text='A Pose').mode='A'
    row.operator('sora.body_pose',text='T Pose').mode='T'
    if rig and STATE in rig: layout.operator('sora.body_pose',text='Restore animation').mode='RESUME'
    layout.operator('sora.pose_display')
    layout.label(text='A/T: constructed from verified body axes')
    layout.label(text='Body only; Face values remain separate')


CLASSES=(SORA_OT_pose_mapping,SORA_OT_pose,SORA_OT_pose_display)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
def unregister():
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
