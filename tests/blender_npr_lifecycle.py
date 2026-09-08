"""Visible MCP regression: rolled bones, two NPR instances, private image cleanup.
Default runs and removes synthetic imports, preserving existing user datablocks.
For actual save/reload coverage set NPR_LIFECYCLE_PHASE='prepare_reload', execute,
save a COPY and reopen it via MCP, then execute with phase='verify_reload'.
The fixture stores expected state in the .blend; this script never saves a file.
"""
import base64
import json
import math
import struct
import zlib
from copy import deepcopy
from unittest.mock import patch

import bpy
from mathutils import Matrix, Quaternion, Vector
from endfield_bridge import scene as bridge_scene, ruri_adapter
from endfield_bridge.vendor.ruri_npr import ruri_endfield as runtime

PHASE = globals().get('NPR_LIFECYCLE_PHASE', 'complete')
TRACKED = ('objects', 'meshes', 'armatures', 'materials', 'node_groups', 'images', 'collections', 'actions')
STATE_KEY = 'endf_npr_lifecycle_fixture'

def snapshot():
    return {kind: {item.as_pointer() for item in getattr(bpy.data, kind)} for kind in TRACKED}

def owned(token):
    return [(kind, item.name) for kind in TRACKED for item in getattr(bpy.data, kind) if item.get('sora_instance') == token]

def close(a, b, epsilon=1e-5):
    return max(abs(x - y) for x, y in zip(a, b)) < epsilon

def flat(matrix):
    return [v for row in matrix for v in row]

def png(pixel):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 1, 1, 8, 6, 0, 0, 0))
    return base64.b64encode(data + chunk(b'IDAT', zlib.compress(bytes([0, *pixel]))) + chunk(b'IEND', b'')).decode()

def descriptor(part, transparent=False):
    stack = next(s for s in ruri_adapter.stacks() if s.post is None and part in s.PART_META)
    meta = stack.PART_META[part]
    floats = {'_OutlineWidth': 0.01, '_SurfaceType': float(transparent), '_Cull': 0}
    if meta.get('discriminator'):
        floats[meta['discriminator']] = 1
    return dict(source={'shaderName': meta['shader']}, floats=floats,
                colors={'_BaseColor': [0.8, 0.7, 0.6, 1]},
                textures={'_BaseMap': {'textureId': 'base', 'scale': [1, 1], 'offset': [0, 0]},
                          '_BumpMap': {'textureId': 'normal', 'scale': [1, 1], 'offset': [0, 0]}},
                renderState={'disabledPasses': []})

def fixture():
    materials = [dict(name='Lifecycle ' + name, baseColor=[0.8, 0.7, 0.6, 1], metallic=0,
                      roughness=0.5, npr=descriptor(part, transparent))
                 for name, part, transparent in [('Face', 'Face', False), ('Alpha', 'Standard', True)]]
    meshes = [dict(name='Lifecycle ' + name, positions=[[0, 0, 1], [0.4, 0, 1], [0, 0, 1.4]],
                   triangles=[[0, 1, 2]], normals=[[0, -1, 0]] * 3, uv=[[0, 0], [1, 0], [0, 1]],
                   material=index, weights=[dict(bone=1, vertex=v, weight=1.0) for v in range(3)], shapes=[])
              for index, name in enumerate(('Face', 'Alpha'))]
    return dict(name='ENDF-NPR Lifecycle',
                bones=[dict(name='Root', parent=-1, head=[0, 0, 0], tail=[0, 0, 1], roll=math.pi / 4),
                       dict(name='Bip001_Head', parent=0, head=[0, 0, 1], tail=[0.25, 0.1, 1.5], roll=math.pi / 3)],
                textures=[dict(name='base', png=png((180, 120, 80, 128)), linear=False),
                          dict(name='normal', png=png((128, 128, 255, 255)), linear=True)],
                textureDescriptors=[dict(id='normal', nativeFormat=27)], materials=materials, meshes=meshes)

def members(collection):
    rig = next(o for o in collection.objects if o.type == 'ARMATURE')
    face = next(o for o in collection.objects if o.type == 'MESH' and any(m.get('ruri_uber_part') == 'Face' for m in o.data.materials))
    return rig, face

def verify_live(collections, references):
    for index, collection in enumerate(collections):
        token = collection['sora_instance']
        rig, face = members(collection)
        assert close(flat(rig.matrix_world), references[index]['world']), 'Object world matrix changed'
        assert close(flat(rig.pose.bones['Bip001_Head'].matrix_basis), references[index]['pose']), 'Head pose changed'
        # Frontend sync scans scene materials directly; the vendor queue is unused.
        evaluated_face = face.evaluated_get(bpy.context.evaluated_depsgraph_get())
        expected = references[index]['basis']
        for axis in range(3):
            key = runtime.RIG_OBJECT_PROP + str(axis)
            assert close(face[key], expected[axis]), (face.name, key, list(face[key]), expected[axis])
            assert close(evaluated_face[key], expected[axis]), 'Evaluated rig basis was not restored'
        images = [image for image in bpy.data.images if image.get('sora_instance') == token]
        private = [image for image in images if image.get('sora_image_view')]
        assert len(private) == 1 and private[0].alpha_mode == 'STRAIGHT', 'Missing private alpha image'
        shared_base = next(image for image in images if image.get('sora_identity') == 'base' and not image.get('sora_image_view'))
        assert shared_base.alpha_mode == 'CHANNEL_PACKED', 'Transparent view changed shared alpha interpretation'
        normal = next(image for image in images if image.get('sora_identity') == 'normal')
        assert normal.get('sora_native_rg_view') and abs(normal.pixels[2]) < 1e-6, 'Native BC5 view must retain blue=0'
        assert all(image.packed_file for image in images), 'Image not packed for reload'
    assert not close(references[0]['basis'][0], references[1]['basis'][0]), 'Two instances have identical head basis'

if PHASE == 'verify_reload':
    state = json.loads(bpy.context.scene[STATE_KEY])
    collections = [next(c for c in bpy.data.collections if c.get('sora_instance') == token) for token in state['tokens']]
    bpy.context.view_layer.update()
    # Depend on the installed load/depsgraph handlers; a manual adapter restore
    # here would hide a broken automatic reload lifecycle.
    verify_live(collections, state['references'])
    for collection in collections:
        token = collection['sora_instance']
        bridge_scene.remove_scene(bpy.context, collection)
        assert not owned(token), owned(token)
    del bpy.context.scene[STATE_KEY]
    print('NPR_LIFECYCLE_RELOAD_OK: matrices, head bases, packed private images and removal verified')
else:
    document = fixture()
    source_copy = deepcopy(document)
    collections = []
    basic_collection = None
    try:
        basic_collection, basic_rig = bridge_scene.create_scene(bpy.context, document, 'BASIC')
        native_rest = {bone.name: bone.matrix_local.copy() for bone in basic_rig.data.bones}
        basic_normal = next(i for i in bpy.data.images if i.get('sora_instance') == basic_collection['sora_instance'] and i.get('sora_identity') == 'normal')
        assert basic_normal.pixels[2] > 0.99 and not basic_normal.get('sora_native_rg_view'), 'Basic BC5 RGB-Z reconstruction changed'
        for _ in range(2):
            collection, rig = bridge_scene.create_scene(bpy.context, document, 'NPR')
            collections.append(collection)
            for name, reference in native_rest.items():
                assert close(flat(rig.matrix_world @ rig.data.bones[name].matrix_local), flat(reference)), ('Rolled native rest frame changed', name)
        assert document == source_copy, 'Adapter mutated native DTO'
        basic_token = basic_collection['sora_instance']
        bridge_scene.remove_scene(bpy.context, basic_collection)
        basic_collection = None
        assert not owned(basic_token)
        # Warmed templates must remain shared, unowned and intact after failures.
        templates = {m.as_pointer() for m in bpy.data.materials if m.get('ruri_uber_template')}
        assert templates
        assert all(not m.get('sora_instance') for m in bpy.data.materials if m.get('ruri_uber_template'))
        # Simulate an older private copy retaining template identity. A renamed
        # shared template must still resolve to itself, never to that copy.
        stack = next(s for s in ruri_adapter.stacks() if s.post is None and 'Face' in s.PART_META)
        template = stack.group(stack.OUTLINE_TEMPLATE)
        template_name = template.name
        impostor = template.copy()
        impostor.name = 'AAA Lifecycle private template identity'
        impostor['sora_instance'] = collections[0]['sora_instance']
        try:
            template.name = 'ZZZ Lifecycle shared outline template'
            assert stack.group(stack.OUTLINE_TEMPLATE) == template, 'Private clone selected as shared template'
        finally:
            bpy.data.node_groups.remove(impostor)
            template.name = template_name
        before = snapshot()
        original_finish = ruri_adapter.finish_import
        def fail_late(context, objects):
            original_finish(context, objects)
            raise RuntimeError('injected after private images and geometry stages')
        try:
            with patch.object(ruri_adapter, 'finish_import', fail_late):
                bridge_scene.create_scene(bpy.context, document, 'NPR')
        except RuntimeError as error:
            assert str(error) == 'injected after private images and geometry stages'
        else:
            raise AssertionError('Expected import rollback')
        assert snapshot() == before, 'Late rollback leaked or removed datablocks'
        # Fail after native creates a root, before modifier attachment/owner tagging.
        stack = next(s for s in ruri_adapter.stacks() if s.post is None and 'Face' in s.PART_META)
        original_clone = stack._clone_vtx
        def fail_clone(*args, **kwargs):
            original_clone(*args, **kwargs)
            raise RuntimeError('injected while attaching geometry clone')
        before = snapshot()
        try:
            with patch.object(stack, '_clone_vtx', fail_clone):
                bridge_scene.create_scene(bpy.context, document, 'NPR')
        except RuntimeError as error:
            assert str(error) == 'injected while attaching geometry clone'
        else:
            raise AssertionError('Expected partial geometry rollback')
        assert snapshot() == before, 'Partial-stage rollback leaked or removed datablocks'
        references = []
        for index, collection in enumerate(collections):
            rig, face = members(collection)
            if index == 0:
                rig.pose.bones['Bip001_Head'].rotation_mode = 'QUATERNION'
                rig.pose.bones['Bip001_Head'].rotation_quaternion = Quaternion((0, 1, 0), 0.45)
                rig.matrix_world = Matrix.Translation((2, 0, 0)) @ Matrix.Rotation(0.6, 4, 'Z') @ rig.matrix_world
            bpy.context.view_layer.update()
            ruri_adapter.update(bpy.context.scene, bpy.context.evaluated_depsgraph_get())
            pose = rig.pose.bones['Bip001_Head']
            delta = pose.matrix.to_3x3() @ pose.bone.matrix_local.to_3x3().inverted()
            basis = []
            for axis in ((1, 0, 0), (0, 0, 1), (0, 1, 0)):
                vector = (delta @ Vector(axis)).normalized()
                basis.append([vector.x, vector.z, vector.y])
            references.append(dict(world=flat(rig.matrix_world), pose=flat(pose.matrix_basis), basis=basis))
        verify_live(collections, references)
        assert {m.as_pointer() for m in bpy.data.materials if m.get('ruri_uber_template')} == templates
        if PHASE == 'prepare_reload':
            bpy.context.scene[STATE_KEY] = json.dumps(dict(tokens=[c['sora_instance'] for c in collections], references=references))
            collections = []  # Keep only explicit reload fixtures for caller's save/reopen.
            print('NPR_LIFECYCLE_RELOAD_READY: save a copy, reopen it, rerun with verify_reload')
        else:
            first_token = collections[0]['sora_instance']
            survivor_before = [(kind, name) for kind, name in owned(collections[1]['sora_instance'])]
            bridge_scene.remove_scene(bpy.context, collections.pop(0))
            assert not owned(first_token), owned(first_token)
            assert owned(collections[0]['sora_instance']) == survivor_before, 'Removing first import affected second instance'
            print('NPR_LIFECYCLE_OK: rolled bones, two instances, alpha/BC5 ownership, late/partial rollback and shared templates')
    finally:
        if basic_collection is not None:
            bridge_scene.remove_scene(bpy.context, basic_collection)
        for collection in collections:
            token = collection['sora_instance']
            bridge_scene.remove_scene(bpy.context, collection)
            assert not owned(token), owned(token)
