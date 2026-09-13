"""Typed native-compatible generic weapon slots, isolated from dedicated slots."""
import json
import bpy
from bpy.props import StringProperty,IntProperty,BoolProperty,CollectionProperty,PointerProperty
from mathutils import Matrix
from . import equipment as eq,tasks
from .tasks import TaskOperator

CONTRACT='sora_generic_weapon_contract'

# Core marks a resource as mount-verified either by an exact WeaponBasicTable match or by an inferred refined
# type joined to the owner's native dynamic slot and a parsed resource; both are valid mount contracts.
MOUNT_VERIFIED_STATUSES={'native-type-match','inferred-base-type-mount-verified'}


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
    # A declared mount point 0 is the owner main model root (AbilitySystem.battleRoot), which the owner instance
    # itself represents; only a real dedicated-equipment slot is resolved through its owned child collection.
    if state.get('targetKind')=='owner-main-model-root':return owner
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
    elif state['targetKind']=='owner-main-model-root':
        rig=eq.owner_rig(owner)
        root=eq.owner_main_model_root(rig) if rig is not None else None
        if root is None:raise ValueError('Generic owner-main-model-root target is unavailable')
        proof=dict(state,parentBoneRestMatrix=state['parentNodeRestMatrix'])
        eq.validate_wire(proof,scale)
        attachment.parent=root;attachment.parent_type='OBJECT';attachment.parent_bone=''
        attachment.matrix_parent_inverse=Matrix.Identity(4);attachment.matrix_basis=eq.mat(state['localMatrix'])
        context.view_layer.update()
        expected=root.matrix_world@eq.mat(state['localMatrix'])
        error=max(abs(a-b) for a,b in zip(eq.flatten(expected),eq.flatten(attachment.matrix_world)))
        if error>0.002:raise ValueError('Generic owner-main-model-root world attachment invariant failed')
        attachment['sora_attachment_node_id']=state.get('attachmentNodeId')
        attachment['sora_attachment_source_path']=state.get('attachmentSourcePath')
        attachment['sora_attachment_error']=error
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
    if assembly['compatibility']['status'] not in MOUNT_VERIFIED_STATUSES or assembly.get('scene') is None:
        raise ValueError(assembly['compatibility']['message'])
    if eq.CONTRACT not in owner or json.loads(owner[eq.CONTRACT])['characterId']!=assembly['compatibility']['characterId']:
        raise ValueError('Load native dedicated-equipment ownership for this character first')
    state=owner.get(eq.STATE,'idle')
    if not assembly['canBindStates'].get(state):raise ValueError('Selected weapon has unresolved native mounts in '+state)
    old=eq.owned_children(owner,'generic');prior=owner.get(CONTRACT)
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
        # Commit the new ownership before the old children are gone, so a failed commit can never leave the instance
        # without a usable generic weapon; the previous contract is restored if any later step raises.
        owner[CONTRACT]=json.dumps({**assembly,'scene':None,'weapon':weapon})
        # remove_scene deletes the old generic tree. If the active object is one of those objects Blender silently
        # clears the context object, and the equipment panel/operators would then resolve no owner instance; select the
        # owner rig before the deletion instead of only after it. No other object's selection is changed here.
        doomed={obj.as_pointer() for child in old for obj in child.all_objects}
        current=context.view_layer.objects.active
        if current is None or current.as_pointer() in doomed:
            rig.select_set(True);context.view_layer.objects.active=rig
        for child in old:remove_scene(context,child,force_cleanup=True)
    except BaseException:
        for child in reversed(created):remove_scene(context,child,force_cleanup=True)
        if owner.get(CONTRACT)!=prior:
            if prior is None:
                if CONTRACT in owner:del owner[CONTRACT]
            else:owner[CONTRACT]=prior
        raise


def adaptation_grade(confidence):
    return {'confirmed':'确定','inferred':'推断','unknown':'未知'}.get(confidence or '','未知')


def adaptation_icon(confidence):
    return {'confirmed':'CHECKMARK','inferred':'INFO','unknown':'QUESTION'}.get(confidence or '','QUESTION')


def adaptation_note(confidence,message=''):
    text=str(message or '').lower()
    if confidence=='confirmed':
        return '原生资源精确匹配'
    if confidence=='inferred':
        if 'ambiguous' in text:
            return '推断类型一致，但基础归属不明确；未验证实际挂载'
        return '名称与基础路径的推断类型一致；未验证实际挂载'
    if 'conflict' in text or 'disagree' in text:
        return '原生关联冲突，类型未知'
    if 'incomplete' in text or 'only the' in text or 'neither the' in text:
        return '缺少完整的原生关联，类型未知'
    return '缺少原生类型依据，类型未知'


def result_context(instance,database,game_root,query):
    return json.dumps({'instance':instance or '','database':database or '','gameRoot':game_root or '','query':query or ''},sort_keys=True,ensure_ascii=False)


def expected_context(settings,owner,rig,context):
    if owner is None or rig is None:
        return ''
    return result_context(str(owner.get('sora_instance') or ''),rig.get('sora_database') or '',
                          bpy.path.abspath(context.scene.sora.game_root),settings.query)


def selection_index(identities,desired):
    if not desired:
        return 0 if identities else -1
    for index,identity in enumerate(identities):
        if identity==desired:
            return index
    return -1


def selection_status(identities,desired,current_query,selected_query,page_total):
    index=selection_index(identities,desired)
    if index>=0:
        return index,''
    if not desired:
        return -1,''
    if current_query!=selected_query and page_total<=len(identities):
        return -1,'已选武器被当前筛选排除；请重新选择'
    return -1,'已选武器不在当前页（筛选结果共 {} 条）；可翻页找回或重新选择'.format(page_total)


def display_name(label,internal):
    label=str(label or internal or '');internal=str(internal or '')
    return label if not internal or label==internal else label+' / '+internal


def _wrap(text,width=58):
    import textwrap
    return textwrap.wrap(str(text or ''),width=width) or ['']


def current_owner(context):
    owner=eq.owner_collection(context)
    return owner,eq.owner_rig(owner) if owner else None


def clear_rows(settings,keep_selection=False):
    settings.selected=-1
    settings.unknown_selected=-1
    settings.rows.clear()
    settings.unknown_rows.clear()
    settings.offset=0
    settings.total=0
    settings.result_signature=''
    settings.summary=''
    settings.status=''
    if not keep_selection:
        settings.selected_identity=''
        settings.selected_label=''
        settings.selection_query=''


def query_changed(settings,context=None):
    clear_rows(settings,keep_selection=True)


def selection_changed(settings,context=None):
    if 0<=settings.selected<len(settings.rows):
        row=settings.rows[settings.selected]
        settings.selected_identity=row.identity
        settings.selected_label=row.label or row.internal_name
        settings.selection_query=settings.query


def stale_results(settings,owner,rig,context):
    if not (settings.result_signature or settings.rows or settings.unknown_rows):
        return False
    return settings.result_signature!=expected_context(settings,owner,rig,context)


class SORA_CompatibleWeapon(bpy.types.PropertyGroup):
    identity:StringProperty()
    label:StringProperty()
    internal_name:StringProperty()
    confidence:StringProperty()
    can_attempt:BoolProperty()
    reason:StringProperty()
    adaptation_note:StringProperty()
    adaptation_detail:StringProperty()


class SORA_UnknownWeapon(bpy.types.PropertyGroup):
    identity:StringProperty()
    internal_name:StringProperty()
    reason:StringProperty()
    note:StringProperty()


class SORA_UL_compatible_weapons(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index=0,flt_flag=0):
        confidence=getattr(item,'confidence','unknown')
        column=layout.column(align=True)
        column.label(text='{} · {}'.format(adaptation_grade(confidence),display_name(item.label or item.name,item.internal_name)),icon=adaptation_icon(confidence))
        if item.label!=item.internal_name:
            column.label(text=item.internal_name)
        if item.adaptation_note:
            column.label(text=item.adaptation_note)


class SORA_WeaponSettings(bpy.types.PropertyGroup):
    query:StringProperty(name='通用武器搜索',update=query_changed)
    rows:CollectionProperty(type=SORA_CompatibleWeapon)
    selected:IntProperty(default=-1,update=selection_changed)
    offset:IntProperty(default=0)
    total:IntProperty(default=0)
    unknown_rows:CollectionProperty(type=SORA_UnknownWeapon)
    unknown_selected:IntProperty(default=-1)
    owner:PointerProperty(type=bpy.types.Collection)
    owner_instance:StringProperty()
    selected_identity:StringProperty()
    selected_label:StringProperty()
    selection_query:StringProperty()
    result_signature:StringProperty()
    summary:StringProperty()
    status:StringProperty()
    details:BoolProperty(default=False)


class SORA_OT_weapon_search(TaskOperator,bpy.types.Operator):
    bl_idname='sora.compatible_weapons'
    bl_label='查找当前角色适配武器'
    direction:IntProperty(default=0)
    def execute(self,context):
        owner,rig=current_owner(context)
        if owner is None or rig is None:
            self.report({'ERROR'},'请先选择一个已导入角色');return {'CANCELLED'}
        settings=context.scene.sora_weapons
        query=settings.query
        instance=str(owner.get('sora_instance') or '')
        database=rig.get('sora_database') or ''
        game_root=bpy.path.abspath(context.scene.sora.game_root)
        base=settings.offset if settings.owner==owner and settings.owner_instance==instance else 0
        offset=max(0,base+self.direction*30) if self.direction else 0
        def complete(result):
            current,_=current_owner(context)
            if (current is None or current!=owner or str(current.get('sora_instance') or '')!=instance
                    or settings.query!=query):
                clear_rows(settings)
                settings.status='角色或筛选已切换；已忽略过期结果'
                context.scene.sora.status=settings.status
                return None
            settings.selected=-1
            settings.rows.clear()
            for source in result['rows']:
                row=settings.rows.add()
                adaptation=source['adaptation']
                row.identity=source['id'];row.label=source['label'];row.internal_name=source['internalName']
                row.confidence=adaptation['confidence']
                row.adaptation_note=adaptation_note(adaptation['confidence'],adaptation.get('message',''))
                row.adaptation_detail=adaptation.get('message','')
                row.can_attempt=source['capability'].get('canAttemptImport',source['capability']['canImport'])
                row.reason=source['capability']['reason']
                row.name=source['label']+' / '+source['internalName']
            settings.unknown_rows.clear()
            for source in result.get('unknown',[]):
                row=settings.unknown_rows.add()
                row.identity=source['id'];row.name=source['label']+' / '+source['internalName']
                row.internal_name=source['internalName'];row.reason=source['reason']
                row.note=adaptation_note('unknown',source['reason'])
            settings.owner=owner;settings.owner_instance=instance
            settings.offset=result['offset'];settings.total=result['total']
            settings.result_signature=result_context(instance,database,game_root,query)
            counts={}
            for row in settings.rows:counts[row.confidence]=counts.get(row.confidence,0)+1
            settings.summary='本页确定 {} · 推断 {}'.format(counts.get('confirmed',0),counts.get('inferred',0))
            index,warning=selection_status([row.identity for row in settings.rows],settings.selected_identity,query,settings.selection_query,result['total'])
            settings.selected=index
            if warning:
                settings.status=warning
            else:
                if index>=0:
                    settings.selected_identity=settings.rows[index].identity
                    settings.selected_label=settings.rows[index].label or settings.rows[index].internal_name
                settings.status='当前角色 {}：适配候选 {} 条（本页 {} 条）'.format(owner.name,result['total'],len(settings.rows))
            context.scene.sora.status=settings.status
        try:
            return tasks.start(self,context,'compatible-weapons',{'root':game_root,'path':database,'asset':rig['sora_asset'],'query':query,'offset':offset,'limit':30},complete)
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_weapon_equip(TaskOperator,bpy.types.Operator):
    bl_idname='sora.equip_weapon'
    bl_label='Equip selected generic weapon'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        owner,rig=current_owner(context);settings=context.scene.sora_weapons
        if owner is None or rig is None or settings.owner!=owner or settings.owner_instance!=str(owner.get('sora_instance') or ''):
            self.report({'ERROR'},'请先搜索并选择当前角色的适配武器');return {'CANCELLED'}
        if not 0<=settings.selected<len(settings.rows):
            self.report({'ERROR'},'请选择一条适配武器；已失效的选择需要重新搜索');return {'CANCELLED'}
        row=settings.rows[settings.selected]
        if row.identity!=settings.selected_identity:
            self.report({'ERROR'},'选择已失效，请重新选择');return {'CANCELLED'}
        expected=expected_context(settings,owner,rig,context)
        if settings.result_signature!=expected:
            self.report({'ERROR'},'搜索结果已过期，请重新搜索');return {'CANCELLED'}
        weapon=row.identity
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
    @classmethod
    def poll(cls,context):
        # Synchronous deletion of this owner generic tree must never overlap an asynchronous equip/replace task
        # that still owns those child collections; other object-mode entries gate on the same runner state.
        return context.mode=='OBJECT' and not tasks.busy()
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
            context.scene.sora.status=('Generic weapon unequipped; dedicated equipment and other instances are unchanged'
                if children else 'No generic weapon was equipped on this instance')
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


def draw(layout,context):
    settings=context.scene.sora_weapons
    owner,rig=current_owner(context)
    instance=str(owner.get('sora_instance') or '') if owner else ''
    stale=stale_results(settings,owner,rig,context)
    layout.label(text='通用武器（与专用装备分开）')
    if stale:
        layout.label(text='角色或实例已切换；请重新搜索当前角色的适配武器',icon='INFO')
    if settings.summary and not stale:
        layout.label(text=settings.summary)
    layout.prop(settings,'query')
    layout.operator('sora.compatible_weapons',icon='VIEWZOOM')
    if not stale:
        layout.template_list('SORA_UL_compatible_weapons','compatible_weapons',settings,'rows',settings,'selected',rows=4)
        row=layout.row(align=True)
        prev=row.row();prev.enabled=settings.offset>0;prev.operator('sora.compatible_weapons',text='',icon='TRIA_LEFT').direction=-1
        row.label(text=f'{settings.offset+1 if settings.total else 0}–{settings.offset+len(settings.rows)} / {settings.total}')
        following=row.row();following.enabled=settings.offset+len(settings.rows)<settings.total;following.operator('sora.compatible_weapons',text='',icon='TRIA_RIGHT').direction=1
        if settings.selected<0 and settings.selected_label:
            layout.label(text='已选未在当前页：'+settings.selected_label,icon='ERROR')
        if settings.status:
            for line in _wrap(settings.status):layout.label(text=line,icon='INFO')
        if 0<=settings.selected<len(settings.rows):
            row=settings.rows[settings.selected]
            layout.label(text='[{}] {}'.format(adaptation_grade(row.confidence),display_name(row.label,row.internal_name)),icon=adaptation_icon(row.confidence))
            for line in _wrap(row.adaptation_note):layout.label(text=line)
            layout.prop(settings,'details',text='技术详情',icon='DISCLOSURE_TRI_DOWN' if settings.details else 'DISCLOSURE_TRI_RIGHT')
            if settings.details:
                layout.label(text='稳定资源ID：'+row.identity)
                for line in _wrap(row.adaptation_detail):layout.label(text=line)
                for line in _wrap(row.reason):layout.label(text=line)
        if settings.unknown_rows:
            layout.label(text='未知类型 {} 条（不可用于装备）'.format(len(settings.unknown_rows)),icon='QUESTION')
            layout.template_list('UI_UL_list','unknown_weapons',settings,'unknown_rows',settings,'unknown_selected',rows=2)
            if 0<=settings.unknown_selected<len(settings.unknown_rows):
                unknown=settings.unknown_rows[settings.unknown_selected]
                for line in _wrap(unknown.note):layout.label(text=line)
                if settings.details:
                    for line in _wrap(unknown.reason):layout.label(text=line)
    layout.operator('sora.equip_weapon');layout.operator('sora.unequip_weapon')

CLASSES=(SORA_CompatibleWeapon,SORA_UnknownWeapon,SORA_UL_compatible_weapons,SORA_WeaponSettings,SORA_OT_weapon_search,SORA_OT_weapon_equip,SORA_OT_weapon_remove)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Scene.sora_weapons=PointerProperty(type=SORA_WeaponSettings)
def unregister():
    del bpy.types.Scene.sora_weapons
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
