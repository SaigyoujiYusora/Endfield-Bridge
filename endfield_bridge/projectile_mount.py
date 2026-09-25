"""Reversible preview alignment between rest-equivalent helper and hand weapon mounts."""
import json
from mathutils import Matrix
import bpy
from . import equipment as eq
from .projectile_math import equivalent_hand

SAVED = 'sora_projectile_mount_restore'
ALIGNED = 'sora_projectile_hand_alignment'


def restore(owner):
    for root in bpy.data.objects:
        if root.get('sora_owner_collection') != owner or SAVED not in root:
            continue
        saved = json.loads(root[SAVED])
        # A newly applied native event or user reparenting has priority over this old preview.
        if root.parent and root.parent.name == saved['parent'] and root.parent_bone == root.get(ALIGNED):
            root.parent_type = saved['type']
            root.parent_bone = saved['bone']
            root.matrix_parent_inverse = eq.mat(saved['inverse'])
            root.matrix_basis = eq.mat(saved['basis'])
        del root[SAVED]
        if ALIGNED in root:
            del root[ALIGNED]


def apply(context, owner, rig, action, weapon_indices):
    sources = json.loads(action.get('sora_bone_sources', '[]'))
    by_path = {s.get('sourcePath'): s for s in sources}
    assembly = json.loads(owner[eq.CONTRACT])
    slots = {s['slotId']: s for s in assembly['slots']}
    changes = []
    for child in eq.owned_children(owner, 'dedicated'):
        slot = slots[child['sora_equipment_slot']]
        if slot['weaponIndex'] not in weapon_indices:
            continue
        root = next(o for o in child.objects if o.get('sora_attachment_root'))
        state = slot['fight']
        helper = by_path.get(state.get('parentSourcePath'))
        if helper is None or not helper.get('restMatrix'):
            continue
        hand = equivalent_hand(sources, helper)
        if hand is None:
            continue
        target = rig.pose.bones.get(hand['name'])
        if target is None:
            raise ValueError('预览缺少已验证的手部骨骼')
        if root.parent == rig and root.parent_type == 'BONE' and root.parent_bone == hand['name'] and ALIGNED in root:
            continue
        if SAVED not in root:
            root[SAVED] = json.dumps({'parent': root.parent.name if root.parent else '', 'type': root.parent_type,
                'bone': root.parent_bone, 'inverse': eq.flatten(root.matrix_parent_inverse),
                'basis': eq.flatten(root.matrix_basis)})
        # Preserve the source attachment frame and scale. Only substitute its native-rest-
        # equivalent hand parent; do not add a guessed world-space translation.
        local = eq.mat(hand['restMatrix']).inverted() @ eq.mat(helper['restMatrix']) @ eq.mat(state['localMatrix'])
        root.parent = rig
        root.parent_type = 'BONE'
        root.parent_bone = hand['name']
        root.matrix_parent_inverse = Matrix.Identity(4)
        root.matrix_basis = Matrix.Translation((0, -target.bone.length, 0)) @ local
        root[ALIGNED] = hand['name']
        changes.append({'slot': slot['slotId'], 'helper': helper['name'], 'hand': hand['name']})
    if changes:
        context.view_layer.update()
    return changes
