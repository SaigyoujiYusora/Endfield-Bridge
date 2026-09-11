"""Manual native dedicated-equipment clips; owner-bound, no controller execution."""
import json
import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty
from . import equipment as eq, tasks
from .tasks import TaskOperator
from . import equipment_animation_contract as contract


def invalidate_clips(settings,context=None):
    settings.clips.clear()
    settings.selected_clip=-1
    settings.result_signature=''


def source_changed(settings,context=None):
    invalidate_clips(settings)
    settings.controllers.clear()
    if 0<=settings.selected_source<len(settings.sources):
        for value in json.loads(settings.sources[settings.selected_source].controllers_json):
            row=settings.controllers.add()
            row.animator_id=value['animatorId'];row.controller_id=value['controllerId']
            row.name='Controller '+value['controllerId'].rsplit(':',1)[-1]
    settings.selected_controller=0 if len(settings.controllers)==1 else -1


class SORA_EquipmentAnimationSource(bpy.types.PropertyGroup):
    slot_id:StringProperty()
    resource_id:StringProperty()
    resource_path:StringProperty()
    controllers_json:StringProperty(default='[]')


class SORA_EquipmentAnimationController(bpy.types.PropertyGroup):
    animator_id:StringProperty()
    controller_id:StringProperty()


class SORA_EquipmentAnimationClip(bpy.types.PropertyGroup):
    metadata_json:StringProperty()


class SORA_EquipmentAnimationSettings(bpy.types.PropertyGroup):
    owner:PointerProperty(type=bpy.types.Collection)
    sources:CollectionProperty(type=SORA_EquipmentAnimationSource)
    selected_source:IntProperty(default=-1,update=source_changed)
    controllers:CollectionProperty(type=SORA_EquipmentAnimationController)
    selected_controller:IntProperty(default=-1,update=invalidate_clips)
    clips:CollectionProperty(type=SORA_EquipmentAnimationClip)
    selected_clip:IntProperty(default=-1)
    supported:BoolProperty(default=False)
    checked_executable:StringProperty()
    checked_binary_signature:StringProperty()
    result_signature:StringProperty()
    status:StringProperty(default='选择专用装备与控制器，再读取片段')


def executable(context):
    return bpy.path.abspath(context.preferences.addons[__package__].preferences.executable)


def require_backend(context):
    settings=context.scene.sora_equipment_animation
    path=executable(context)
    current=json.dumps(contract.backend_fingerprint(path),sort_keys=True)
    if not settings.supported or settings.checked_executable!=path or settings.checked_binary_signature!=current:
        raise ValueError('Core executable or dependencies changed; check equipment animation support again')
    return current


def timeline_state(context):
    scene=context.scene
    return (scene.render.fps,scene.render.fps_base,scene.frame_start,scene.frame_end,scene.frame_current,scene.frame_subframe)


def selection(context):
    settings=context.scene.sora_equipment_animation
    owner=eq.owner_collection(context)
    if owner is None or settings.owner!=owner or eq.CONTRACT not in owner:
        raise ValueError('请为当前角色刷新专用装备列表')
    if not 0<=settings.selected_source<len(settings.sources) or not 0<=settings.selected_controller<len(settings.controllers):
        raise ValueError('请选择专用装备和原生控制器')
    source=settings.sources[settings.selected_source]
    controller=settings.controllers[settings.selected_controller]
    assembly=json.loads(owner[eq.CONTRACT])
    fresh=next((row for row in contract.sources(assembly) if row['slotId']==source.slot_id),None)
    pair={'animatorId':controller.animator_id,'controllerId':controller.controller_id}
    if fresh is None or fresh['resourceId']!=source.resource_id or fresh['resourcePath']!=source.resource_path or pair not in fresh['controllers']:
        raise ValueError('专用装备来源已改变，请刷新列表')
    children=[child for child in eq.owned_children(owner,'dedicated') if child.get('sora_equipment_slot')==source.slot_id
              and child.get('sora_equipment_resource')==source.resource_id and child.get('sora_equipment_path')==source.resource_path]
    if len(children)!=1:raise ValueError('当前角色的专用装备槽未唯一导入')
    child=children[0]
    rigs=[obj for obj in child.objects if obj.type=='ARMATURE' and not obj.get('sora_display_source')]
    if len(rigs)!=1:raise ValueError('该专用装备没有唯一可动画原生骨架')
    rig=rigs[0]
    if rig.pose is None or rig.get('sora_instance')!=child.get('sora_instance') or rig.get('sora_owner_collection')!=owner or rig.get('sora_equipment_resource')!=source.resource_id:
        raise ValueError('专用装备骨架归属不一致')
    owner_rig=eq.owner_rig(owner)
    if owner_rig is None:raise ValueError('当前角色的原生骨架不存在')
    root=bpy.path.abspath(context.scene.sora.game_root)
    if not context.scene.sora.game_root.strip():raise ValueError('请先配置匹配的游戏目录')
    expected={'ownerAssetId':owner_rig['sora_asset'],'characterId':assembly['characterId'],
              'declarationId':assembly['declaration']['sourceId'],'slotId':source.slot_id,'resourceId':source.resource_id,
              'resourcePath':source.resource_path,**pair}
    resource=next(r for r in assembly['resources'] if r['resourceId']==source.resource_id)
    selected_controller=next(c for c in resource['controllers'] if all(c.get(k)==v for k,v in pair.items()))
    initial=resource.get('defaultPose') or {}
    initial_path=initial.get('animatorSourcePath') if all(initial.get(k)==v for k,v in pair.items()) else None
    expected.update(controllerClips=selected_controller.get('clips',[]),
                    nodeSourcePaths=sorted({obj.get('sora_source_path') for obj in child.objects
                                            if obj.get('sora_node_id') and obj.get('sora_source_path')}),
                    animatorSourcePath=initial_path)
    parameters={'root':root,'path':owner_rig['sora_database'],'asset':owner_rig['sora_asset'],'resource':source.resource_path,
                'equipment':{key:expected[key] for key in contract.SELECTOR_KEYS}}
    signature=json.dumps({'parameters':parameters,'owner':owner.as_pointer(),'child':child.as_pointer(),
                          'rig':rig.as_pointer(),'data':rig.data.as_pointer(),'executable':executable(context),
                          'binary':settings.checked_binary_signature,'sourceAuthority':expected},sort_keys=True)
    return owner,child,rig,expected,parameters,signature


def paused(context,rig):
    if context.screen and context.screen.is_animation_playing:raise ValueError('请暂停播放后加载专用装备片段')
    if context.mode!='OBJECT':raise ValueError('请在物体模式加载专用装备片段')
    if rig.get('sora_pose_resume'):raise ValueError('请先恢复该装备的挂起姿势')
    if rig.animation_data and (rig.animation_data.nla_tracks or rig.animation_data.drivers):
        raise ValueError('该装备有 NLA 或驱动器；当前手动片段加载不支持叠加，现有状态保留')


def clone(value):
    if isinstance(value,bpy.types.ID):return value
    if isinstance(value,(str,bool,int,float)) or value is None:return value
    if hasattr(value,'items'):return {key:clone(item) for key,item in value.items()}
    return [clone(item) for item in value]


def editable_rna(value):
    """Capture authored constraint settings, excluding evaluated readonly errors."""
    result={'__rnaType':value.bl_rna.identifier}
    for prop in value.bl_rna.properties:
        name=prop.identifier
        if name=='rna_type' or name.startswith('bl_') or prop.is_readonly and prop.type not in {'COLLECTION','POINTER'}:continue
        item=getattr(value,name)
        if prop.type=='COLLECTION':result[name]=[editable_rna(row) for row in item]
        elif prop.type=='POINTER':
            result[name]=clone(item) if item is None or isinstance(item,bpy.types.ID) else editable_rna(item)
        else:result[name]=clone(item)
    return result


def constraint_state(rig):
    return {'object':[editable_rna(c) for c in rig.constraints],
            'bones':{bone.name:[editable_rna(c) for c in bone.constraints] for bone in rig.pose.bones}}


def capture(rig):
    from .action_binding import slot_identity
    from .pose_controls import root_channels
    animation=rig.animation_data
    return {'root':root_channels(rig),'properties':clone(dict(rig.items())),'constraints':constraint_state(rig),
        'bones':{bone.name:{'mode':bone.rotation_mode,**{key:list(getattr(bone,key)) for key in
                    ('location','scale','rotation_euler','rotation_quaternion','rotation_axis_angle')}} for bone in rig.pose.bones},
        'hadAnimation':animation is not None,'action':animation.action if animation else None,
        'slot':slot_identity(animation.action_slot) if animation else None,
        'lastSlot':animation.last_slot_identifier if animation else ''}


def restore(rig,saved):
    # Constraints are only observed; do not overwrite intervening user edits.
    from .action_binding import bind_action
    from .pose_controls import restore_root
    if saved['hadAnimation']:
        rig.animation_data_create()
        bind_action(rig,saved['action'],saved['slot'],select_slot=True)
        rig.animation_data.last_slot_identifier=saved['lastSlot']
    elif rig.animation_data:
        # The preflight rejected drivers/NLA; do not remove new user content.
        if rig.animation_data.drivers or rig.animation_data.nla_tracks:
            raise ValueError('装备动画状态被编辑，未清除新的驱动器或 NLA')
        rig.animation_data_clear()
    for key in list(rig.keys()):
        if key not in saved['properties']:del rig[key]
    for key,value in saved['properties'].items():
        if key not in rig or clone(rig[key])!=value:rig[key]=value
    for bone in rig.pose.bones:
        values=saved['bones'][bone.name]
        bone.rotation_mode=values['mode']
        for key,value in values.items():
            if key!='mode':setattr(bone,key,value)
    restore_root(rig,{'rootChannels':saved['root']})
    rig.update_tag()


def live_bones(rig):
    return [{'index':bone['sora_source_index'],'sourcePath':bone.get('sora_source_path'),
             'sourceHash':bone.get('sora_source_hash'),'parent':bone.parent.get('sora_source_index') if bone.parent else -1,
             'restMatrix':[value for row in bone.matrix_local for value in row]}
            for bone in rig.data.bones if 'sora_source_index' in bone]


class SORA_OT_equipment_animation_sources(bpy.types.Operator):
    bl_idname='sora.equipment_animation_sources'
    bl_label='刷新专用装备'
    def execute(self,context):
        try:
            owner=eq.owner_collection(context)
            if owner is None or eq.CONTRACT not in owner:raise ValueError('请选择已关联专用装备的角色')
            rows=contract.sources(json.loads(owner[eq.CONTRACT]))
            settings=context.scene.sora_equipment_animation
            settings.sources.clear();settings.controllers.clear();invalidate_clips(settings)
            settings.owner=owner;settings.selected_source=-1
            for value in rows:
                row=settings.sources.add();row.name=value['resourcePath'].rsplit('/',1)[-1]+' / '+value['slotId'].rsplit(':',1)[-1]
                row.slot_id=value['slotId'];row.resource_id=value['resourceId'];row.resource_path=value['resourcePath']
                row.controllers_json=json.dumps(value['controllers'])
            settings.selected_source=0 if rows else -1
            settings.status='选择专用装备和控制器；片段按需验证' if rows else '当前角色没有专用装备槽'
            context.scene.sora.status=settings.status
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_equipment_animation_capabilities(TaskOperator,bpy.types.Operator):
    bl_idname='sora.equipment_animation_capabilities'
    bl_label='检查装备动画支持'
    def execute(self,context):
        settings=context.scene.sora_equipment_animation;path=executable(context)
        try:binary=json.dumps(contract.backend_fingerprint(path),sort_keys=True)
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}
        settings.supported=False;settings.checked_binary_signature='';invalidate_clips(settings)
        def complete(result):
            if executable(context)!=path:raise ValueError('后端已改变，请重新检查')
            if json.dumps(contract.backend_fingerprint(path),sort_keys=True)!=binary:raise ValueError('Core binary changed during capability check')
            settings.supported=contract.supported(result);settings.checked_executable=path;settings.checked_binary_signature=binary
            settings.status='后端支持专用装备原生片段' if settings.supported else '当前后端不支持专用装备片段；请使用已审核的支持版本'
            context.scene.sora.status=settings.status
        try:return tasks.start(self,context,'capabilities',{},complete)
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_equipment_animation_clips(TaskOperator,bpy.types.Operator):
    bl_idname='sora.equipment_animation_clips'
    bl_label='读取控制器片段'
    def execute(self,context):
        try:
            settings=context.scene.sora_equipment_animation
            require_backend(context)
            _,_,rig,expected,parameters,signature=selection(context);paused(context,rig)
            def complete(result):
                if selection(context)[-1]!=signature:raise ValueError('装备选择或来源已改变，请重新读取片段')
                require_backend(context)
                current_expected=dict(expected,manifestHash=contract.catalog_manifest(result['catalog']))
                rows=contract.validate_discovery(result['clips'],current_expected)
                settings.clips.clear()
                for value in rows:
                    row=settings.clips.add();row.name=value['name'];row.metadata_json=json.dumps(value)
                settings.selected_clip=0 if len(rows)==1 else -1
                settings.result_signature=signature
                settings.status=str(len(rows))+' 个引用片段；加载时验证采样和绑定'
                context.scene.sora.status=settings.status
            return tasks.start_batch(self,context,[{'key':'catalog','method':'inspect','params':{'path':parameters['path']}},
                {'key':'clips','method':'animation-clips','params':parameters}],complete)
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class SORA_OT_equipment_animation_import(TaskOperator,bpy.types.Operator):
    bl_idname='sora.equipment_animation_import'
    bl_label='加载专用装备片段'
    bl_options={'REGISTER','UNDO'}
    align_body_event:BoolProperty(default=False)
    def execute(self,context):
        try:
            settings=context.scene.sora_equipment_animation
            require_backend(context)
            owner,child,rig,expected,parameters,signature=selection(context);paused(context,rig)
            if settings.result_signature!=signature or not 0<=settings.selected_clip<len(settings.clips):raise ValueError('请读取并选择当前控制器的片段')
            selected=json.loads(settings.clips[settings.selected_clip].metadata_json)
            contract.validate_discovery([selected],dict(expected,manifestHash=selected['equipment'].get('manifestHash')))
            parameters=dict(parameters,selection={'cab':selected['cab'],'pathId':selected['pathId']})
            initial=capture(rig);timing=timeline_state(context)
            from .equipment_event_alignment import mapping as event_mapping
            aligned=event_mapping(owner,eq.owner_rig(owner),selected,timing[0]/timing[1]) if self.align_body_event else None
            def check():
                require_backend(context)
                paused(context,rig)
                if selection(context)[-1]!=signature or timeline_state(context)!=timing:raise ValueError('装备目标或时间轴已改变；未覆盖新的用户状态')
                if not 0<=settings.selected_clip<len(settings.clips) or json.loads(settings.clips[settings.selected_clip].metadata_json)!=selected:raise ValueError('片段选择已改变')
                if capture(rig)!=initial:raise ValueError('装备姿势或 Action 已编辑；保留新状态')
                if aligned is not None and event_mapping(owner,eq.owner_rig(owner),selected,timing[0]/timing[1])!=aligned:
                    raise ValueError('身体动作或原生事件已改变；未应用装备片段')
            def complete(result):
                check()
                current_expected=dict(expected,manifestHash=contract.catalog_manifest(result['catalog']))
                clip,bones,proof=contract.validate_import(result['clip'],current_expected,selected,live_bones(rig),bool(child.get('sora_render_canonical')))
                from .animation_actions import apply_clip_steps
                mapping=aligned or {'fps':timing[0]/timing[1],'origin':timing[4]+timing[5]}
                frames=contract.frame_grid(proof['times'],mapping['fps'],mapping['origin'])
                factory=lambda:apply_clip_steps(context,rig,clip,[bone['name'] for bone in bones],bone_sources=bones,
                                                keep_face_controls=True,timeline=mapping)
                action=yield from contract.guarded_steps(factory,check,lambda:capture(rig),lambda state:restore(rig,state))
                action['sora_equipment_clip_proof']=json.dumps(proof,separators=(',',':'))
                action['sora_equipment_frame_grid']=json.dumps(frames,separators=(',',':'))
                action['sora_equipment_slot']=expected['slotId']
                if aligned is not None:action['sora_body_event_alignment']=json.dumps(aligned['proof'],separators=(',',':'))
                end=mapping['origin']+clip['duration']*mapping['fps']
                settings.status=f"已加载 {clip['name']}：帧 {mapping['origin']:g}–{end:g}；源 {clip['fps']:g} Hz，时间轴 {mapping['fps']:g} fps"
                if end>context.scene.frame_end:settings.status+='；片段超出场景结束帧，可按需调整'
                context.scene.sora.status=settings.status
            return tasks.start_batch(self,context,[{'key':'catalog','method':'inspect','params':{'path':parameters['path']}},
                {'key':'clip','method':'animation-import','params':parameters}],complete)
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


def draw(layout,context):
    from . import wrapped_label
    settings=context.scene.sora_equipment_animation
    box=layout.box();box.label(text='专用装备 · 原生片段')
    wrapped_label(box,settings.status,context)
    if context.scene.sora.task_error:
        wrapped_label(box,'最近任务错误：'+context.scene.sora.task_error,context)
    row=box.row();row.enabled=not tasks.busy()
    row.operator('sora.equipment_animation_sources');row.operator('sora.equipment_animation_capabilities')
    if settings.owner!=eq.owner_collection(context):box.label(text='刷新当前角色的专用装备列表');return
    if settings.sources and not settings.controllers:wrapped_label(box,'该专用装备没有已记录的原生控制器。',context)
    body=box.column();body.enabled=not tasks.busy()
    body.template_list('UI_UL_list','equipment_animation_sources',settings,'sources',settings,'selected_source',rows=2)
    body.label(text='原生控制器 / Animator')
    body.template_list('UI_UL_list','equipment_animation_controllers',settings,'controllers',settings,'selected_controller',rows=2)
    if 0<=settings.selected_controller<len(settings.controllers):
        body.label(text='Animator '+settings.controllers[settings.selected_controller].animator_id.rsplit(':',1)[-1])
    tools=body.row();tools.enabled=settings.supported and settings.checked_executable==executable(context)
    tools.operator('sora.equipment_animation_clips')
    body.template_list('UI_UL_list','equipment_animation_clips',settings,'clips',settings,'selected_clip',rows=4)
    load=body.row();load.enabled=bool(settings.result_signature) and 0<=settings.selected_clip<len(settings.clips)
    load.operator('sora.equipment_animation_import')
    load.operator('sora.equipment_animation_import',text='按身体源事件时间加载').align_body_event=True
    current_rig=None
    if 0<=settings.selected_source<len(settings.sources):
        slot_id=settings.sources[settings.selected_source].slot_id
        children=[child for child in eq.owned_children(settings.owner,'dedicated') if child.get('sora_equipment_slot')==slot_id]
        if len(children)==1:current_rig=eq.owner_rig(children[0])
    playback=body.row()
    playback.enabled=bool(current_rig and current_rig.animation_data and current_rig.animation_data.action)
    playback.operator('screen.animation_play',text='播放 / 暂停',icon='PLAY')
    if current_rig and any(child.hide_viewport for child in eq.owned_children(settings.owner,'dedicated') if current_rig.name in child.objects):
        wrapped_label(box,'当前专用装备隐藏；可用上方静态状态按钮查看，不会自动改变显隐。',context)
    wrapped_label(box,'此处加载单个片段；身体事件跟随和模型显隐由上方独立控制。',context)


CLASSES=(SORA_EquipmentAnimationSource,SORA_EquipmentAnimationController,SORA_EquipmentAnimationClip,
         SORA_EquipmentAnimationSettings,SORA_OT_equipment_animation_sources,SORA_OT_equipment_animation_capabilities,
         SORA_OT_equipment_animation_clips,SORA_OT_equipment_animation_import)


def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Scene.sora_equipment_animation=PointerProperty(type=SORA_EquipmentAnimationSettings)


def unregister():
    if hasattr(bpy.types.Scene,'sora_equipment_animation'):del bpy.types.Scene.sora_equipment_animation
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
