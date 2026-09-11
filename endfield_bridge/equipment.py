"""Instance-owned equipment assembly from explicit Core attachment contracts."""
import json
import math
import bpy
from bpy.props import StringProperty
from mathutils import Matrix
from . import tasks
from .tasks import TaskOperator
from .equipment_math import validate_wire,tail_compensated
from .equipment_initial_pose import validate_initial_pose_identity

CONTRACT='sora_equipment_contract'
STATE='sora_equipment_state'


def mat(values):return Matrix([values[r*4:r*4+4] for r in range(4)])
def flatten(value):return [float(value[r][c]) for r in range(4) for c in range(4)]


def apply_initial_equipment_pose(rig,resource):
    """Apply Core's source-derived initial pose only to a newly created child rig."""
    pose=resource.get('defaultPose')
    if pose is None:return
    if rig is None or rig.get('sora_equipment_resource')!=resource.get('resourceId') or rig.get('sora_resource_path')!=resource.get('resourcePath'):
        raise ValueError('Invalid native initial equipment pose')
    if rig.animation_data and (rig.animation_data.action or rig.animation_data.nla_tracks):
        raise ValueError('Initial equipment pose cannot overwrite an existing Action')
    sources,rows=validate_initial_pose_identity(resource,pose)
    checked=[]
    for row in rows:
        source=sources[row['bone']];bone=rig.pose.bones.get(source['name']);values=row.get('basisMatrix')
        if bone is None or type(bone.bone.get('sora_source_index')) is not int or bone.bone.get('sora_source_index')!=row['bone'] or bone.bone.get('sora_source_path')!=row.get('sourcePath'):
            raise ValueError('Initial equipment pose source bone identity differs')
        if not isinstance(values,list) or len(values)!=16 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in values):
            raise ValueError('Initial equipment pose matrix is invalid')
        target=mat(values);location,rotation,scale=target.decompose()
        rebuilt=Matrix.LocRotScale(location,rotation,scale)
        if max(abs(target[r][c]-rebuilt[r][c]) for r in range(4) for c in range(4))>0.0001:
            raise ValueError('Initial equipment pose contains unsupported shear')
        checked.append((bone,target))
    for bone,target in checked:bone.matrix_basis=target
    rig['sora_equipment_default_pose']=json.dumps({k:v for k,v in pose.items() if k!='bones'})


def owner_collection(context):
    obj=context.object
    if obj is None or not obj.get('sora_instance'):return None
    collection=next((c for c in obj.users_collection if c.get('sora_instance')==obj['sora_instance']),None)
    seen=set()
    while collection and collection.get('sora_owner_collection'):
        if collection.as_pointer() in seen:raise ValueError('Equipment ownership cycle')
        seen.add(collection.as_pointer());collection=collection['sora_owner_collection']
    if collection and not any(o.name in context.view_layer.objects for o in collection.objects):return None
    return collection


def owner_rig(collection):
    return next((o for o in collection.objects if o.type=='ARMATURE' and not o.get('sora_display_source')),None)


def owned_children(collection,role=None):
    return [child for child in collection.children if child.get('sora_owner_collection')==collection
            and (role is None or child.get('sora_equipment_role')==role)]


def resolve_bone(rig,state):
    path=state.get('parentSourcePath');index=state.get('parentBoneIndex')
    matches=[bone for bone in rig.pose.bones if bone.bone.get('sora_source_path')==path]
    if type(index) is not int or len(matches)!=1 or matches[0].bone.get('sora_source_index')!=index:
        raise ValueError('Equipment parent bone path/index does not match this imported rig')
    return matches[0]


def bind(context,rig,attachment,state,scale):
    validate_wire(state,scale)
    bone=resolve_bone(rig,state)
    canonical=next((c.get('sora_render_canonical') for c in rig.users_collection if c.get('sora_instance')==rig.get('sora_instance')),False)
    basis=Matrix.Diagonal((-1.,-1.,1.,1.)) if canonical else Matrix.Identity(4)
    expected_rest=basis@mat(state['parentBoneRestMatrix'])
    if max(abs(bone.bone.matrix_local[r][c]-expected_rest[r][c]) for r in range(4) for c in range(4))>0.002:
        raise ValueError('Equipment wire rest frame differs from this rig; reimport the owner')
    attachment.parent=rig;attachment.parent_type='BONE';attachment.parent_bone=bone.name
    attachment.matrix_parent_inverse=Matrix.Identity(4)
    attachment.matrix_basis=mat(tail_compensated(state['localMatrix'],bone.bone.length))
    context.view_layer.update()
    expected=rig.matrix_world@bone.matrix@mat(state['localMatrix'])
    error=max(abs(attachment.matrix_world[r][c]-expected[r][c]) for r in range(4) for c in range(4))
    if error>0.002:raise ValueError('Blender equipment bone-head attachment invariant failed: '+str(error))
    attachment['sora_attachment_node_id']=state['attachmentNodeId']
    attachment['sora_attachment_source_path']=state['attachmentSourcePath']
    attachment['sora_attachment_error']=error


def compact(assembly):
    return {**assembly,'resources':[{k:v for k,v in resource.items() if k!='scene'} for resource in assembly['resources']]}


def apply_state(context,collection,state):
    if state not in {'idle','fight'}:raise ValueError('Unknown equipment state')
    assembly=json.loads(collection[CONTRACT])
    if not assembly.get('canBindStates',{}).get(state):
        reasons=[gap['message'] for gap in assembly.get('gaps',[]) if gap.get('blocksBinding')]
        raise ValueError('Equipment state is not fully bindable: '+'; '.join(reasons))
    rig=owner_rig(collection)
    if rig is None:raise ValueError('Character armature is unavailable')
    slots={slot['slotId']:slot for slot in assembly['slots']}
    declarations={slot['slotId']:slot for slot in assembly['declaration']['dedicatedEquipment']}
    children=owned_children(collection,'dedicated')
    if set(slots)!={child.get('sora_equipment_slot') for child in children}:
        raise ValueError('Dedicated equipment slot ownership is incomplete')
    previous=[]
    try:
        for child in children:
            attachment=next((o for o in child.objects if o.get('sora_attachment_root')),None)
            if attachment is None:raise ValueError('Equipment attachment root is missing')
            previous.append((child,attachment,attachment.parent,attachment.parent_type,attachment.parent_bone,
                             attachment.matrix_basis.copy(),attachment.matrix_parent_inverse.copy(),child.hide_viewport,child.hide_render,
                             {key:attachment.get(key) for key in ('sora_attachment_node_id','sora_attachment_source_path','sora_attachment_error')}))
            child.hide_viewport=False
            target=slots[child['sora_equipment_slot']][state]
            if target.get('canBind'):bind(context,rig,attachment,target,declarations[child['sora_equipment_slot']]['scale'])
            elif target['visible']:raise ValueError('Visible equipment has no valid native attachment')
            child.hide_viewport=not target['visible'];child.hide_render=not target['visible']
        from . import generic_weapons
        if generic_weapons.CONTRACT in collection:
            generic_weapons.apply_state(context,collection,state)
        collection[STATE]=state
    except BaseException:
        for child,attachment,parent,parent_type,bone,basis,inverse,viewport,render,metadata in previous:
            attachment.parent=parent;attachment.parent_type=parent_type;attachment.parent_bone=bone
            attachment.matrix_parent_inverse=inverse;attachment.matrix_basis=basis
            child.hide_viewport=viewport;child.hide_render=render
            for key,value in metadata.items():
                if value is None:
                    if key in attachment:del attachment[key]
                else:attachment[key]=value
        raise


def create_dedicated_steps(context,collection,rig,assembly,material_mode=None,state='idle'):
    from .scene import create_scene_steps,remove_scene
    if owned_children(collection,'dedicated'):raise ValueError('Dedicated equipment is already associated')
    if rig is None:raise ValueError('Dedicated equipment requires its original owner armature')
    owner_path=rig.get('sora_resource_path') or rig.get('sora_asset')
    if owner_path!=assembly.get('ownerResourcePath'):
        raise ValueError('Equipment assembly owner resource differs from the imported character')
    if not assembly.get('canBindStates',{}).get(state):
        raise ValueError('Required dedicated equipment cannot bind in '+state+': '+'; '.join(g['message'] for g in assembly.get('gaps',[]) if g.get('blocksBinding')))
    resources={r['resourceId']:r for r in assembly['resources']}
    for slot in assembly['slots']:
        resource=resources.get(slot.get('resourceId'))
        if not resource or resource.get('scene') is None or resource.get('alreadyPresentMeshIds'):
            raise ValueError('Dedicated resource missing or overlaps owner renderer sources: '+slot['slotId'])
    created=[];prior=collection.get(CONTRACT)
    try:
        for index,slot in enumerate(assembly['slots']):
            yield {'stage':'Creating dedicated equipment slots','completed':index,'total':len(assembly['slots']),'detail':slot['slotId']}
            resource=resources[slot['resourceId']]
            child,child_rig=yield from create_scene_steps(context,resource['scene'],material_mode)
            created.append(child)
            if child_rig is not None:
                child_rig['sora_equipment_resource']=resource['resourceId']
                child_rig['sora_resource_path']=resource['resourcePath']
            apply_initial_equipment_pose(child_rig,resource)
            collection.children.link(child)
            context.scene.collection.children.unlink(child)
            child['sora_owner_collection']=collection
            child['sora_equipment_role']='dedicated';child['sora_equipment_slot']=slot['slotId']
            child['sora_equipment_resource']=slot['resourceId'];child['sora_equipment_path']=resource['resourcePath']
            attachment=bpy.data.objects.new(slot['slotId']+' attachment',None)
            child.objects.link(attachment)
            attachment['sora_instance']=child['sora_instance'];attachment['sora_attachment_root']=True
            attachment['sora_owner_collection']=collection
            for obj in list(child.objects):
                if obj==attachment:continue
                obj['sora_owner_collection']=collection
                obj['sora_asset']=slot['resourceId']
                obj['sora_database']=rig['sora_database']
                obj['sora_resource_path']=resource['resourcePath']
                if obj.parent is None:
                    basis=obj.matrix_basis.copy();obj.parent=attachment;obj.matrix_parent_inverse=Matrix.Identity(4);obj.matrix_basis=basis
            child.hide_viewport=True;child.hide_render=True
        collection[CONTRACT]=json.dumps(compact(assembly))
        apply_state(context,collection,state)
        collection['sora_equipment_can_idle']=bool(assembly['canBindStates'].get('idle'))
        collection['sora_equipment_can_fight']=bool(assembly['canBindStates'].get('fight'))
        collection['sora_equipment_status']='Static '+state+' ready; helper damping is not evaluated'
        from . import equipment_events
        collection[equipment_events.ENABLED]=True
        collection[equipment_events.STATUS]='身体事件跟随已开启；等待当前角色的原生身体动作'
    except BaseException:
        for child in reversed(created):remove_scene(context,child,force_cleanup=True)
        if prior is None:
            if CONTRACT in collection:del collection[CONTRACT]
        else:collection[CONTRACT]=prior
        raise


class SORA_OT_equipment_load(TaskOperator,bpy.types.Operator):
    bl_idname='sora.equipment_load'
    bl_label='Load native dedicated equipment'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        collection=owner_collection(context);rig=owner_rig(collection) if collection else None
        if rig is None or not rig.get('sora_asset'):
            self.report({'ERROR'},'Select the imported owner character');return {'CANCELLED'}
        def complete(assembly):
            yield from create_dedicated_steps(context,collection,rig,assembly,collection.get('sora_render_mode'))
            context.scene.sora.status=collection['sora_equipment_status']
        try:return tasks.start(self,context,'equipment-assembly',{'root':bpy.path.abspath(context.scene.sora.game_root),
            'path':rig['sora_database'],'asset':rig['sora_asset']},complete,
            prepare=tasks.native_texture_prepare(collection.get('sora_render_mode')))
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_equipment_state(bpy.types.Operator):
    bl_idname='sora.equipment_state'
    bl_label='Apply native static equipment state'
    bl_options={'REGISTER','UNDO'}
    state:StringProperty(default='idle')
    def execute(self,context):
        try:
            collection=owner_collection(context)
            if collection is None or CONTRACT not in collection:raise ValueError('Load dedicated equipment first')
            from . import equipment_events
            equipment_events.pause(collection)
            apply_state(context,collection,self.state)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


def draw(layout,context):
    collection=owner_collection(context)
    if collection is None:layout.label(text='Select an imported character');return
    layout.operator('sora.equipment_load')
    if CONTRACT in collection:
        row=layout.row(align=True)
        idle=row.row(align=True);idle.enabled=bool(collection.get('sora_equipment_can_idle'))
        idle.operator('sora.equipment_state',text='Idle (static)').state='idle'
        fight=row.row(align=True);fight.enabled=bool(collection.get('sora_equipment_can_fight'))
        fight.operator('sora.equipment_state',text='Fight (static)').state='fight'
        if not collection.get('sora_equipment_can_idle') or not collection.get('sora_equipment_can_fight'):
            layout.label(text='Disabled state: native attachment gaps')
        layout.label(text='Dedicated slots: '+str(len(owned_children(collection,'dedicated'))))
        from . import equipment_events
        equipment_events.draw(layout,context,collection)
        layout.label(text='Helper damping: not evaluated')
    else:layout.label(text=collection.get('sora_equipment_status','Dedicated equipment not associated'))
    from . import generic_weapons
    generic_weapons.draw(layout,context)
    from . import equipment_animation
    equipment_animation.draw(layout,context)

CLASSES=(SORA_OT_equipment_load,SORA_OT_equipment_state)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    from . import equipment_events
    equipment_events.register()
def unregister():
    from . import equipment_events
    equipment_events.unregister()
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
