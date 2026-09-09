"""Blender regression for native NPC customization using a caller-supplied DTO.

Call run(document) with a real Core scene response containing an enabled
Standard customization material and its original textures. No game/audit paths
are embedded here. The test uses unlinked temporary materials, never renders or
saves, and restores the original engine and releases its owned data in finally.
"""
import uuid

import bpy

from endfield_bridge import npc_customization as npc, ruri_adapter
from endfield_bridge.materials import load_images


def _segment(material):
    return next(node for node in material.node_tree.nodes
                if node.type == 'GROUP' and node.node_tree
                and node.node_tree.get(npc.MARKER) == npc.STAMP)


def run(document):
    """Return a JSON-serializable success report; failures raise after cleanup."""
    candidates = [record for record in document['materials']
        if (record.get('npr') or {}).get('source', {}).get('shaderName') == 'HGRP/CharacterNPR'
        and float((record.get('npr') or {}).get('floats', {}).get(npc.ENABLE, 0)) > 0.5
        and not float((record.get('npr') or {}).get('floats', {}).get('_UseCharacterFur', 0))]
    if not candidates:
        raise ValueError('Scene DTO must contain an enabled Standard NPC customization material')
    scene = bpy.context.scene
    original_engine = scene.render.engine
    token = 'npc-customization-test-' + uuid.uuid4().hex
    report = {'checks': [], 'sourceMaterial': candidates[0]['name']}
    owned_images = []
    copies = []
    try:
        images = load_images(document.get('textures') or [], token, owned_images,
                             document.get('textureDescriptors'), True)
        source = ruri_adapter.build_material([candidates[0]], images, token)
        stack = next(item for item in ruri_adapter.stacks()
                     if item.PANEL_KEY == source['ruri_uber_stack'])
        for index in range(2):
            material = source.copy()
            material.name = 'NPC customization test ' + str(index)
            material['sora_instance'] = token
            material['endf_npc_customization_id'] = uuid.uuid4().hex
            copies.append(material)
            stack._param_write(material)
        first, second = copies
        assert _segment(first).node_tree != _segment(second).node_tree
        report['checks'].append('independent material groups')
        pointers = [_segment(material).node_tree.as_pointer() for material in copies]
        for material in copies:
            for _ in range(3):
                stack._param_write(material)
        assert pointers == [_segment(material).node_tree.as_pointer() for material in copies]
        report['checks'].append('repeated sync does not clone')
        row = {'name': npc.COLORS[0], 'kind': 'COLOR', 'size': 4}
        stack.panel_write(first, row, (0.125, 0.25, 0.5, 1))
        stack.panel_write(second, row, (0.75, 0.5, 0.25, 1))
        assert tuple(_segment(first).inputs[npc.COLORS[0]].default_value) == (0.125, 0.25, 0.5)
        assert tuple(_segment(second).inputs[npc.COLORS[0]].default_value) == (0.75, 0.5, 0.25)
        report['checks'].append('live colors remain independent')
        for value in (False, True):
            stack.panel_write(first, {'name': npc.ENABLE, 'kind': 'SWITCH', 'size': 1}, value)
            assert _segment(first).inputs[npc.ENABLE].default_value == float(value)
        report['checks'].append('live enable toggle')
        for engine in ('BLENDER_EEVEE', 'CYCLES'):
            scene.render.engine = engine
            for material in copies:
                ruri_adapter.rewire_material(stack, material)
            assert tuple(_segment(first).inputs[npc.COLORS[0]].default_value) == (0.125, 0.25, 0.5)
            assert tuple(_segment(second).inputs[npc.COLORS[0]].default_value) == (0.75, 0.5, 0.25)
            assert _segment(first).node_tree != _segment(second).node_tree
            for material in copies:
                tree = _segment(material).node_tree
                assert tree['sora_instance'] == token
                assert all(not node.clamp_factor for node in tree.nodes
                           if node.type == 'MIX' and node.get('endf_npc_customization_node'))
            report['checks'].append(engine + ' reassembly preserves customization')
        report['temporaryGroupCount'] = sum(group.get('sora_instance') == token
                                            for group in bpy.data.node_groups)
    finally:
        scene.render.engine = original_engine
        for material in list(bpy.data.materials):
            if material.get('sora_instance') == token and material.users == 0:
                bpy.data.materials.remove(material)
        ruri_adapter.release_instance(token)
        for image in list(bpy.data.images):
            if image.get('sora_instance') == token and image.users == 0:
                bpy.data.images.remove(image)
        report['remainingGroups'] = sum(group.get('sora_instance') == token for group in bpy.data.node_groups)
        report['remainingMaterials'] = sum(material.get('sora_instance') == token for material in bpy.data.materials)
        report['remainingImages'] = sum(image.get('sora_instance') == token for image in bpy.data.images)
    assert report['remainingGroups'] == report['remainingMaterials'] == report['remainingImages'] == 0
    report['checks'].append('unused import-owned private groups released')
    report['state'] = 'passed'
    return report
