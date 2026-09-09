"""Post graph preservation: animation, drivers, outputs and edits while enabled."""
import bpy,json,tempfile
from pathlib import Path
from endfield_bridge import post,post_graph as graph

scenes=[];trees=[];actions=[]
try:
    first=bpy.data.scenes.new('post edits first');scenes.append(first)
    second=bpy.data.scenes.new('post edits second');scenes.append(second)
    user=bpy.data.node_groups.new('post edits shared user','CompositorNodeTree');trees.append(user)
    user.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
    render=user.nodes.new('CompositorNodeRLayers');render.scene=first
    exposure=user.nodes.new('CompositorNodeExposure')
    active=user.nodes.new('NodeGroupOutput')
    other=user.nodes.new('NodeGroupOutput');active.is_active_output=True
    user.links.new(render.outputs['Image'],exposure.inputs['Image'])
    user.links.new(exposure.outputs['Image'],active.inputs['Image'])
    user.links.new(render.outputs['Image'],other.inputs['Image'])
    exposure.inputs['Exposure'].default_value=.1
    exposure.inputs['Exposure'].keyframe_insert('default_value',frame=1)
    exposure.inputs['Exposure'].default_value=.4
    exposure.inputs['Exposure'].keyframe_insert('default_value',frame=9)
    action=user.animation_data.action;actions.append(action)
    value=user.nodes.new('ShaderNodeValue')
    user['driven_value']=.2
    driver=value.outputs[0].driver_add('default_value').driver
    var=driver.variables.new();var.name='v';var.type='SINGLE_PROP'
    var.targets[0].id_type='NODETREE';var.targets[0].id=user;var.targets[0].data_path='["driven_value"]'
    driver.expression='v'
    original_action=graph._action(action)
    original_signature=graph.signature(user)
    first.compositing_node_group=user;second.compositing_node_group=user
    a=post.enable(first);b=post.enable(second)
    assert a.animation_data.action!=action and b.animation_data.action not in (action,a.animation_data.action)
    assert a.animation_data.drivers[0].driver.variables[0].targets[0].id==a
    assert len([n for n in a.nodes if n.bl_idname=='NodeGroupOutput'])==2
    assert not graph.upstream_changed(a)
    first.frame_set(9)
    assert not graph.upstream_changed(a),'animation evaluation was mistaken for a user edit'
    a.nodes[exposure.name].label='User edited while enabled'
    a['driven_value']=.6
    curves=[]
    for layer in a.animation_data.action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:curves.extend(bag.fcurves)
    curves[0].keyframe_points[0].co.y=.8
    assert graph.upstream_changed(a)
    post.disable(first)
    assert first.compositing_node_group==a
    assert a.nodes[exposure.name].label=='User edited while enabled'
    assert a.get('driven_value')==.6
    assert len(a.nodes)==len(user.nodes),'adapter nodes were not stripped'
    assert a.nodes[active.name].inputs['Image'].links[0].from_node.name==exposure.name
    assert graph.signature(user)==original_signature and graph._action(action)==original_action
    trees.append(a);actions.append(a.animation_data.action)
    post.disable(second)
    assert second.compositing_node_group==user
    # Persist edits while enabled, then append the saved scene and disable.
    first.compositing_node_group=user
    current=post.enable(first)
    current.nodes[exposure.name].location.x+=17
    with tempfile.TemporaryDirectory(prefix='post-edits-') as folder:
        path=str(Path(folder)/'post-edits.blend')
        bpy.data.libraries.write(path,{first})
        with bpy.data.libraries.load(path,link=False) as (_,target):target.scenes=[first.name]
        loaded=target.scenes[0];scenes.append(loaded)
        loaded_tree=loaded.compositing_node_group;loaded_previous=loaded.get(post.PREVIOUS)
        assert graph.upstream_changed(loaded_tree)
        post.disable(loaded)
        assert loaded.compositing_node_group==loaded_tree and loaded_tree!=loaded_previous
        trees.extend([loaded_tree,loaded_previous]);actions.append(loaded_tree.animation_data.action)
    # Nested Render Layers is rejected without changing scene assignment.
    nested=bpy.data.node_groups.new('post nested source rejected','CompositorNodeTree');trees.append(nested)
    nested.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
    n=nested.nodes.new('CompositorNodeGroup');n.node_tree=user
    o=nested.nodes.new('NodeGroupOutput');nested.links.new(n.outputs['Image'],o.inputs['Image'])
    second.compositing_node_group=nested
    try:post.enable(second)
    except ValueError as exc:assert 'nested group' in str(exc)
    else:raise AssertionError('nested render source accepted')
    assert second.compositing_node_group==nested
    print(json.dumps({'result':'POST_EDIT_PRESERVATION_OK','checks':[
        'private actions','self drivers remapped','multiple outputs','animated values not false edits',
        'upstream edits retained on disable','shared original unchanged','edited save round-trip',
        'nested render sources rejected without mutation']}))
finally:
    for scene in reversed(scenes):
        if post.STATE in scene:post.disable(scene)
        active_tree=scene.compositing_node_group
        if active_tree is not None and active_tree not in trees:trees.append(active_tree)
        bpy.data.scenes.remove(scene)
    for tree in reversed(trees):
        try:
            if tree.animation_data and tree.animation_data.action not in actions:
                actions.append(tree.animation_data.action)
            owners=bpy.data.user_map(subset={tree}).get(tree,set())-{tree}
            if not owners:bpy.data.node_groups.remove(tree)
        except ReferenceError:pass
    for action in actions:
        try:
            if action.users==0:bpy.data.actions.remove(action)
        except ReferenceError:pass
