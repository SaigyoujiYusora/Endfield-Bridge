"""Reuse the owner's held-arrow material with the projectile's native mesh attributes."""
import json
from mathutils import Matrix
from . import equipment as eq, ruri_adapter

SOURCE = 'sora_projectile_material_source'


def material_source(owner, projectile_id, held_events=()):
    indices = {event['weaponIndex'] for event in held_events}
    # Ground attacks have no arrow-show buff events. This is an explicit reuse
    # policy for Typhoea's arrow, not a claim that the VFX uses CharacterNPR.
    if not indices and projectile_id.startswith('projectile_chr_0034_typhoea_'):
        declaration = json.loads(owner[eq.CONTRACT])['declaration']['dedicatedEquipment']
        indices = {slot['weaponIndex'] for slot in declaration if slot['resourcePath'] ==
                   'assets/beyond/dynamicassets/gameplay/prefabs/weapons/wpn_misc_0054_01.prefab'}
    children = [child for child in eq.owned_children(owner, 'dedicated')
                if child.get('sora_equipment_slot', '').rsplit(':', 1)[-1] in {str(i) for i in indices}]
    candidates = [obj for child in children for obj in child.objects
                  if obj.type == 'MESH' and len(obj.data.materials) == 1 and obj.data.materials[0]]
    if not candidates or len({obj.data.materials[0] for obj in candidates}) != 1:
        raise ValueError('弹体预览缺少唯一的同角色手持箭材质')
    return candidates[0]


def populate(obj, source, donor):
    """Populate a new mesh; return the compensating rotation for the NPR local frame.

    UVs belong to the projectile topology. Never index the held-arrow UVs with
    projectile vertex indices, and never duplicate or modify the shared material.
    """
    canonical = any(c.get('sora_render_canonical') for c in donor.users_collection)
    if canonical:
        source = ruri_adapter.object_frame({'bones': [], 'meshes': [source]})['meshes'][0]
    mesh = obj.data
    mesh.from_pydata(source['positions'], [], source['triangles'])
    mesh.update()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    if source.get('normals'):
        mesh.normals_split_custom_set_from_vertices(source['normals'])
    uv_sets = source.get('uvSets') or ([{'set': 0, 'values': source['uv']}] if source.get('uv') else [])
    for uv in uv_sets:
        layer = mesh.uv_layers.new(name='UVMap' + (str(uv['set']) if uv['set'] else ''))
        for loop in mesh.loops:
            value = uv['values'][loop.vertex_index]
            layer.data[loop.index].uv = (value[0], value[1] if len(value) > 1 else 0)
        dimension = len(uv['values'][0]) if uv['values'] else 0
        for component in range(2, dimension):
            attr = mesh.attributes.new(name=f"SoraUV{uv['set']}_{'ZW'[component - 2]}", type='FLOAT', domain='POINT')
            for item, value in zip(attr.data, uv['values']):
                item.value = value[component]
        for key in ('nativeDimension', 'nativeFormat'):
            if uv.get(key) is not None:
                suffix = 'dimension' if key == 'nativeDimension' else 'format'
                mesh[f"sora_uv{uv['set']}_native_{suffix}"] = uv[key]
    if source.get('colors'):
        attr = mesh.color_attributes.new(name='SoraColor', type='FLOAT_COLOR', domain='POINT')
        for item, rgba in zip(attr.data, source['colors']):
            item.color = rgba
    if source.get('tangents'):
        tangent = mesh.attributes.new(name='SoraTangent', type='FLOAT_VECTOR', domain='POINT')
        sign = mesh.attributes.new(name='SoraTangentSign', type='FLOAT', domain='POINT')
        for i, value in enumerate(source['tangents']):
            tangent.data[i].vector = value[:3]
            sign.data[i].value = value[3]
    mesh.materials.append(donor.data.materials[0])
    if canonical:
        ruri_adapter.prepare_mesh(obj, source)
    if source.get('sourceId'):
        obj['sora_source_id'] = source['sourceId']
    obj[SOURCE] = donor
    obj['sora_projectile_visual'] = 'native mesh and UVs; shared held-arrow material; no Unity particles/trails'
    return Matrix.Diagonal((-1., -1., 1.)).to_quaternion() if canonical else Matrix.Identity(3).to_quaternion()


def restore_material(obj):
    donor = obj.get(SOURCE)
    if donor and donor.type == 'MESH' and donor.data.materials and donor.data.materials[0]:
        if len(obj.data.materials) != 1 or obj.data.materials[0] != donor.data.materials[0]:
            obj.data.materials.clear()
            obj.data.materials.append(donor.data.materials[0])
