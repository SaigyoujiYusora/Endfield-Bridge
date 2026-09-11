"""Browse actual mesh material slots belonging to one imported instance."""
import json
import bpy
from bpy.props import StringProperty, IntProperty, PointerProperty, CollectionProperty, EnumProperty


def metadata(material):
    descriptor = json.loads(material.get('sora_material_descriptor', '{}'))
    source = descriptor.get('source') or {}
    shader = source.get('shaderName') or ''
    reference = material.get('sora_native_shader_ref') or material.get('sora_native_shader_id') or ''
    floats = descriptor.get('floats') or dict(material.get('ruri_uber_floats') or {})
    surface = floats.get('_SurfaceType')
    transparent = ('TRANSPARENT' if material.get('endf_npr_transparent_base') or
                   (surface is not None and surface >= 0.5) else
                   'OPAQUE' if surface is not None else 'UNKNOWN')
    return dict(part=str(material.get('ruri_uber_part') or descriptor.get('part') or ''),
                shader=shader, reference=reference, transparency=transparent)


def owners(root):
    yield root
    for child in root.children:
        if child.get('sora_owner_collection') == root:
            yield from owners(child)


def remember(self, context):
    if 0 <= self.selected < len(self.rows):
        row = self.rows[self.selected]
        self.material = row.material
        self.mesh = row.mesh
        self.slot = row.slot


def refresh(self, context):
    material,mesh,slot = self.material,self.mesh,self.slot
    self.rows.clear()
    self.selected = -1
    for collection in owners(self.id_data):
        role = collection.get('sora_equipment_role') or '本体'
        for obj in collection.objects:
            if obj.type != 'MESH':
                continue
            for index,item in enumerate(obj.material_slots):
                if item.material is None:
                    continue
                info = metadata(item.material)
                if self.part_filter.casefold() not in (info['part']+' '+obj.name).casefold():
                    continue
                if self.shader_filter.casefold() not in (info['shader']+' '+info['reference']).casefold():
                    continue
                if self.transparency != 'ALL' and info['transparency'] != self.transparency:
                    continue
                row = self.rows.add()
                row.material = item.material
                row.mesh = obj
                row.slot = index
                row.role = role
    self.selected = next((i for i,r in enumerate(self.rows)
                          if r.material == material and r.mesh == mesh and r.slot == slot),
                         0 if self.rows else -1)


class SORA_MaterialRow(bpy.types.PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    mesh: PointerProperty(type=bpy.types.Object)
    slot: IntProperty()
    role: StringProperty()


class SORA_MaterialBrowser(bpy.types.PropertyGroup):
    rows: CollectionProperty(type=SORA_MaterialRow)
    selected: IntProperty(default=-1, update=remember)
    material: PointerProperty(type=bpy.types.Material)
    mesh: PointerProperty(type=bpy.types.Object)
    slot: IntProperty(default=-1)
    part_filter: StringProperty(name='部位 / 网格', update=refresh)
    shader_filter: StringProperty(name='原生 Shader', update=refresh)
    transparency: EnumProperty(name='透明属性', items=[('ALL','全部',''),('TRANSPARENT','透明标志',''),
        ('OPAQUE','不透明标志',''),('UNKNOWN','未记录','')], default='ALL', update=refresh)


class SORA_UL_materials(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        layout.label(text=f'{item.role} · {item.material.name if item.material else "已移除"}', icon='MATERIAL')


class SORA_OT_material_refresh(bpy.types.Operator):
    bl_idname = 'sora.refresh_material_browser'
    bl_label = '读取 / 刷新实例材质'
    def execute(self, context):
        from .equipment import owner_collection
        root = owner_collection(context)
        if root is None:
            return {'CANCELLED'}
        refresh(root.sora_material_browser,context)
        return {'FINISHED'}


class SORA_OT_material_locate(bpy.types.Operator):
    bl_idname = 'sora.locate_material_slot'
    bl_label = '定位网格和材质槽'

    @classmethod
    def poll(cls, context):
        from . import tasks
        return context.mode == 'OBJECT' and not tasks.busy()

    def execute(self, context):
        from .equipment import owner_collection
        root = owner_collection(context)
        browser = root.sora_material_browser if root else None
        if browser is None or not 0 <= browser.selected < len(browser.rows):
            return {'CANCELLED'}
        row = browser.rows[browser.selected]
        obj = row.mesh
        if (obj is None or not any(obj in c.objects.values() for c in owners(root)) or
            row.slot >= len(obj.material_slots) or obj.material_slots[row.slot].material != row.material):
            self.report({'ERROR'},'材质槽已变化，请刷新实例材质')
            return {'CANCELLED'}
        if obj.name not in context.view_layer.objects or not obj.visible_get() or obj.hide_select:
            self.report({'ERROR'},'拥有此材质的网格当前不可见或不可选')
            return {'CANCELLED'}
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        obj.active_material_index = row.slot
        for area in context.screen.areas:
            if area.type == 'PROPERTIES' and area.spaces.active.pin_id is None:
                area.spaces.active.context = 'MATERIAL'
        return {'FINISHED'}


def draw(layout, context):
    from .equipment import owner_collection
    root = owner_collection(context)
    if root is None:
        layout.label(text='选择导入实例以浏览材质')
        return
    browser = root.sora_material_browser
    box = layout.box()
    box.label(text='当前实例材质（含所属装备）')
    box.operator('sora.refresh_material_browser', icon='FILE_REFRESH')
    box.prop(browser,'part_filter')
    box.prop(browser,'shader_filter')
    box.prop(browser,'transparency')
    box.template_list('SORA_UL_materials','',browser,'rows',browser,'selected',rows=5,maxrows=5)
    if 0 <= browser.selected < len(browser.rows):
        row = browser.rows[browser.selected]
        if row.material is None or row.mesh is None:
            box.label(text='材质或网格已移除，请刷新')
            return
        info = metadata(row.material)
        box.label(text=row.material.name)
        box.label(text=f'{row.mesh.name} · Slot {row.slot + 1}')
        box.label(text='部位: '+(info['part'] or '未记录'))
        box.label(text='Shader: '+(info['shader'] or '未记录'))
        box.label(text='透明属性: '+{'TRANSPARENT':'透明标志','OPAQUE':'不透明标志','UNKNOWN':'未记录'}[info['transparency']])
        if info['reference']:
            box.label(text='原生引用: '+info['reference'])
        box.operator('sora.locate_material_slot', icon='RESTRICT_SELECT_OFF')
        box.label(text='在 Properties 材质页编辑参数')


CLASSES=(SORA_MaterialRow,SORA_MaterialBrowser,SORA_UL_materials,SORA_OT_material_refresh,SORA_OT_material_locate)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.Collection.sora_material_browser = PointerProperty(type=SORA_MaterialBrowser)


def unregister():
    del bpy.types.Collection.sora_material_browser
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
