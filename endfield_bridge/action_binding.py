"""Validated Action binding without redundant ActionSlot RNA assignments."""


def slot_identity(slot):
    return None if slot is None else {'identifier':slot.identifier,'handle':slot.handle}


def bind_action(owner, action, wanted=None, *, select_slot=False, bpy_module=None):
    if bpy_module is None:
        import bpy as bpy_module
    name,pointer=owner.name,owner.as_pointer()
    action_name=action.name if action is not None else None
    action_pointer=action.as_pointer() if action is not None else None

    def fresh():
        obj=bpy_module.data.objects.get(name)
        current_action=bpy_module.data.actions.get(action_name) if action_name is not None else None
        if obj is None or obj.as_pointer()!=pointer or (action_name is not None and
                (current_action is None or current_action.as_pointer()!=action_pointer)):
            raise ValueError('Animation owner or Action changed during binding')
        # Blender 5.1.2 traverses Object.pose without a null check when removing
        # a previous ActionSlot use. Do not enter that setter for a new empty rig.
        if obj.type=='ARMATURE' and obj.pose is None:
            raise ValueError('Armature pose is not initialized; evaluate the imported rig before binding animation')
        animation=obj.animation_data
        if animation is None:
            raise ValueError('AnimationData must exist before binding an Action')
        return obj,current_action,animation

    def wanted_slot(current_action):
        if wanted is None:return None
        if current_action is None:raise ValueError('Cannot select a slot without an Action')
        # The identifier survives .blend roundtrips and is the existing saved
        # pose contract; a handle, when supplied, strengthens same-session checks.
        identifier=wanted['identifier'] if isinstance(wanted,dict) else wanted
        handle=wanted.get('handle') if isinstance(wanted,dict) else None
        matches=[s for s in current_action.slots if s.identifier==identifier and (handle is None or s.handle==handle)]
        if len(matches)!=1:raise ValueError('Saved Action slot is missing or ambiguous')
        return matches[0]

    obj,current_action,animation=fresh()
    if select_slot:wanted_slot(current_action) # Validate before changing the Action.
    if animation.action!=current_action:
        animation.action=current_action
    obj,current_action,animation=fresh()
    if animation.action!=current_action:raise ValueError('Action assignment did not bind the requested Action')
    if select_slot:
        slot=wanted_slot(current_action)
        expected_identity=slot_identity(slot)
        current=animation.action_slot
        if slot_identity(current)!=expected_identity:
            # Preserve necessary multi-slot selection, but never redundantly
            # reassign Blender's already-correct automatic slot selection.
            animation.action_slot=slot
            _,_,animation=fresh()
            if slot_identity(animation.action_slot)!=expected_identity:
                raise ValueError('Action slot assignment did not bind the requested slot')
