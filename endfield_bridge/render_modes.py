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
    from .equipment import owner_collection
    return owner_collection(context)


def owned_tree(collection):
    result=[collection]
    for child in collection.children:
        if child.get('sora_owner_collection')!=collection:
            raise ValueError('Render tree contains a collection outside this instance')
        result.extend(owned_tree(child))
    return result


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


def switch_tree_steps(context,root,mode,documents):
    tree=owned_tree(root)
    before_modes={c.as_pointer():c.get(MODE) for c in tree}
    missing={o.as_pointer():'sora_render_'+mode+'_count' not in o for c in tree for o in meshes(c)}
    before_data={name:{x.as_pointer() for x in getattr(bpy.data,name)} for name in ('materials','images','node_groups')}
    before_mods={o.as_pointer():{m.as_pointer() for m in o.modifiers} for c in tree for o in meshes(c)}
    tokens={c['sora_instance'] for c in tree}
    completed=[]
    # Reject unsupported/shared members before changing any member of the tree.
    for collection in tree:
        if (collection.get(MODE)!=mode and not collection.get('sora_render_canonical')) or any(o.data.users!=1 for o in meshes(collection)):
            raise ValueError('Reimport unsupported/shared members before switching the owned render tree')
        if any(missing[o.as_pointer()] for o in meshes(collection)):
            document=documents.get(collection.as_pointer())
            if document is None or source_signature(document)!=collection[SIGNATURE]:
                raise ValueError('An owned equipment render source changed or is unavailable')
    try:
        for collection in tree:
            yield from switch_steps(context,collection,mode,documents.get(collection.as_pointer()))
            completed.append(collection)
    except BaseException:
        for collection in reversed(completed):
            for _ in switch_steps(context,collection,before_modes[collection.as_pointer()],None):pass
        for collection in tree:
            for obj in meshes(collection):
                if missing[obj.as_pointer()]:
                    for key in list(obj.keys()):
                        if key.startswith('sora_render_'+mode+'_'):del obj[key]
                for modifier in list(obj.modifiers):
                    if modifier.as_pointer() not in before_mods[obj.as_pointer()]:obj.modifiers.remove(modifier)
        while True:
            removed=False
            for name in ('materials','node_groups','images'):
                for item in list(getattr(bpy.data,name)):
                    if item.as_pointer() not in before_data[name] and item.get('sora_instance') in tokens and item.users==0:
                        getattr(bpy.data,name).remove(item);removed=True
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
        tree=owned_tree(collection)
        pending=[c for c in tree if any('sora_render_'+self.mode+'_count' not in o for o in meshes(c))]
        base={'path':obj['sora_database'],'root':bpy.path.abspath(context.scene.sora.game_root)}
        jobs=[];keys={}
        dedicated=[c for c in pending if c.get('sora_equipment_role')=='dedicated']
        if dedicated:
            jobs.append({'key':'dedicated','method':'equipment-assembly','params':dict(base,asset=obj['sora_asset'],includeOwner=True)})
        for current in pending:
            if current in dedicated:continue
            if current==collection and dedicated:continue
            source=next((o for o in current.objects if o.get('sora_asset')),None)
            if source is None:
                self.report({'ERROR'},'Owned render source identity is missing');return {'CANCELLED'}
            key=source['sora_asset'];keys[current.as_pointer()]=key
            if not any(job['key']==key for job in jobs):jobs.append({'key':key,'method':'scene','params':dict(base,asset=key)})
        def complete(results):
            results=results or {};documents={}
            if 'dedicated' in results:
                packet=results['dedicated'];documents[collection.as_pointer()]=packet['scene']
                resources={r['resourceId']:r['scene'] for r in packet['equipment']['resources']}
                for child in dedicated:documents[child.as_pointer()]=resources[child['sora_equipment_resource']]
            for pointer,key in keys.items():documents[pointer]=results[key]
            yield from switch_tree_steps(context,collection,self.mode,documents)
            context.scene.sora.status='Owned character/equipment render mode: '+self.mode
        try:
            if not jobs:return tasks.start_local(self,context,'Cached owned render-tree switch',complete)
            return tasks.start_batch(self,context,jobs,complete)
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
