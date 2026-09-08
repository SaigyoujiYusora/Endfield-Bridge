"""Parent-run native Animation Player lifecycle and large-clip checks."""
import json
from pathlib import Path
import time
from types import SimpleNamespace

import bpy
from endfield_bridge import animation_actions as aa, face_controls as face
from endfield_bridge.scene import create_scene, remove_scene, apply_clip

ROOT = Path('F:/Games/Endfield-unpack/ENDF-DR/audit')


def fixture():
    source = json.loads((ROOT/'FaceProbe/face-runtime-fixture.json').read_text())
    source.update(name='Native animation lifecycle fixture', materials=[], meshes=[], clips=[])
    return source


def run(large=False):
    run_sparse_switch()
    run_face_mask_switches()
    document = fixture()
    collection, rig = create_scene(bpy.context, document, 'BASIC')
    token = rig['sora_instance']
    names = [bone['name'] for bone in document['bones']]
    source_bones = document['bones']
    scene = bpy.context.scene
    timing = (scene.render.fps,scene.render.fps_base,scene.frame_start,scene.frame_end,scene.frame_current,scene.frame_subframe)
    try:
        face_index = document['faceDriver']['bones'][0]['sceneBone']
        governed = {bone['sceneBone'] for bone in document['faceDriver']['bones']}
        body = next(i for i,bone in enumerate(source_bones) if bone['parent'] >= 0 and i not in governed)
        def track(index,channel,a,b):
            return {'bone':index,'channel':channel,'keys':[{'time':0,'value':a},{'time':1,'value':b}]}
        old_clip = {'name':'Previous action','fps':2,'duration':1,'tracks':[track(body,'location',[0,0,0],[.1,0,0])]}
        old = apply_clip(bpy.context,rig,old_clip,names,source_bones)
        old_slot = rig.animation_data.action_slot
        old_signature = [(c.data_path,c.array_index,aa.curve_signature(c),c.mute) for c in face.curves(old)]
        aa.set_manual_face(bpy.context,rig,True)
        clip = {'name':'Native lifecycle action','fps':2,'duration':1,
                'tracks':[track(body,'location',[0,0,0],[.1,0,0]),
                          track(body,'rotation',[0,0,0,1],[0,0,0,-1]),
                          track(face_index,'location',[0,0,0],[.002,0,0])],
                'native':{'source':{'resourcePath':'fixture/path','pathId':'-123'},
                          'customScalars':[{'path':0,'typeId':95,'customType':0,'attribute':4294967295,'sampleRate':2,'values':[.25,.5,.75]}],
                          'diagnostics':['raw Animator property preserved']}}
        action = apply_clip(bpy.context,rig,clip,names,source_bones,True)
        assert action['sora_previous_action'] == old
        assert old_slot in old.slots.values()
        assert old_signature == [(c.data_path,c.array_index,aa.curve_signature(c),c.mute) for c in face.curves(old)]
        assert len(face.curves(action)) == 11
        assert sum(c.mute for c in face.curves(action)) == 3
        assert sum(len(c.keyframe_points) for c in face.curves(action)) == 23
        scene.frame_set(2)
        assert abs(rig[aa.scalar_property('Animator_0_95_0_4294967295')]-.5) < 1e-6
        assert abs(rig.pose.bones[names[body]].location.x-.05) < 1e-6
        quaternion = rig.pose.bones[names[body]].rotation_quaternion
        assert abs(abs(quaternion.w)-1) < 1e-6
        assert json.loads(action['sora_clip_metadata'])['native'] == clip['native']
        # Rename a source bone and the Action before toggling either order.
        rig.data.bones[names[face_index]].name += '_UserRename'
        action.name += '_UserRename'
        aa.set_manual_face(bpy.context,rig,False)
        assert not any(c.mute for c in face.curves(action))
        assert not rig.get(face.ENABLED)
        aa.set_manual_face(bpy.context,rig,True)
        assert rig.get(face.ENABLED) and sum(c.mute for c in face.curves(action)) == 3
        aa.set_manual_face(bpy.context,rig,False)
        # A rejected clip must not replace action/slot/timing or create data.
        previous = (rig.animation_data.action,rig.animation_data.action_slot,len(bpy.data.actions),scene.frame_current)
        broken = dict(clip,tracks=clip['tracks']+[clip['tracks'][0]])
        try:
            apply_clip(bpy.context,rig,broken,names,source_bones)
        except ValueError:
            pass
        else:
            raise AssertionError('Duplicate curve destination accepted')
        assert previous == (rig.animation_data.action,rig.animation_data.action_slot,len(bpy.data.actions),scene.frame_current)
        if large:
            clip = json.loads((ROOT/'AnimationIntegration/output/metadata327-authoritative/combined-clip.json').read_text())
            start = time.perf_counter()
            action = apply_clip(bpy.context,rig,clip,names,source_bones)
            elapsed = time.perf_counter()-start
            curves = face.curves(action)
            assert len(clip['tracks']) == 1023
            expected = sum(len(track['keys'])*(4 if track['channel']=='rotation' else 3) for track in clip['tracks'])
            expected += sum(len(track['values']) for track in clip['native']['customScalars'])
            assert sum(len(curve.keyframe_points) for curve in curves) == expected
            assert all(len(curve.keyframe_points)==327 for curve in curves)
            scene.frame_set(164)
            assert all(all(abs(value)<1e6 for row in bone.matrix_basis for value in row) for bone in rig.pose.bones)
            print('NATIVE_ANIMATION_LARGE_OK', {'seconds':elapsed,'curves':len(curves),'keys':expected})
        print('ANIMATION_ACTION_LIFECYCLE_OK: prior actions, bulk keys, hemisphere, scalars, both Face orders, renames, rejection rollback')
    finally:
        if rig.get(face.ENABLED):
            aa.set_manual_face(bpy.context,rig,False)
        remove_scene(bpy.context,collection)
        for action in list(bpy.data.actions):
            if action.get('sora_instance') == token:
                bpy.data.actions.remove(action,do_unlink=True)
        scene.render.fps,scene.render.fps_base,scene.frame_start,scene.frame_end = timing[:4]
        scene.frame_set(timing[4],subframe=timing[5])


def run_sparse_switch():
    context = bpy.context
    scene = context.scene
    timing = (scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end,
              scene.frame_current, scene.frame_subframe)
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    rig, actions = None, []
    try:
        bpy.ops.object.armature_add()
        rig = context.object
        rig['sora_instance'] = 'sparse_animation_regression'
        name = rig.data.bones[0].name
        bpy.ops.object.mode_set(mode='EDIT')
        extra = rig.data.edit_bones.new('UserExtra')
        extra.head, extra.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.pose.bones['UserExtra'].location.x = 7
        bone = rig.pose.bones[name]
        driver = bone.driver_add('scale', 2)
        driver.driver.expression = '2.0'
        def clip(label, channel, values):
            return {'name': label, 'duration': 1, 'fps': 60, 'tracks': [
                {'bone': 0, 'channel': channel, 'keys': [
                    {'time': i, 'value': value} for i, value in enumerate(values)]}]}
        first = aa.apply_clip(context, rig, clip('Location source', 'location', [[0, 0, 0], [9, 0, 0]]), [name])
        actions.append(first)
        scene.frame_set(61)
        assert bone.location.x == 9
        bone.scale.x = 3
        second = aa.apply_clip(context, rig, clip('Sparse rotation', 'rotation', [[0, 0, 0, 1]] * 2), [name])
        actions.append(second)
        assert tuple(bone.location) == (0, 0, 0)
        assert tuple(bone.scale) == (1, 1, 2)
        assert rig.pose.bones['UserExtra'].location.x == 7
        assert driver in rig.animation_data.drivers.values()
        assert second['sora_previous_action'] == first
        bone.rotation_quaternion = (.8, .3, .4, .5)
        rotation_driver = bone.driver_add('rotation_quaternion', 0)
        rotation_driver.driver.expression = '1.0'
        third = aa.apply_clip(context, rig, clip('Sparse with quaternion driver', 'location', [[0, 0, 0]] * 2), [name])
        actions.append(third)
        assert tuple(bone.rotation_quaternion) == (1, 0, 0, 0)
        assert rotation_driver in rig.animation_data.drivers.values()
        bone.rotation_mode = 'XYZ'
        bone.rotation_euler = (.5, .6, .7)
        euler_driver = bone.driver_add('rotation_euler', 1)
        euler_driver.driver.expression = '.25'
        fourth = aa.apply_clip(context, rig, clip('Sparse with Euler driver', 'location', [[0, 0, 0]] * 2), [name])
        actions.append(fourth)
        assert max(abs(a-b) for a,b in zip(bone.rotation_euler, (0, .25, 0))) < 1e-6
        assert euler_driver in rig.animation_data.drivers.values()
        print('SPARSE_CLIP_SWITCH_OK: defaults restored, foreign driver and extra bone preserved')
    finally:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if rig is not None:
            data = rig.data
            bpy.data.objects.remove(rig, do_unlink=True)
            bpy.data.armatures.remove(data)
        for action in reversed(actions):
            bpy.data.actions.remove(action)
        scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end = timing[:4]
        scene.frame_set(timing[4], subframe=timing[5])
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active


def run_face_mask_switches():
    """Parent MCP only: empty Face actions, mask transfer and transactional rejection."""
    context, scene = bpy.context, bpy.context.scene
    timing = (scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end,
              scene.frame_current, scene.frame_subframe)
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    document = fixture()
    collection, rig = create_scene(context, document, 'BASIC')
    token = rig['sora_instance']
    names, sources = [b['name'] for b in document['bones']], document['bones']
    governed = {bone['sceneBone'] for bone in document['faceDriver']['bones']}
    face_index = document['faceDriver']['bones'][0]['sceneBone']
    body_index = next(i for i, bone in enumerate(sources) if bone['parent'] >= 0 and i not in governed)
    def clip(name, facial=False):
        tracks = [{'bone': body_index, 'channel': 'location', 'keys': [
            {'time': 0, 'value': [0, 0, 0]}, {'time': 1, 'value': [.1, 0, 0]}]}]
        if facial:
            tracks.append({'bone': face_index, 'channel': 'location', 'keys': [
                {'time': 0, 'value': [0, 0, 0]}, {'time': 1, 'value': [.002, .004, 0]}]})
        return {'name': name, 'fps': 2, 'duration': 1, 'tracks': tracks}
    def state(action):
        return ([(c.data_path, c.array_index, aa.curve_signature(c), c.mute) for c in face.curves(action)],
                action.get('sora_face_mode'), action.get('sora_face_override'),
                action.get('sora_face_curves'), action.get('sora_face_reset_defaults'))
    def driver_state():
        return [(c.as_pointer(), c.data_path, c.array_index, c.driver.expression, c.mute)
                for c in rig.animation_data.drivers]
    def rejected(operation):
        before = (state(rig.animation_data.action), rig.animation_data.action,
                  rig.animation_data.action_slot, rig.get('sora_face_override_action'),
                  len(bpy.data.actions), driver_state(), rig.get(face.STATE), rig.get(face.ENABLED))
        before_pose = {bone.name: tuple(v for row in bone.matrix_basis for v in row) for bone in rig.pose.bones}
        try:
            operation()
        except (ValueError, RuntimeError):
            pass
        else:
            raise AssertionError('Expected rejection before shared/source mutation')
        assert before == (state(rig.animation_data.action), rig.animation_data.action,
                          rig.animation_data.action_slot, rig.get('sora_face_override_action'),
                          len(bpy.data.actions), driver_state(), rig.get(face.STATE), rig.get(face.ENABLED))
        assert max(abs(a-b) for bone in rig.pose.bones for a,b in
                   zip(before_pose[bone.name], (v for row in bone.matrix_basis for v in row))) < 1e-6
    try:
        body = aa.apply_clip(context, rig, clip('Empty Face bookkeeping'), names, sources)
        rig.pose.bones[names[face_index]].location.y = .025
        aa.set_manual_face(context, rig, True)
        assert rig.get(face.ENABLED) and body['sora_face_mode'] == 'MANUAL'
        assert body['sora_face_override'] == '[]' and rig['sora_face_override_action'] == body
        aa.set_manual_face(context, rig, False)
        assert not rig.get(face.ENABLED) and body['sora_face_mode'] == 'ANIMATION'
        assert 'sora_face_override_action' not in rig
        assert abs(rig.pose.bones[names[face_index]].location.y - .025) < 1e-6

        first = aa.apply_clip(context, rig, clip('Prior muted facial source', True), names, sources)
        facial = aa.face_curves(rig, first, json.loads(first['sora_face_curves']))
        facial[0][1].mute = True  # An original user mute must remain true after release.
        original_curves = state(first)[0]
        scene.frame_set(3)
        assert abs(rig.pose.bones[names[face_index]].location.y - .004) < 1e-6
        aa.set_manual_face(context, rig, True)
        assert [r['priorMute'] for r in json.loads(first['sora_face_override'])] == [True, False, False]
        drivers = driver_state()
        body = aa.apply_clip(context, rig, clip('Manual to body only'), names, sources, True)
        assert state(first)[0] == original_curves
        assert first['sora_face_mode'] == 'ANIMATION' and first['sora_face_override'] == '[]'
        assert body['sora_face_mode'] == 'MANUAL' and body['sora_face_curves'] == '[]'
        assert rig['sora_face_override_action'] == body and driver_state() == drivers

        # Drawing uses the real driver flag, even if old metadata claimed ANIMATION.
        from endfield_bridge import animation_panel
        rig['sora_asset'], rig['sora_database'] = 'fixture/path', 'fixture.sredb'
        context.view_layer.objects.active = rig
        class Layout:
            def __init__(self): self.labels, self.operators = [], []
            def prop(self, *args, **kwargs): pass
            def template_list(self, *args, **kwargs): pass
            def label(self, **kwargs): self.labels.append(kwargs.get('text'))
            def operator(self, name, **kwargs):
                result = SimpleNamespace()
                self.operators.append((name, kwargs, result))
                return result
        body['sora_face_mode'] = 'ANIMATION'
        layout = Layout()
        animation_panel.draw(layout, context)
        assert 'Face: manual controls' in layout.labels
        toggle = next(row for row in layout.operators if row[0] == 'sora.animation_face')
        assert toggle[1]['text'] == 'Disable manual Face controls' and toggle[2].manual is False
        body['sora_face_mode'] = 'MANUAL'

        aa.set_manual_face(context, rig, False)
        assert max(abs(value) for value in rig.pose.bones[names[face_index]].location) < 1e-6
        assert not body.get('sora_face_reset_defaults')
        aa.set_manual_face(context, rig, True)
        drivers = driver_state()

        second = aa.apply_clip(context, rig, clip('Body only to facial', True), names, sources, True)
        assert body['sora_face_mode'] == 'ANIMATION' and body['sora_face_override'] == '[]'
        assert second['sora_face_mode'] == 'MANUAL' and rig['sora_face_override_action'] == second
        second_keys = [(c.data_path, c.array_index, aa.curve_signature(c)) for c in face.curves(second)]
        current = aa.apply_clip(context, rig, clip('Facial to facial', True), names, sources, True)
        assert not any(c.mute for c in face.curves(second))
        assert second['sora_face_override'] == '[]' and second['sora_face_mode'] == 'ANIMATION'
        assert second_keys == [(c.data_path, c.array_index, aa.curve_signature(c)) for c in face.curves(second)]
        assert rig['sora_face_override_action'] == current and driver_state() == drivers

        foreign = rig.pose.bones[names[body_index]].driver_add('scale', 2)
        foreign.driver.expression = '2.0'
        context.view_layer.update()
        before_timing = (scene.render.fps, scene.render.fps_base, scene.frame_start,
                         scene.frame_end, scene.frame_current, scene.frame_subframe)
        refresh = aa._refresh_frame
        reached = []
        def fail_after_transfer(*args, **kwargs):
            reached.append(True)
            assert current['sora_face_override'] == '[]'
            assert not any(c.mute for c in face.curves(current))
            assert rig['sora_face_override_action'] == rig.animation_data.action != current
            raise RuntimeError('Injected after old mask release and new pointer transfer')
        try:
            aa._refresh_frame = fail_after_transfer
            rejected(lambda: aa.apply_clip(context, rig, clip('Must roll back'), names, sources, True))
        finally:
            aa._refresh_frame = refresh
        assert reached and foreign.driver.expression == '2.0'
        assert before_timing == (scene.render.fps, scene.render.fps_base, scene.frame_start,
                                 scene.frame_end, scene.frame_current, scene.frame_subframe)

        edited = aa.face_curves(rig, current, json.loads(current['sora_face_curves']))[0][1]
        old_value = edited.keyframe_points[0].co.y
        try:
            edited.keyframe_points[0].co.y += .001
            edited.update()
            rejected(lambda: aa.apply_clip(context, rig, clip('Edited source must reject'), names, sources, True))
        finally:
            edited.keyframe_points[0].co.y = old_value
            edited.update()

        other_data = rig.data.copy()
        other = bpy.data.objects.new('External Action user regression', other_data)
        context.scene.collection.objects.link(other)
        try:
            other.animation_data_create()
            other.animation_data.action = current
            other.animation_data.action_slot = rig.animation_data.action_slot
            rejected(lambda: aa.apply_clip(context, rig, clip('Shared source must reject'), names, sources, True))
        finally:
            bpy.data.objects.remove(other, do_unlink=True)
            bpy.data.armatures.remove(other_data)
        nla = rig.animation_data.nla_tracks.new()
        try:
            nla.mute = True
            nla.strips.new('Muted NLA still shares global mutes', 1, current)
            rejected(lambda: aa.apply_clip(context, rig, clip('NLA source must reject'), names, sources, True))
        finally:
            rig.animation_data.nla_tracks.remove(nla)
        rig.pose.bones[names[body_index]].driver_remove('scale', 2)
        aa.set_manual_face(context, rig, False)
        assert not rig.get(face.ENABLED) and 'sora_face_override_action' not in rig
        print('ANIMATION_FACE_MASK_SWITCH_OK: empty actions, priorMute, both switches, rollback, signatures, sharing and NLA')
    finally:
        if rig.get(face.ENABLED):
            face.set_enabled(rig, False)
        remove_scene(context, collection)
        for action in list(bpy.data.actions):
            if action.get('sora_instance') == token:
                bpy.data.actions.remove(action, do_unlink=True)
        scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end = timing[:4]
        scene.frame_set(timing[4], subframe=timing[5])
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
