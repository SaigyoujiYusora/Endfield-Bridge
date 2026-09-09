"""Reversible material pipelines on an invariant imported geometry frame."""
import hashlib
import json
import bpy
from bpy.props import StringProperty
from . import tasks
from .tasks import TaskOperator

MODE='sora_render_mode'
SIGNATURE='sora_render_source_signature'


def source_signature(document):
    contract={'materials':document['materials'],'textureDescriptors':document.get('textureDescriptors'),
              'textures':[(t['name'],hashlib.sha256(t['png'].encode('ascii')).hexdigest()) for t in document.get('textures') or []],
              'meshes':[{key:m.get(key) for key in ('name','sourceId','material','materialSlots','submeshCount')} for m in document['meshes']]}
    return hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def selected_collection(context):
    obj=context.object
    if obj is None or not obj.get('sora_instance'):return None
    return next((c for c in obj.users_collection if c.get('sora_instance')==obj['sora_instance']),None)


def meshes(collection):
    return sorted((o for o in collection.objects if o.type=='MESH' and 'sora_render_mesh_index' in o),key=lambda o:o['sora_render_mesh_index'])


def cache(obj,mode):
    prefix='sora_render_'+mode+'_'
    old_count=obj.get(prefix+'count',0)
    for i in range(old_count):
        key=prefix+str(i)
        if key in obj:del obj[key]
    obj[prefix+'count']=len(obj.data.materials)
    obj[prefix+'occupied']=json.dumps([m is not None for m in obj.data.materials])
    for i,material in enumerate(obj.data.materials):
        if material is not None:obj[prefix+str(i)]=material
    obj[prefix+'polygons']=[p.material_index for p in obj.data.polygons]
    if mode=='RURI':
        obj[prefix+'modifiers']=json.dumps([(m.name,m.show_viewport,m.show_render) for m in obj.modifiers
            if m.type=='NODES' and m.node_group and m.node_group.get('sora_instance')==obj['sora_instance']])


def activate(obj,mode):
    prefix='sora_render_'+mode+'_'
    if prefix+'count' not in obj:raise ValueError('Render cache is unavailable')
    indices=obj[prefix+'polygons']
    if len(indices)!=len(obj.data.polygons):raise ValueError('Mesh topology changed; cached material assignments cannot be restored')
    materials=[obj.get(prefix+str(i)) for i in range(obj[prefix+'count'])]
    occupied=json.loads(obj.get(prefix+'occupied','[]'))
    if len(occupied)!=len(materials) or any(expected and material is None for expected,material in zip(occupied,materials)):
        raise ValueError('A cached material was deleted; source mode retained')
    obj.data.materials.clear()
    for material in materials:obj.data.materials.append(material)
    for polygon,index in zip(obj.data.polygons,indices):polygon.material_index=index
    for name,viewport,render in json.loads(obj.get('sora_render_RURI_modifiers','[]')):
        modifier=obj.modifiers.get(name)
        if modifier is None:
            if mode=='BASIC':continue
            raise ValueError('Cached NPR modifier was removed: '+name)
        modifier.show_viewport=viewport if mode=='RURI' else False
        modifier.show_render=render if mode=='RURI' else False
    obj[MODE]=mode


def initialize(collection,document,mode,canonical):
    mode='RURI' if mode in {'RURI','NPR'} else 'BASIC'
    collection[MODE]=mode
    collection[SIGNATURE]=source_signature(document)
    collection['sora_render_canonical']=bool(canonical)
    for obj in meshes(collection):
        obj[MODE]=mode
        cache(obj,mode)


def switch_steps(context,collection,mode,document=None):
    from .materials import build_material,load_images
    from . import ruri_adapter
    mode='RURI' if mode=='RURI' else 'BASIC'
    current=collection.get(MODE)
    if current==mode:return
    if current not in {'RURI','BASIC'} or not collection.get('sora_render_canonical'):
        raise ValueError('Reimport with the current addon to establish the invariant render frame')
    objects=meshes(collection)
    if not objects or any(o.data.users!=1 for o in objects):
        raise ValueError('Mode switching requires instance-local mesh datablocks')
    ready=all('sora_render_'+mode+'_count' in obj for obj in objects)
    if not ready and (document is None or source_signature(document)!=collection[SIGNATURE]):
        raise ValueError('Source material/texture/slot signature changed; reimport before rebuilding a render mode')
    token=collection['sora_instance']
    before={name:{x.as_pointer() for x in getattr(bpy.data,name)} for name in ('materials','images','node_groups')}
    modifier_before={obj.as_pointer():{m.as_pointer() for m in obj.modifiers} for obj in objects}
    basis_before={obj.as_pointer():{key:list(obj[key]) if key in obj else None for key in ('ruri_face_basis0','ruri_face_basis1','ruri_face_basis2')} for obj in objects}
    for obj in objects:cache(obj,current)
    try:
        if not ready:
            images={};owned=[]
            for index,texture in enumerate(document.get('textures') or []):
                yield {'stage':'Loading '+mode+' texture views','completed':index,'total':len(document['textures'])}
                images.update(load_images([texture],token,owned,document.get('textureDescriptors'),mode=='RURI'))
            built={}
            for index,obj in enumerate(objects):
                yield {'stage':'Building '+mode+' instance materials','completed':index,'total':len(objects)}
                source=document['meshes'][obj['sora_render_mesh_index']]
                slots=source.get('materialSlots')
                indices=[]
                if slots is not None and all(i>=0 for i in slots):
                    count=source.get('submeshCount',1)
                    indices=[[slots[i]] if mode=='RURI' else (slots[i:] if i==count-1 else [slots[i]])
                             for i in range(len(slots) if mode=='RURI' else count)]
                elif source['material']>=0:indices=[[source['material']]]
                materials=[]
                for group in indices:
                    key=tuple(group)
                    if key not in built:built[key]=build_material([document['materials'][i] for i in group],images,token,mode)
                    materials.append(built[key])
                obj.data.materials.clear()
                for material in materials:obj.data.materials.append(material)
                for polygon,index in zip(obj.data.polygons,source['triangleSlots']):polygon.material_index=index
                obj[MODE]=mode
            if mode=='RURI' and not any(material.get('ruri_uber_stack') for material in built.values()):
                raise ValueError('No supported NPR materials were built; original mode retained')
            if mode=='RURI':
                yield {'stage':'Binding NPR vertex and outline stages'}
                ruri_adapter.finish_import(context,objects)
            for obj in objects:cache(obj,mode)
        for index,obj in enumerate(objects):
            yield {'stage':'Activating '+mode,'completed':index,'total':len(objects)}
            activate(obj,mode)
        collection[MODE]=mode
        context.view_layer.update()
    except BaseException:
        for obj in objects:
            for modifier in list(obj.modifiers):
                if modifier.as_pointer() not in modifier_before[obj.as_pointer()]:obj.modifiers.remove(modifier)
            if not ready:
                prefix='sora_render_'+mode+'_'
                for key in list(obj.keys()):
                    if key.startswith(prefix):del obj[key]
            activate(obj,current)
            for key,value in basis_before[obj.as_pointer()].items():
                if value is None:
                    if key in obj:del obj[key]
                else:obj[key]=value
        collection[MODE]=current
        # Remove only unused data created during this switch, not cached or shared data.
        while True:
            removed=False
            for name in ('materials','node_groups','images'):
                for item in list(getattr(bpy.data,name)):
                    if item.as_pointer() not in before[name] and item.get('sora_instance')==token and item.users==0:
                        getattr(bpy.data,name).remove(item)
                        removed=True
            if not removed:break
        raise


class SORA_OT_render_mode(TaskOperator,bpy.types.Operator):
    bl_idname='sora.instance_render_mode'
    bl_label='Switch instance rendering'
    bl_options={'REGISTER','UNDO'}
    mode:StringProperty(default='BASIC')
    def execute(self,context):
        if self.mode not in {'RURI','BASIC'}:
            self.report({'ERROR'},'Unknown render mode');return {'CANCELLED'}
        collection=selected_collection(context)
        if collection is None or not collection.get('sora_render_canonical'):
            self.report({'ERROR'},'Select an instance imported with canonical render-mode support')
            return {'CANCELLED'}
        obj=next((o for o in collection.objects if o.get('sora_asset')),None)
        if obj is None:
            self.report({'ERROR'},'Instance source identity is missing');return {'CANCELLED'}
        def complete(document):
            yield from switch_steps(context,collection,self.mode,document)
            context.scene.sora.status='Instance render mode: '+self.mode
        try:
            if all('sora_render_'+self.mode+'_count' in mesh for mesh in meshes(collection)):
                return tasks.start_local(self,context,'Cached render-mode switch',complete)
            return tasks.start(self,context,'scene',{'path':obj['sora_database'],'asset':obj['sora_asset'],
                'root':bpy.path.abspath(context.scene.sora.game_root)},complete)
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


def draw(layout,context):
    collection=selected_collection(context)
    layout.label(text='Current instance: '+str(collection.get(MODE,'source unavailable') if collection else 'none'))
    row=layout.row(align=True)
    row.enabled=collection is not None and bool(collection.get('sora_render_canonical'))
    row.operator('sora.instance_render_mode',text='NPR').mode='RURI'
    row.operator('sora.instance_render_mode',text='Basic PBR').mode='BASIC'
    if collection and not collection.get('sora_render_canonical'):layout.label(text='Reimport to enable reversible modes')


def register():bpy.utils.register_class(SORA_OT_render_mode)
def unregister():bpy.utils.unregister_class(SORA_OT_render_mode)
