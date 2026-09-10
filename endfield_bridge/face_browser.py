"""Instance-local browsing of existing native face controls; no game resource parsing."""
import json

import bpy

CATEGORIES = [('ALL', '全部部位', ''), ('EYE', '眼', ''), ('BROW', '眉', ''),
              ('MOUTH', '嘴', ''), ('UNKNOWN', '未分类', '')]
KINDS = [('ALL', '全部类型', ''), ('CONTROL', '单控制', ''),
         ('PRESET', '组合预设', ''), ('SHADER', '材质驱动', '')]


def control_identity(data, index):
    source = data
    for extra in data.get('additionalSources') or ():
        if extra['controlStart'] <= index < extra['controlStart'] + extra['controlCount']:
            source = extra
            break
    return json.dumps(['control', source['sourceCab'], str(source['sourceObject']),
                       data['controls'][index]['id']], separators=(',', ':'))


def preset_identity(preset):
    return json.dumps(['preset', preset['sourcePath'], preset['name']], separators=(',', ':'))


def category(control):
    known = {'eye': 'EYE', 'eyes': 'EYE', '眼': 'EYE', 'brow': 'BROW', 'eyebrow': 'BROW',
             '眉': 'BROW', 'mouth': 'MOUTH', '嘴': 'MOUTH'}
    for field in ('category', 'partName'):
        if isinstance(control.get(field), str) and control[field]:
            return known.get(control[field].casefold(), 'UNKNOWN'), '原生字段 ' + field + ': ' + control[field]
    name = control['name'].casefold()
    for result, tokens in [('BROW', ('brow', '眉')), ('EYE', ('eye', 'lid', 'blink', '眼', '瞳')),
                           ('MOUTH', ('mouth', 'lip', 'jaw', 'tongue', '嘴', '唇', '舌'))]:
        if any(token in name for token in tokens):
            return result, '名称规则 name-rule-v1 推断（非原生部位枚举）'
    return 'UNKNOWN', '未识别部位；未解释原生 partType 数值'


def selected_changed(self, _context):
    if 0 <= self.index < len(self.rows):
        self.selected_identity = self.rows[self.index].identity


class SORA_FaceBrowserRow(bpy.types.PropertyGroup):
    identity: bpy.props.StringProperty()
    source_index: bpy.props.IntProperty()
    kind: bpy.props.StringProperty()
    category: bpy.props.StringProperty()
    evidence: bpy.props.StringProperty()
    favorite: bpy.props.BoolProperty(name='常用收藏', default=False)


class SORA_FaceBrowserState(bpy.types.PropertyGroup):
    rows: bpy.props.CollectionProperty(type=SORA_FaceBrowserRow)
    index: bpy.props.IntProperty(default=0, min=0, update=selected_changed)
    selected_identity: bpy.props.StringProperty()
    query: bpy.props.StringProperty(name='搜索原生名称')
    category: bpy.props.EnumProperty(name='部位', items=CATEGORIES)
    kind: bpy.props.EnumProperty(name='类型', items=KINDS)
    favorites_only: bpy.props.BoolProperty(name='常用', default=False)
    modified_only: bpy.props.BoolProperty(name='已修改控制', default=False,
        description='显示值不为零的单控制和材质控制；固定组合预设不属于已修改控制')
    initialized: bpy.props.BoolProperty(default=False)
    error: bpy.props.StringProperty()


def refresh(rig):
    from .face_controls import DATA, descriptor
    state = rig.sora_face_browser
    data = descriptor(rig[DATA])
    records = []
    for index, control in enumerate(data['controls']):
        part, evidence = category(control)
        records.append((control_identity(data, index), control['name'], index,
                        'SHADER' if control.get('shader') else 'CONTROL', part, evidence))
    for index, preset in enumerate(data['presets']):
        records.append((preset_identity(preset), preset['name'], index, 'PRESET',
                        *category(preset)))
    if len({record[0] for record in records}) != len(records):
        state.error = '原生表情身份重复；未重建浏览列表'
        return
    favorites = {row.identity for row in state.rows if row.favorite}
    selected = state.selected_identity
    state.rows.clear()
    for identity, name, index, kind, part, evidence in records:
        row = state.rows.add()
        row.identity, row.name, row.source_index = identity, name, index
        row.kind, row.category, row.evidence = kind, part, evidence
        row.favorite = identity in favorites
    state.index = next((i for i, row in enumerate(state.rows) if row.identity == selected), 0)
    state.selected_identity = state.rows[state.index].identity if state.rows else ''
    state.initialized = True
    state.error = ''


def visible(row, state, rig):
    from .face_controls import property_name
    return ((not state.query or state.query.casefold() in row.name.casefold())
            and (state.category == 'ALL' or row.category == state.category)
            and (state.kind == 'ALL' or row.kind == state.kind)
            and (not state.favorites_only or row.favorite)
            and (not state.modified_only or (row.kind != 'PRESET'
                 and abs(rig.get(property_name(row.source_index), 0.0)) > 1e-6)))


class SORA_UL_face_browser(bpy.types.UIList):
    def filter_items(self, _context, data, propname):
        return [self.bitflag_filter_item if visible(row, data, data.id_data) else 0
                for row in getattr(data, propname)], []

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index=0, _flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, 'favorite', text='', icon='SOLO_ON' if item.favorite else 'SOLO_OFF', emboss=False)
        row.label(text=item.name, icon={'PRESET': 'PRESET', 'SHADER': 'MATERIAL'}.get(item.kind, 'SHAPEKEY_DATA'))


class SORA_OT_face_browser_refresh(bpy.types.Operator):
    bl_idname = 'sora.face_browser_refresh'
    bl_label = '刷新表情列表'

    def execute(self, context):
        from .face_controls import rig_for
        rig = rig_for(context.object)
        if rig is None:
            return {'CANCELLED'}
        try:
            refresh(rig)
            if rig.sora_face_browser.error:
                raise ValueError(rig.sora_face_browser.error)
        except (KeyError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def refresh_all():
    from .face_controls import DATA
    for rig in bpy.data.objects:
        if rig.type == 'ARMATURE' and DATA in rig:
            try:
                refresh(rig)
            except (KeyError, TypeError, ValueError) as error:
                rig.sora_face_browser.error = str(error)


def draw(layout, context, rig, data):
    from . import face_controls, face_shader
    from . import wrapped_label
    state = rig.sora_face_browser
    if not state.initialized or state.error:
        layout.label(text=state.error or '表情列表尚未初始化', icon='INFO')
        layout.operator('sora.face_browser_refresh')
        return
    layout.prop(state, 'query', text='', icon='VIEWZOOM')
    row = layout.row(align=True)
    row.prop(state, 'category', text='')
    row.prop(state, 'kind', text='')
    row = layout.row(align=True)
    row.prop(state, 'favorites_only')
    row.prop(state, 'modified_only')
    row.operator('sora.face_browser_refresh', text='', icon='FILE_REFRESH')
    layout.template_list('SORA_UL_face_browser', '', state, 'rows', state, 'index', rows=6, maxrows=6)
    if not 0 <= state.index < len(state.rows):
        return
    selected = state.rows[state.index]
    if not visible(selected, state, rig):
        layout.label(text='请选择筛选结果中的表情', icon='INFO')
        return
    records = data['presets'] if selected.kind == 'PRESET' else data['controls']
    index = selected.source_index
    if not 0 <= index < len(records) or selected.identity != (
            preset_identity(records[index]) if selected.kind == 'PRESET' else control_identity(data, index)):
        layout.label(text='原生表情数据已改变，请刷新列表', icon='ERROR')
        return
    record = records[index]
    box = layout.box()
    wrapped_label(box, record['name'], context)
    box.label(text=dict((key, label) for key, label, _ in KINDS)[selected.kind] + ' · '
              + dict((key, label) for key, label, _ in CATEGORIES)[selected.category])
    wrapped_label(box, selected.evidence, context)
    if selected.kind == 'PRESET':
        box.label(text='叠加预设' if record['additive'] else '替换控制值')
        row = box.row()
        row.enabled = not record['missingControls']
        op = row.operator('sora.face_preset', text='应用组合预设')
        op.index = index
        op.identity = selected.identity
        if record['missingControls']:
            box.label(text=f"缺少 {len(record['missingControls'])} 个原生控制", icon='ERROR')
    else:
        row = box.row()
        row.enabled = selected.kind != 'SHADER' or bool(rig.get(face_shader.ENABLED))
        row.prop(rig, '["' + face_controls.property_name(index) + '"]', text='数值', slider=True)
        if selected.kind == 'SHADER':
            box.label(text=record['shader']['parameter'])


CLASSES = (SORA_FaceBrowserRow, SORA_FaceBrowserState, SORA_UL_face_browser, SORA_OT_face_browser_refresh)


def register():
    registered = []
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.Object.sora_face_browser = bpy.props.PointerProperty(type=SORA_FaceBrowserState)
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    if hasattr(bpy.types.Object, 'sora_face_browser'):
        del bpy.types.Object.sora_face_browser
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
