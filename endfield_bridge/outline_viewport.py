"""GPU inverted-hull viewport pass; the original GN branch remains for renders.

Camera changes only update shader uniforms at draw time. Mesh buffers are
invalidated by geometry updates, not by viewport movement. All RNA topology
changes happen in the existing main-thread view timer, never in draw callbacks.
"""
import json
import math
import time
import bpy
import gpu
import numpy as np
from mathutils import Matrix, Vector
from gpu_extras.batch import batch_for_shader
from . import outline_gpu_shader

MARK='endf_gpu_outline_v1'
_draw_handle=None
_entries={}
_dirty=set()
_busy=False
_warned=set()
_versions={}
_occluders={}
_target=None
_depth_shader=None
_alpha_depth_shader=None
_blit_shader=None
_blit_batch=None
_scene_frames={}
_program_dirty=set()
_failures={}
_stats={'draws':0,'uploads':0,'last_draw_ms':0.0}


def _switch(tree):
    return next((n for n in tree.nodes if n.get(MARK)=='switch'),None)


def _base_link(tree):
    switch=_switch(tree)
    if switch is not None:
        return switch.inputs['True'].links[0].from_socket,switch.inputs['False'].links[0].from_socket,None
    output=next((n for n in tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output),None)
    if output is None or not output.inputs[0].is_linked:raise ValueError('Outline output is not connected')
    joined=output.inputs[0].links[0].from_node
    if joined.bl_idname!='GeometryNodeJoinGeometry':raise ValueError('Unsupported custom outline output')
    links=list(joined.inputs['Geometry'].links)
    base=[l for l in links if l.from_node.label!='RuriOutlineViewGate']
    gates=[l for l in links if l.from_node.label=='RuriOutlineViewGate']
    if len(base)!=1 or len(gates)!=1 or base[0].from_node.type!='GROUP_INPUT':
        raise ValueError('Viewport GPU outline requires the native outline-only branch')
    return base[0].from_socket,joined.outputs['Geometry'],output.inputs[0]


def _install_switch(tree):
    base,full,target=_base_link(tree)
    if target is None:return
    viewport=tree.nodes.new('GeometryNodeIsViewport');viewport[MARK]='viewport'
    viewport.label='Endfield GPU viewport outline'
    switch=tree.nodes.new('GeometryNodeSwitch');switch.input_type='GEOMETRY';switch[MARK]='switch'
    switch.label='GPU viewport / original render outline'
    tree.links.new(viewport.outputs['Is Viewport'],switch.inputs['Switch'])
    tree.links.new(base,switch.inputs['True']);tree.links.new(full,switch.inputs['False'])
    tree.links.new(switch.outputs[0],target)
    tree.update_tag()


def _remove_switch(tree):
    switch=_switch(tree)
    if switch is not None and switch.inputs['False'].is_linked:
        original=switch.inputs['False'].links[0].from_socket
        for link in list(switch.outputs[0].links):tree.links.new(original,link.to_socket)
    for node in list(tree.nodes):
        if node.get(MARK) in {'switch','viewport'}:tree.nodes.remove(node)
    tree.update_tag()


def handles(obj):
    entry=_entries.get(obj.as_pointer())
    try:return entry is not None and entry['object']==obj and _switch(entry['tree']) is not None
    except ReferenceError:return False


def _material_slot(node):
    for link in node.outputs['offset'].links:
        target=link.to_node
        if target.bl_idname!='GeometryNodeSetPosition':continue
        if target.inputs['Selection'].is_linked:
            compare=target.inputs['Selection'].links[0].from_node
            if compare.bl_idname=='ShaderNodeMath' and compare.operation=='COMPARE':
                return int(compare.inputs[1].default_value)
    raise ValueError('Cannot resolve outline material slot')


def _warn(obj,error):
    try:name=obj.name
    except ReferenceError:name='<removed>'
    key=(name,str(error))
    if key not in _warned:
        print('[Endfield GPU outline]',name,str(error),flush=True);_warned.add(key)


def sync(stacks,scene):
    global _busy
    if bpy.app.background or bpy.app.is_job_running('RENDER') or _busy:return
    ensure_registered()
    _busy=True
    try:
        alive=set()
        for obj in scene.objects:
            if obj.type!='MESH' or obj.data is None:continue
            if obj.get('sora_render_mode')=='BASIC':
                previous=_entries.pop(obj.as_pointer(),None)
                if previous is not None:_remove_switch(previous['tree'])
                continue
            mod=next((m for m in obj.modifiers if m.type=='NODES' and m.node_group and
                      m.node_group.get('ruri_outline_runtime_revision')),None)
            if mod is None:continue
            tree=mod.node_group;key=obj.as_pointer();alive.add(key)
            nodes=[]
            for node in tree.nodes:
                if node.type!='GROUP' or node.node_tree is None:continue
                stack=next((s for s in stacks if s.post is None and node.node_tree.name.startswith(s.CLONE_O_PREFIX)),None)
                if stack is not None:nodes.append((stack,node))
            if not nodes:continue
            signature=None
            try:
                _base_link(tree)
                parts=[];signature=[]
                for stack,node in nodes:
                    slot=_material_slot(node)
                    material=obj.data.materials[slot]
                    floats,st,colors=stack._mat_meta(material)
                    base=stack._material_images(material).get('_BaseMap')
                    values=[(s.name,list(s.default_value) if s.type=='VECTOR' else s.default_value)
                            for s in node.inputs if s.name not in outline_gpu_shader.VIEW_INPUTS]
                    images=[n.inputs['Image'].default_value for n in node.node_tree.nodes if n.bl_idname=='GeometryNodeImageTexture']
                    signature.append((slot,node.node_tree.as_pointer(),values,floats,colors,st,
                                      base.as_pointer() if base else 0,[i.as_pointer() if i else 0 for i in images]))
                    parts.append((slot,node,base,floats,colors,list(st.get('_BaseMap',[1,1,0,0]))))
                signature=json.dumps(signature,sort_keys=True,default=list)
                entry=_entries.get(key)
                source_dirty=tree.as_pointer() in _program_dirty or any(node.node_tree.as_pointer() in _program_dirty for _,node in nodes)
                if _failures.get(key,(None,))[0]==signature and not source_dirty:continue
                if entry is not None and entry.get('failed'):raise ValueError(entry['failed'])
                if entry is None or entry['tree']!=tree or entry['signature']!=signature or source_dirty:
                    programs=[]
                    for slot,node,base,floats,colors,st in parts:
                        shader,textures,source=outline_gpu_shader.build(node,base,floats,colors,st,obj.data.attributes.keys())
                        transparent=floats.get('_OutlineTransparent',0.0)>0.5 or floats.get('_SurfaceType',0.0)>0.5
                        clip=(base,st,float(colors.get('_BaseColor',[1,1,1,1])[3])) if transparent and base is not None else None
                        programs.append({'slot':slot,'shader':shader,'textures':textures,'source':source,'depth_clip':clip})
                    _install_switch(tree)
                    _entries[key]={'object':obj,'modifier':mod,'tree':tree,'signature':signature,'programs':programs,'batches':None}
                    _dirty.add(key)
                    _failures.pop(key,None)
                elif _switch(tree) is None:_install_switch(tree);_dirty.add(key)
            except (ValueError,RuntimeError,KeyError,TypeError,IndexError) as error:
                if _switch(tree) is not None:_remove_switch(tree)
                _entries.pop(key,None);_warn(obj,error)
                if isinstance(signature,str):_failures[key]=(signature,str(error))
        # Entries belonging to other scenes remain available for their viewports.
        for key,entry in list(_entries.items()):
            try:valid=bpy.data.objects.get(entry['object'].name)==entry['object']
            except ReferenceError:valid=False
            if not valid:_entries.pop(key,None);_dirty.discard(key)
        alive_objects={o.as_pointer() for o in bpy.data.objects}
        for key in set(_occluders)-alive_objects:_occluders.pop(key,None)
        _program_dirty.clear()
    finally:_busy=False


def _point_attribute(mesh,name,loop_vertices,width):
    attr=mesh.attributes.get(name);count=len(mesh.vertices)
    if attr is None:return np.zeros((count,width),dtype=np.float32)
    components={'FLOAT_VECTOR':3,'FLOAT2':2,'FLOAT':1,'INT':1}.get(attr.data_type)
    if components is None:raise ValueError('Unsupported outline attribute data: '+name)
    data=np.empty((len(attr.data),components),dtype=np.float32)
    attr.data.foreach_get('value' if components==1 else 'vector',data.ravel())
    if attr.domain=='CORNER':
        accumulated=np.zeros((count,components),dtype=np.float32)
        np.add.at(accumulated,loop_vertices,data)
        divisor=np.bincount(loop_vertices,minlength=count).astype(np.float32)
        data=accumulated/np.maximum(divisor[:,None],1.0)
    elif attr.domain!='POINT':raise ValueError('Unsupported outline attribute domain: '+name)
    result=np.zeros((count,width),dtype=np.float32);result[:,:min(width,components)]=data[:,:width]
    return result


def _upload(entry,depsgraph):
    obj=entry['object'];mesh=obj.evaluated_get(depsgraph).data
    entry['eval_scope']=(depsgraph.scene.original.as_pointer(),depsgraph.view_layer.name)
    count=len(mesh.vertices);loops=len(mesh.loops)
    positions=np.empty((count,3),dtype=np.float32);mesh.vertices.foreach_get('co',positions.ravel())
    normals=np.empty((count,3),dtype=np.float32);mesh.vertex_normals.foreach_get('vector',normals.ravel())
    signature=(count,loops,len(mesh.polygons))
    streamable=all(m==entry['modifier'] or m.type=='ARMATURE' for m in obj.modifiers)
    if (streamable and entry.get('topology')==signature and entry.get('buffers')
            and not entry.pop('full_dirty',False)):
        loop_vertices=entry['loop_vertices']
        pose={'position':positions[loop_vertices],'normal':normals[loop_vertices]}
        entry['batches']=[_pose_batch(record,pose,loops) if record else None for record in entry['buffers']]
        _stats['uploads']+=1
        return
    mesh.calc_loop_triangles()
    loop_vertices=np.empty(loops,dtype=np.int32);mesh.loops.foreach_get('vertex_index',loop_vertices)
    triangles=np.empty((len(mesh.loop_triangles),3),dtype=np.int32);mesh.loop_triangles.foreach_get('loops',triangles.ravel())
    poly=np.empty(len(mesh.loop_triangles),dtype=np.int32);mesh.loop_triangles.foreach_get('polygon_index',poly)
    materials=np.empty(len(mesh.polygons),dtype=np.int32);mesh.polygons.foreach_get('material_index',materials)
    uv=np.zeros((loops,2),dtype=np.float32)
    if mesh.uv_layers.get('UVMap') is not None:mesh.uv_layers['UVMap'].data.foreach_get('uv',uv.ravel())
    attrs={'position':positions[loop_vertices],'normal':normals[loop_vertices],'fragmentUV':uv}
    for name,(shader_name,typ) in outline_gpu_shader.ATTRIBUTES.items():
        data=_point_attribute(mesh,name,loop_vertices,1 if typ=='float' else 3)[loop_vertices]
        attrs[shader_name]=data.ravel() if typ=='float' else data
    batches=[];buffers=[]
    for program in entry['programs']:
        indices=triangles[materials[poly]==program['slot']]
        active={name:attrs[name] for name,_ in program['shader'].attrs_info_get()}
        if len(indices):
            fmt=gpu.types.GPUVertFormat();static_fmt=gpu.types.GPUVertFormat()
            moving={name for name in active if name in {'position','normal'}}
            fixed={name for name in active if name not in moving}
            for name,data in active.items():
                (fmt if name in moving else static_fmt).attr_add(id=name,comp_type='F32',len=1 if data.ndim==1 else data.shape[1],fetch_mode='FLOAT')
            static=gpu.types.GPUVertBuf(static_fmt,len=loops) if fixed else None
            for name in fixed:static.attr_fill(name,active[name])
            record={'format':fmt,'moving':moving,'static':static,'index':gpu.types.GPUIndexBuf(type='TRIS',seq=indices)}
            batches.append(_pose_batch(record,active,loops));buffers.append(record)
        else:batches.append(None);buffers.append(None)
    entry.update(batches=batches,buffers=buffers,topology=signature,loop_vertices=loop_vertices)
    _stats['uploads']+=1


def _pose_batch(record,attributes,count):
    # Python exposes static VBOs only: keep immutable UV/tangent/index buffers,
    # and replace just the small position/normal buffer when a pose changes.
    pose=gpu.types.GPUVertBuf(record['format'],len=count)
    for name in record['moving']:pose.attr_fill(name,attributes[name])
    batch=gpu.types.GPUBatch(type='TRIS',buf=pose,elem=record['index'])
    if record['static'] is not None:batch.vertbuf_add(record['static'])
    return batch


def view_data(obj,tree,view,projection,width,height,near,far):
    world=view.inverted();basis=world.to_3x3();look_world=basis@Vector((0,0,-1))
    inverse=obj.matrix_world.inverted();inv3=inverse.to_3x3()
    perspective=abs(projection[3][3])<0.5
    scale=abs(projection[1][1]);half_fov=math.atan(1/max(scale,1e-8))
    position=world.translation
    if not perspective:
        center=obj.matrix_world@Vector(tuple(tree.get('ruri_outline_base_center') or (0,0,0)))
        distance=max(float(tree.get('ruri_outline_base_extent',0.01))*1000.0,10.0)
        position=center-look_world.normalized()*distance
        half_fov=math.atan((2/max(scale,1e-8))/(2*distance))
    near=max(near,1e-5);far=max(far,near+1e-4)
    coefficient=1e-4*(far-near)/(far*near) if perspective else 1e-4*(far-near)*0.5
    return Matrix([tuple(inv3@basis.col[0])+(half_fov,),tuple(inv3@basis.col[1])+(max(width,2)*(1 if perspective else -1),),
                   tuple(inv3@look_world)+(max(height,2),),tuple(inverse@position)+(coefficient,)]).transposed()


def _render_target(width,height):
    global _target,_depth_shader,_alpha_depth_shader,_blit_shader,_blit_batch
    if _target is None or _target[:2]!=(width,height):
        color=gpu.types.GPUTexture((width,height),format='RGBA8')
        depth=gpu.types.GPUTexture((width,height),format='DEPTH_COMPONENT32F')
        _target=(width,height,color,depth,gpu.types.GPUFrameBuffer(color_slots=color,depth_slot=depth))
    if _depth_shader is None:
        info=gpu.types.GPUShaderCreateInfo();info.vertex_in(0,'VEC3','position');info.push_constant('MAT4','mvp');info.fragment_out(0,'VEC4','color')
        info.vertex_source('void main(){gl_Position=mvp*vec4(position,1.0);}')
        info.fragment_source('void main(){color=vec4(0.0);}')
        _depth_shader=gpu.shader.create_from_info(info)
        uv=gpu.types.GPUStageInterfaceInfo('endf_depth_alpha');uv.smooth('VEC2','uv')
        info=gpu.types.GPUShaderCreateInfo();info.vertex_in(0,'VEC3','position');info.vertex_in(1,'VEC2','fragmentUV')
        info.push_constant('MAT4','mvp');info.push_constant('VEC4','st');info.push_constant('FLOAT','alpha')
        info.sampler(0,'FLOAT_2D','baseMap');info.vertex_out(uv);info.fragment_out(0,'VEC4','color')
        info.vertex_source('void main(){gl_Position=mvp*vec4(position,1.0);uv=fragmentUV*st.xy+st.zw;}')
        info.fragment_source('void main(){if(texture(baseMap,uv).a*alpha<0.01)discard;color=vec4(0.0);}')
        _alpha_depth_shader=gpu.shader.create_from_info(info)
        interface=gpu.types.GPUStageInterfaceInfo('endf_outline_blit');interface.smooth('VEC2','texCoord')
        info=gpu.types.GPUShaderCreateInfo();info.vertex_in(0,'VEC2','position');info.vertex_out(interface)
        info.sampler(0,'FLOAT_2D','image');info.fragment_out(0,'VEC4','color')
        info.vertex_source('void main(){gl_Position=vec4(position,0.0,1.0);texCoord=position*0.5+0.5;}')
        info.fragment_source('''void main(){vec2 d=0.5/vec2(textureSize(image,0));
            color=(texture(image,texCoord+vec2(-d.x,-d.y))+texture(image,texCoord+vec2(d.x,-d.y))+
                   texture(image,texCoord+vec2(-d.x,d.y))+texture(image,texCoord+d))*0.25;}''')
        _blit_shader=gpu.shader.create_from_info(info)
        _blit_batch=batch_for_shader(_blit_shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})
    return _target[2],_target[4]


def _depth_batch(obj,depsgraph,excluded=()):
    key=obj.as_pointer();version=_versions.get(key,0)
    entry=_occluders.get(key)
    if entry is not None and entry['uid']!=obj.session_uid:entry=None
    if entry is not None and entry['version']==version and entry['excluded']==excluded:return entry['batch']
    mesh=obj.evaluated_get(depsgraph).data
    positions=np.empty((len(mesh.vertices),3),np.float32);mesh.vertices.foreach_get('co',positions.ravel())
    signature=(len(mesh.vertices),len(mesh.loops),len(mesh.polygons))
    own=_entries.get(key)
    streamable=all(m.type=='ARMATURE' or (own is not None and m==own['modifier']) for m in obj.modifiers)
    if entry is not None and streamable and entry['topology']==signature and entry['excluded']==excluded and entry['buffer'] is not None:
        entry['buffer']=gpu.types.GPUVertBuf(entry['format'],len=len(positions));entry['buffer'].attr_fill('position',positions)
        entry['batch']=gpu.types.GPUBatch(type='TRIS',buf=entry['buffer'],elem=entry['index']);entry['version']=version
        return entry['batch']
    mesh.calc_loop_triangles()
    triangles=np.empty((len(mesh.loop_triangles),3),np.int32);mesh.loop_triangles.foreach_get('vertices',triangles.ravel())
    if excluded:
        poly=np.empty(len(mesh.loop_triangles),np.int32);mesh.loop_triangles.foreach_get('polygon_index',poly)
        slots=np.empty(len(mesh.polygons),np.int32);mesh.polygons.foreach_get('material_index',slots)
        triangles=triangles[~np.isin(slots[poly],excluded)]
    batch=buffer=fmt=index=None
    if len(triangles):
        fmt=gpu.types.GPUVertFormat();fmt.attr_add(id='position',comp_type='F32',len=3,fetch_mode='FLOAT')
        buffer=gpu.types.GPUVertBuf(fmt,len=len(positions));buffer.attr_fill('position',positions)
        index=gpu.types.GPUIndexBuf(type='TRIS',seq=triangles)
        batch=gpu.types.GPUBatch(type='TRIS',buf=buffer,elem=index)
    _occluders[key]={'version':version,'batch':batch,'buffer':buffer,'topology':signature,'format':fmt,'index':index,'uid':obj.session_uid,'excluded':excluded}
    return batch


def draw_view(scene,view_layer,space,region,view,projection,depsgraph):
    if bpy.app.is_job_running('RENDER') or space.shading.type not in {'MATERIAL','RENDERED'}:return
    before=time.perf_counter()
    scene_key=scene.as_pointer();frame=scene.frame_current_final
    if _scene_frames.get(scene_key)!=frame:
        # frame_set may run frame handlers without depsgraph_update_post.
        # Never reuse a previous pose's outline or occluder buffers.
        for obj in scene.objects:
            if obj.type=='MESH':
                key=obj.as_pointer();_dirty.add(key);_versions[key]=_versions.get(key,0)+1
        _scene_frames[scene_key]=frame
    depth=gpu.state.depth_test_get();mask=gpu.state.depth_mask_get();blend=gpu.state.blend_get();viewport=gpu.state.viewport_get()
    try:
        # A separate coverage buffer gives the post-view pass stable edge AA,
        # without depending on EEVEE's temporal history.
        scale=2 if max(viewport[2],viewport[3])<=4096 else 1
        width,height=viewport[2]*scale,viewport[3]*scale
        color,target=_render_target(width,height)
        for key,entry in list(_entries.items()):
            try:
                obj=entry['object']
                if obj.name not in scene.objects or obj.get('sora_render_mode')=='BASIC' or not entry['modifier'].show_viewport or not obj.visible_get(view_layer=view_layer,viewport=space):continue
                scope=(depsgraph.scene.original.as_pointer(),depsgraph.view_layer.name)
                if entry['batches'] is None or key in _dirty or entry.get('eval_scope')!=scope:
                    _upload(entry,depsgraph);_dirty.discard(key)
            except (ReferenceError,ValueError,RuntimeError,KeyError) as error:
                entry['failed']=str(error);_warn(entry['object'],error)
        with target.bind():
            gpu.state.viewport_set(0,0,width,height);target.clear(color=(0,0,0,0),depth=1.0)
            gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True);gpu.state.blend_set('NONE');gpu.state.face_culling_set('NONE')
            # EEVEE's resolved viewport does not guarantee an attachment with
            # usable scene depth for custom POST_VIEW draws. Keep our own GPU
            # depth pass so outlines cannot paint over foreground geometry.
            for obj in scene.objects:
                if obj.type!='MESH' or obj.display_type in {'WIRE','BOUNDS'} or not obj.visible_get(view_layer=view_layer,viewport=space):continue
                own=_entries.get(obj.as_pointer())
                if own is not None and (own.get('failed') or own['batches'] is None):own=None
                excluded=tuple(p['slot'] for p in own['programs'] if p['depth_clip']) if own else ()
                batch=_depth_batch(obj,depsgraph,excluded)
                if batch is not None:
                    _depth_shader.bind();_depth_shader.uniform_float('mvp',projection@view@obj.matrix_world);batch.draw(_depth_shader)
                if own:
                    for program,part in zip(own['programs'],own['batches']):
                        if part is None or program['depth_clip'] is None:continue
                        image,st,alpha=program['depth_clip'];_alpha_depth_shader.bind()
                        _alpha_depth_shader.uniform_float('mvp',projection@view@obj.matrix_world)
                        _alpha_depth_shader.uniform_float('st',st);_alpha_depth_shader.uniform_float('alpha',alpha)
                        _alpha_depth_shader.uniform_sampler('baseMap',gpu.texture.from_image(image));part.draw(_alpha_depth_shader)
            gpu.state.depth_mask_set(False);gpu.state.blend_set('ALPHA')
            for key,entry in list(_entries.items()):
                try:
                    obj=entry['object']
                    if entry.get('failed'):continue
                    if obj.name not in scene.objects or not entry['modifier'].show_viewport or not obj.visible_get(view_layer=view_layer,viewport=space):continue
                    if entry['batches'] is None or key in _dirty:
                        _upload(entry,depsgraph);_dirty.discard(key)
                    uniforms=view_data(obj,entry['tree'],view,projection,region.width,region.height,space.clip_start,space.clip_end)
                    mvp=projection@view@obj.matrix_world
                    gpu.state.face_culling_set('FRONT' if obj.matrix_world.to_3x3().determinant()>0 else 'BACK')
                    for program,batch in zip(entry['programs'],entry['batches']):
                        if batch is None:continue
                        shader=program['shader'];shader.bind()
                        shader.uniform_float('modelViewProjection',mvp);shader.uniform_float('viewData',uniforms)
                        for name,image in program['textures']:shader.uniform_sampler(name,gpu.texture.from_image(image))
                        batch.draw(shader)
                except (ReferenceError,ValueError,RuntimeError,KeyError) as error:
                    entry['failed']=str(error);_warn(entry['object'],error)
        gpu.state.viewport_set(*viewport);gpu.state.face_culling_set('NONE');gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False);gpu.state.blend_set('ALPHA_PREMULT')
        _blit_shader.bind();_blit_shader.uniform_sampler('image',color);_blit_batch.draw(_blit_shader)
    finally:
        gpu.state.viewport_set(*viewport);gpu.state.face_culling_set('NONE');gpu.state.depth_mask_set(mask);gpu.state.depth_test_set(depth);gpu.state.blend_set(blend)
    _stats['draws']+=1;_stats['last_draw_ms']=(time.perf_counter()-before)*1000


def _draw():
    context=bpy.context
    if context.region_data is None or _busy or not _entries:return
    draw_view(context.scene,context.view_layer,context.space_data,context.region,
              context.region_data.view_matrix,context.region_data.window_matrix,context.evaluated_depsgraph_get())


@bpy.app.handlers.persistent
def _changed(scene,depsgraph):
    if bpy.app.is_job_running('RENDER') or depsgraph.mode=='RENDER':return
    for update in depsgraph.updates:
        original=update.id.original
        if isinstance(original,bpy.types.Object) and update.is_updated_geometry:
            key=original.as_pointer();_dirty.add(key);_versions[key]=_versions.get(key,0)+1
        elif isinstance(original,bpy.types.Mesh):
            for key,entry in _entries.items():
                if entry['object'].data==original:entry['full_dirty']=True;_dirty.add(key)
            for obj in scene.objects:
                if obj.type=='MESH' and obj.data==original:_occluders.pop(obj.as_pointer(),None)
        elif isinstance(original,bpy.types.NodeTree):_program_dirty.add(original.as_pointer())


@bpy.app.handlers.persistent
def _loaded(*_):
    global _target
    _entries.clear();_dirty.clear();_warned.clear();_versions.clear();_occluders.clear();_scene_frames.clear();_program_dirty.clear();_failures.clear();_target=None


def ensure_registered():
    global _draw_handle
    if bpy.app.background:return
    if _draw_handle is None:_draw_handle=bpy.types.SpaceView3D.draw_handler_add(_draw,(),'WINDOW','POST_VIEW')
    if _changed not in bpy.app.handlers.depsgraph_update_post:bpy.app.handlers.depsgraph_update_post.append(_changed)
    if _loaded not in bpy.app.handlers.load_post:bpy.app.handlers.load_post.append(_loaded)


def unregister():
    global _draw_handle
    if _draw_handle is not None:bpy.types.SpaceView3D.draw_handler_remove(_draw_handle,'WINDOW');_draw_handle=None
    for handlers,fn in ((bpy.app.handlers.depsgraph_update_post,_changed),(bpy.app.handlers.load_post,_loaded)):
        if fn in handlers:handlers.remove(fn)
    for tree in getattr(bpy.data,'node_groups',()):
        if tree.bl_idname=='GeometryNodeTree' and _switch(tree) is not None:_remove_switch(tree)
    _loaded()
