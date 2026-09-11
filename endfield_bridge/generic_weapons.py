"""Typed native-compatible generic weapon slots, isolated from dedicated slots."""
import json
import bpy
from bpy.props import StringProperty,IntProperty,BoolProperty,CollectionProperty,PointerProperty
from mathutils import Matrix
from . import equipment as eq,tasks
from .tasks import TaskOperator

CONTRACT='sora_generic_weapon_contract'


def state_scale(state):
    parent=state.get('parentBoneRestMatrix') if state.get('targetKind','bone-head')=='bone-head' else state.get('parentNodeRestMatrix')
    if parent is None or state.get('nativeMountWorldInScene') is None or state.get('localMatrix') is None:
        raise ValueError('Typed generic attachment proof is incomplete')
    delta=eq.mat(state['nativeMountWorldInScene']).inverted()@eq.mat(parent)@eq.mat(state['localMatrix'])
    scale=float(delta[0][0]);expected=Matrix.Diagonal((scale,scale,scale,1.0))
    if scale<=0 or max(abs(delta[r][c]-expected[r][c]) for r in range(4) for c in range(4))>0.001:
        raise ValueError('Generic attachment scale proof is not uniform')
    return scale


def target_collection(owner,state):
    role=state.get('targetRole','owner')
    if role=='owner':return owner
    if role!='dedicated':raise ValueError('Unsupported generic target role')
    matches=[c for c in eq.owned_children(owner,'dedicated') if c.get('sora_equipment_slot')==state.get('targetDedicatedSlotId')]
    if len(matches)!=1:raise ValueError('Required dedicated parent slot is not present exactly once')
    return matches[0]


def bind(context,owner,attachment,state):
    target=target_collection(owner,state)
    scale=state_scale(state)
    if state.get('targetKind','bone-head')=='bone-head':
        rig=eq.owner_rig(target)
        if rig is None:raise ValueError('Generic target has no source skeleton')
        eq.bind(context,rig,attachment,state,scale)
    elif state['targetKind']=='scene-node':
        candidates=[o for o in target.objects if o.get('sora_node_id')==state.get('parentNodeId')
                    and o.get('sora_source_path')==state.get('parentSourcePath')]
        if len(candidates)!=1:raise ValueError('Generic weapon mount node ID/path is missing or ambiguous')
        node=candidates[0]
        proof=dict(state,parentBoneRestMatrix=state['parentNodeRestMatrix'])
        eq.validate_wire(proof,scale)
        attachment.parent=node;attachment.parent_type='OBJECT';attachment.parent_bone=''
        attachment.matrix_parent_inverse=Matrix.Identity(4);attachment.matrix_basis=eq.mat(state['localMatrix'])
        context.view_layer.update()
        expected=node.matrix_world@eq.mat(state['localMatrix'])
        error=max(abs(a-b) for a,b in zip(eq.flatten(expected),eq.flatten(attachment.matrix_world)))
        if error>0.002:raise ValueError('Generic scene-node world attachment invariant failed')
        attachment['sora_attachment_error']=error
    else:raise ValueError('Unsupported typed generic target kind')


def apply_state(context,owner,state,contract=None,children=None):
    contract=contract or json.loads(owner[CONTRACT])
    children=eq.owned_children(owner,'generic') if children is None else children
    if not contract['canBindStates'].get(state):raise ValueError('Generic weapon state has unresolved native mount gaps')
    slots={s['slotId']:s for s in contract['genericSlots']}
    if set(slots)!={c.get('sora_equipment_slot') for c in children}:raise ValueError('Generic slot ownership is incomplete')
    saved=[]
    try:
        for child in children:
            attachment=next(o for o in child.objects if o.get('sora_attachment_root'))
            saved.append((child,attachment,attachment.parent,attachment.parent_type,attachment.parent_bone,
                          attachment.matrix_parent_inverse.copy(),attachment.matrix_basis.copy(),child.hide_viewport,child.hide_render))
            child.hide_viewport=False
            target=slots[child['sora_equipment_slot']][state]
            if target.get('canBind'):bind(context,owner,attachment,target)
            elif target['visible']:raise ValueError('Visible generic weapon has no verified attachment')
            child.hide_viewport=not target['visible'];child.hide_render=not target['visible']
    except BaseException:
        for child,obj,parent,kind,bone,inverse,basis,viewport,render in saved:
            obj.parent=parent;obj.parent_type=kind;obj.parent_bone=bone;obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
            child.hide_viewport=viewport;child.hide_render=render
        raise


def replace_steps(context,owner,weapon,assembly):
    from .scene import create_scene_steps,remove_scene,_validate_remove_tree
    if assembly['compatibility']['status']!='native-type-match' or assembly.get('scene') is None:
        raise ValueError(assembly['compatibility']['message'])
    if eq.CONTRACT not in owner or json.loads(owner[eq.CONTRACT])['characterId']!=assembly['compatibility']['characterId']:
        raise ValueError('Load native dedicated-equipment ownership for this character first')
    state=owner.get(eq.STATE,'idle')
    if not assembly['canBindStates'].get(state):raise ValueError('Selected weapon has unresolved native mounts in '+state)
    old=eq.owned_children(owner,'generic')
    for child in old:_validate_remove_tree(child)
    for slot in assembly['genericSlots']:
        target=slot[state]
        if target.get('canBind'):target_collection(owner,target);state_scale(target)
        elif target['visible']:raise ValueError('Generic weapon target is unresolved')
    rig=eq.owner_rig(owner);created=[]
    try:
        for index,slot in enumerate(assembly['genericSlots']):
            yield {'stage':'Creating generic weapon slots','completed':index,'total':len(assembly['genericSlots'])}
            child,_=yield from create_scene_steps(context,assembly['scene'],owner.get('sora_render_mode'))
            created.append(child);owner.children.link(child);context.scene.collection.children.unlink(child)
            child['sora_owner_collection']=owner;child['sora_equipment_role']='generic';child['sora_equipment_slot']=slot['slotId']
            child['sora_equipment_resource']=weapon
            root=bpy.data.objects.new(slot['slotId']+' attachment',None);child.objects.link(root)
            root['sora_instance']=child['sora_instance'];root['sora_attachment_root']=True;root['sora_owner_collection']=owner
            for obj in list(child.objects):
                if obj==root:continue
                obj['sora_owner_collection']=owner;obj['sora_asset']=weapon;obj['sora_database']=rig['sora_database']
                if obj.parent is None:
                    basis=obj.matrix_basis.copy();obj.parent=root;obj.matrix_parent_inverse=Matrix.Identity(4);obj.matrix_basis=basis
            child.hide_viewport=True;child.hide_render=True
        apply_state(context,owner,state,assembly,created)
        for child in old:_validate_remove_tree(child)
        for child in old:remove_scene(context,child,force_cleanup=True)
        owner[CONTRACT]=json.dumps({**assembly,'scene':None,'weapon':weapon})
        if context.view_layer.objects.active is None:
            rig.select_set(True);context.view_layer.objects.active=rig
    except BaseException:
        for child in reversed(created):remove_scene(context,child,force_cleanup=True)
        raise


class SORA_CompatibleWeapon(bpy.types.PropertyGroup):
    identity:StringProperty()
    internal_name:StringProperty()
    can_attempt:BoolProperty()
    reason:StringProperty()
class SORA_WeaponSettings(bpy.types.PropertyGroup):
    query:StringProperty(name='Compatible weapon search')
    rows:CollectionProperty(type=SORA_CompatibleWeapon)
    selected:IntProperty(default=-1)
    offset:IntProperty(default=0)
    total:IntProperty(default=0)
    owner:PointerProperty(type=bpy.types.Collection)
    status:StringProperty()


class SORA_OT_weapon_search(TaskOperator,bpy.types.Operator):
    bl_idname='sora.compatible_weapons'
    bl_label='Find native-compatible weapons'
    direction:IntProperty(default=0)
    def execute(self,context):
        owner=eq.owner_collection(context);rig=eq.owner_rig(owner) if owner else None
        if rig is None:self.report({'ERROR'},'Select a character');return {'CANCELLED'}
        settings=context.scene.sora_weapons;query=settings.query
        offset=max(0,settings.offset+self.direction*30) if self.direction and settings.owner==owner else 0
        def complete(result):
            if eq.owner_collection(context)!=owner or settings.query!=query:raise ValueError('Weapon query owner/filter changed')
            settings.rows.clear()
            for source in result['rows']:
                row=settings.rows.add();row.identity=source['id'];row.name=source['label']+' / '+source['internalName']
                row.internal_name=source['internalName'];row.can_attempt=source['capability'].get('canAttemptImport',source['capability']['canImport'])
                row.reason=source['capability']['reason']
            settings.selected=0 if settings.rows else -1;settings.owner=owner;settings.offset=result['offset'];settings.total=result['total']
            settings.status=result['compatibility']['message'];context.scene.sora.status=settings.status
        try:return tasks.start(self,context,'compatible-weapons',{'root':bpy.path.abspath(context.scene.sora.game_root),
            'path':rig['sora_database'],'asset':rig['sora_asset'],'query':query,'offset':offset,'limit':30},complete)
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_weapon_equip(TaskOperator,bpy.types.Operator):
    bl_idname='sora.equip_weapon'
    bl_label='Equip selected generic weapon'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        owner=eq.owner_collection(context);settings=context.scene.sora_weapons
        if owner!=settings.owner or not 0<=settings.selected<len(settings.rows):
            self.report({'ERROR'},'Search and select a compatible weapon for this instance');return {'CANCELLED'}
        row=settings.rows[settings.selected];weapon=row.identity;rig=eq.owner_rig(owner)
        if not row.can_attempt:self.report({'ERROR'},row.reason);return {'CANCELLED'}
        def complete(result):
            yield from replace_steps(context,owner,weapon,result)
            context.scene.sora.status='Generic weapon equipped; current instance body-event following uses its declared slot'
        try:return tasks.start(self,context,'weapon-assembly',{'root':bpy.path.abspath(context.scene.sora.game_root),
            'path':rig['sora_database'],'asset':rig['sora_asset'],'weapon':weapon},complete,
            prepare=tasks.native_texture_prepare(owner.get('sora_render_mode')))
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_weapon_remove(bpy.types.Operator):
    bl_idname='sora.unequip_weapon'
    bl_label='Unequip generic weapon only'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        from .scene import remove_scene,_validate_remove_tree
        try:
            owner=eq.owner_collection(context)
            if owner is None:raise ValueError('Select the owner character')
            children=eq.owned_children(owner,'generic')
            for child in children:_validate_remove_tree(child)
            for child in children:remove_scene(context,child)
            if CONTRACT in owner:del owner[CONTRACT]
            if context.view_layer.objects.active is None:
                rig=eq.owner_rig(owner)
                if rig:rig.select_set(True);context.view_layer.objects.active=rig
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


def draw(layout,context):
    settings=context.scene.sora_weapons
    layout.label(text='Generic weapons (separate from dedicated slots)')
    layout.prop(settings,'query');layout.operator('sora.compatible_weapons')
    layout.template_list('UI_UL_list','compatible_weapons',settings,'rows',settings,'selected',rows=3)
    row=layout.row(align=True)
    prev=row.row();prev.enabled=settings.offset>0;prev.operator('sora.compatible_weapons',text='',icon='TRIA_LEFT').direction=-1
    row.label(text=f'{settings.offset+len(settings.rows)} / {settings.total}')
    following=row.row();following.enabled=settings.offset+len(settings.rows)<settings.total;following.operator('sora.compatible_weapons',text='',icon='TRIA_RIGHT').direction=1
    layout.operator('sora.equip_weapon');layout.operator('sora.unequip_weapon')

CLASSES=(SORA_CompatibleWeapon,SORA_WeaponSettings,SORA_OT_weapon_search,SORA_OT_weapon_equip,SORA_OT_weapon_remove)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Scene.sora_weapons=PointerProperty(type=SORA_WeaponSettings)
def unregister():
    del bpy.types.Scene.sora_weapons
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
