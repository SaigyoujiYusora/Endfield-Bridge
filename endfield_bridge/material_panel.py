"""ENDF NPR-Shader material editor using the attributed vendored Stack contract.

Rows and grouping come from vendor/ruri_npr (AGPL-3.0; see vendor/ruri_npr/LICENSE.txt).
One operator dialog owns transient RNA values; material snapshots remain the
persistent source of truth. No per-parameter classes or dynamic Python evaluation.
"""
import math
import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty


def material_of(context):
    return getattr(context, 'material', None) or getattr(getattr(context, 'object', None), 'active_material', None)


def stack_for(material):
    if material is None or not str(material.get('ruri_uber_stack', '')).startswith('endf_npr_'):
        return None
    from .ruri_adapter import stacks
    return next((s for s in stacks() if s.panel_claims(material)), None)


def editable(material):
    return (material is not None and material.library is None
            and material.override_library is None and material.is_editable
            and material.node_tree is not None and material.node_tree.library is None)


def groups_for(stack, material):
    groups = [dict(g, rows=list(g['rows'])) for g in stack.panel_rows(material)]
    seen = {r['name'] for g in groups for r in g['rows']}
    # Compiled uniforms can include native controls omitted from the authored UI.
    # Preserve manifest types/defaults; never infer parameter offsets or eval names.
    authored = {r['name']: r for g in stack.INTERFACE for r in g['rows']}
    extra = []
    part = material.get('ruri_uber_part')
    if part in stack.m['parts']:
        for name, kind, _texel, _component, default, tail in stack.part(part)['params']:
            if name in seen:
                continue
            row = authored.get(name)
            if row is None:
                row = {'name': name, 'label': name, 'kind': 'VALUE' if kind == 'F' else 'VECTOR',
                       'size': 1 if kind == 'F' else 4 if kind == 'V4' else 3,
                       'default': [default[0]] if kind == 'F' else list(default) + ([tail] if kind == 'V4' else [])}
            if name.endswith('_ST') and kind == 'V4':
                texture_row = next((r for g in groups for r in g['rows']
                                    if r['kind'] == 'TEXTURE' and r['name'] == name[:-3]), None)
                row = dict(row, st_row=texture_row or {'name': name[:-3], 'kind': 'TEXTURE'})
            extra.append(row)
    if extra:
        groups.append({'name': 'Native Parameters', 'gate': None, 'rows': extra})
    return groups


def resolve(material_name, parameter):
    material = bpy.data.materials.get(material_name)
    stack = stack_for(material)
    if stack is None or not editable(material):
        raise ValueError('Select a local editable ENDF NPR-Shader material; linked references are read-only')
    row = next((r for g in groups_for(stack, material) for r in g['rows'] if r['name'] == parameter), None)
    if row is None:
        raise ValueError('Parameter is not present in this material')
    return material, stack, row


def value_of(stack, material, row, snapshot):
    value = snapshot['values'].get(row['name'])
    if value is None:
        value = stack._param_read(material, row['name'])
    if value is None:
        value = row.get('default', [0.0])
    if row['kind'] in {'VALUE', 'SLIDER', 'INT', 'SWITCH'} and isinstance(value, (list, tuple)):
        value = value[0]
    return value


def _color_changed(operator, context):
    operator.vector = operator.color


class ENDF_OT_npr_parameter(bpy.types.Operator):
    bl_idname = 'endf.npr_parameter'
    bl_label = 'Edit ENDF NPR Parameter'
    bl_description = 'Edit this material parameter and update its shader bindings'
    bl_options = {'REGISTER', 'UNDO'}

    material_name: StringProperty(options={'HIDDEN'})
    parameter: StringProperty(options={'HIDDEN'})
    scalar: FloatProperty(name='Value')
    integer: IntProperty(name='Value')
    switch: BoolProperty(name='Enabled')
    vector: FloatVectorProperty(name='Components', size=4)
    color: FloatVectorProperty(name='Color', size=4, subtype='COLOR', min=0.0, soft_max=1.0, default=(1, 1, 1, 1), update=_color_changed)
    image_name: StringProperty(name='Image')
    tiling: FloatVectorProperty(name='Tiling', size=2, default=(1, 1))
    offset: FloatVectorProperty(name='Offset', size=2)

    def invoke(self, context, event):
        try:
            material, stack, row = resolve(self.material_name, self.parameter)
            snapshot = stack.panel_read(material)
            kind = row['kind']
            if kind == 'TEXTURE':
                image = snapshot['images'].get(row['name'])
                self.image_name = image.name if image else ''
                st = snapshot['st'].get(row['name'], (1, 1, 0, 0))
                self.tiling, self.offset = st[:2], st[2:]
            else:
                value = value_of(stack, material, row, snapshot)
                if kind == 'SWITCH':
                    self.switch = bool(value)
                elif kind == 'INT':
                    self.integer = int(value)
                elif kind in {'VALUE', 'SLIDER'}:
                    self.scalar = float(value)
                else:
                    vector = (list(value) + [0.0] * 4)[:4]
                    self.color = tuple(max(0.0, x) for x in vector)
                    self.vector = vector
            return context.window_manager.invoke_props_dialog(self, width=480)
        except (ValueError, KeyError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def draw(self, context):
        layout = self.layout
        try:
            _material, _stack, row = resolve(self.material_name, self.parameter)
        except ValueError as error:
            layout.label(text=str(error), icon='ERROR')
            return
        layout.label(text=row.get('label', row['name']))
        layout.label(text=row['name'])
        kind = row['kind']
        if kind == 'TEXTURE':
            layout.prop_search(self, 'image_name', bpy.data, 'images')
            if row.get('has_st'):
                layout.prop(self, 'tiling')
                layout.prop(self, 'offset')
        elif kind in {'SWITCH', 'INT', 'VALUE', 'SLIDER'}:
            layout.prop(self, 'switch' if kind == 'SWITCH' else 'integer' if kind == 'INT' else 'scalar')
            if kind == 'SLIDER':
                layout.label(text=f"Native range: {row.get('min')} to {row.get('max')}")
        else:
            # Numeric components preserve HDR and signed values exactly. COLOR
            # picker is supplemental; only a deliberate picker edit replaces them.
            if kind in {'COLOR', 'HDRCOLOR'}:
                layout.label(text='Linear RGBA; values above 1 are supported')
                layout.prop(self, 'color')
            for i in range(row['size']):
                layout.prop(self, 'vector', index=i, text=('RGBA' if kind in {'COLOR', 'HDRCOLOR'} else 'XYZW')[i])

    def execute(self, context):
        try:
            material, stack, row = resolve(self.material_name, self.parameter)
            kind = row['kind']
            if kind == 'TEXTURE':
                image = bpy.data.images.get(self.image_name) if self.image_name else None
                if image is None:
                    raise ValueError('Choose an existing image; empty texture replacement is not supported')
                if not all(math.isfinite(x) for x in (*self.tiling, *self.offset)):
                    raise ValueError('Texture transform must be finite')
                stack.panel_write_image(material, row, image)
                if row.get('has_st'):
                    stack.panel_write_st(material, row, self.tiling, self.offset)
            else:
                value = (self.switch if kind == 'SWITCH' else self.integer if kind == 'INT'
                         else self.scalar if kind in {'VALUE', 'SLIDER'} else list(self.vector)[:row['size']])
                if not all(math.isfinite(x) for x in (value if isinstance(value, list) else [value])):
                    raise ValueError('Parameter must be finite')
                if row.get('st_row'):
                    stack.panel_write_st(material, row['st_row'], value[:2], value[2:])
                else:
                    stack.panel_write(material, row, value)
            if context.area:
                context.area.tag_redraw()
            return {'FINISHED'}
        except (ValueError, KeyError, TypeError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class ENDF_OT_npr_rig(bpy.types.Operator):
    bl_idname = 'endf.npr_rig'
    bl_label = 'Edit Head Bone Reference'
    bl_options = {'REGISTER', 'UNDO'}
    material_name: StringProperty(options={'HIDDEN'})
    armature_name: StringProperty(name='Armature')
    bone_name: StringProperty(name='Bone')

    def invoke(self, context, event):
        obj = getattr(context, 'object', None)
        rig = obj if obj and obj.type == 'ARMATURE' else obj.find_armature() if obj else None
        self.armature_name = rig.name if rig else ''
        mat = bpy.data.materials.get(self.material_name)
        stack = stack_for(mat)
        row = stack.panel_rig(mat, rig) if stack else None
        self.bone_name = row['bone'] if row else ''
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        self.layout.prop_search(self, 'armature_name', bpy.data, 'objects')
        rig = bpy.data.objects.get(self.armature_name)
        if rig and rig.type == 'ARMATURE':
            self.layout.prop_search(self, 'bone_name', rig.data, 'bones')
        else:
            self.layout.label(text='Choose an armature', icon='INFO')

    def execute(self, context):
        mat = bpy.data.materials.get(self.material_name)
        stack = stack_for(mat)
        rig = bpy.data.objects.get(self.armature_name)
        if stack is None or not editable(mat) or rig is None or rig.type != 'ARMATURE' or stack.panel_rig(mat, rig) is None:
            self.report({'ERROR'}, 'Choose a local ENDF material and armature with a head reference')
            return {'CANCELLED'}
        ok, message = stack.panel_write_rig(mat, rig, self.bone_name)
        self.report({'INFO'} if ok else {'ERROR'}, message)
        return {'FINISHED'} if ok else {'CANCELLED'}


class ENDF_PT_npr_material(bpy.types.Panel):
    bl_label = 'ENDF NPR-Shader'
    bl_idname = 'ENDF_PT_npr_material'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'

    @classmethod
    def poll(cls, context):
        return stack_for(material_of(context)) is not None

    def draw(self, context):
        layout = self.layout
        material = material_of(context)
        stack = stack_for(material)
        layout.label(text=material.get('ruri_uber_part', material.name))
        layout.prop(context.window_manager, 'endf_npr_search', text='', icon='VIEWZOOM')
        query = context.window_manager.endf_npr_search.casefold().strip()
        if not editable(material):
            layout.label(text='Linked reference: read-only', icon='LINKED')
        body = layout.column()
        body.enabled = editable(material)
        rig = stack.panel_rig(material)
        if rig and (not query or query in ('head bone ' + rig['prop'] + rig['label']).casefold()):
            op = body.operator('endf.npr_rig', text=rig['label'] + ': ' + (rig['unity'] or 'Unassigned'))
            op.material_name = material.name
        snapshot = stack.panel_read(material)
        found = 0
        for index, group in enumerate(groups_for(stack, material)):
            rows = [r for r in group['rows'] if not query or query in (r['name'] + ' ' + r.get('label', '') + ' ' + group['name']).casefold()]
            if not rows:
                continue
            found += len(rows)
            if query:
                content = body.box()
                content.label(text=group['name'])
            else:
                header, content = body.panel('endf_npr_group_' + str(index), default_closed=True)
                header.label(text=group['name'])
                if content is None:
                    continue
            for row in rows:
                value = snapshot['images'].get(row['name']) if row['kind'] == 'TEXTURE' else value_of(stack, material, row, snapshot)
                value_text = value.name if row['kind'] == 'TEXTURE' and value else 'Unassigned' if row['kind'] == 'TEXTURE' else ', '.join(f'{v:.3g}' for v in value) if isinstance(value, (list, tuple)) else str(round(value, 4)) if isinstance(value, float) else str(value)
                line = content.row()
                op = line.operator('endf.npr_parameter', text=row['name'] + ': ' + value_text)
                op.material_name, op.parameter = material.name, row['name']
        if not found:
            body.label(text='No matching parameters')


CLASSES = (ENDF_OT_npr_parameter, ENDF_OT_npr_rig, ENDF_PT_npr_material)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.endf_npr_search = StringProperty(name='Search native parameters', options={'SKIP_SAVE', 'TEXTEDIT_UPDATE'})


def unregister():
    del bpy.types.WindowManager.endf_npr_search
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
