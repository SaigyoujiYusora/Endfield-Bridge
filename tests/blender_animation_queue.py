"""Live Blender checks: ordered NLA playback, fractional boundaries and all-or-nothing rollback."""
import json
from pathlib import Path
import bpy
from endfield_bridge import animation_queue as queue
from endfield_bridge import equipment_animation as manual
from endfield_bridge.scene import create_scene, remove_scene


def run():
    source = json.loads(Path('F:/Games/Endfield-unpack/ENDF-DR/audit/FaceProbe/face-runtime-fixture.json').read_text())
    source.update(name='Queue test', materials=[], meshes=[], clips=[])
    source.pop('faceDriver', None)
    owner, rig = create_scene(bpy.context, source, 'BASIC')
    initial_actions = set(bpy.data.actions)
    scene = bpy.context.scene
    old_timing = manual.timeline_state(bpy.context)
    try:
        scene.render.fps = 24
        index = next(i for i, b in enumerate(source['bones']) if b['parent'] >= 0)
        def result(name, duration, amount):
            return {'bones': source['bones'], 'clip': {'name': name, 'fps': 60, 'duration': duration,
                'tracks': [{'bone': index, 'channel': 'location', 'keys': [
                    {'time': 0, 'value': [0, 0, 0]}, {'time': duration, 'value': [amount, 0, 0]}]}]}}
        results = {'a': result('Third selected first', 0.37, 0.3),
                   'b': result('First selected second', 0.55, -0.2),
                   'c': result('Repeated clip', 0.37, 0.3)}
        entries = [{'keys': {'body': key}, 'targets': []} for key in results]
        work = queue.apply_steps(bpy.context, rig, owner, entries, results, 10, False)
        list(work)
        tracks = [t for t in rig.animation_data.nla_tracks if not t.mute]
        assert len(tracks) == 1 and len(tracks[0].strips) == 3
        strips = list(tracks[0].strips)
        assert [s.action.name for s in strips] == [r['clip']['name'] for r in results.values()]
        assert abs(strips[0].frame_start-10) < 1e-5
        assert all(abs(a.frame_end-b.frame_start) < 2e-5 for a, b in zip(strips, strips[1:]))
        assert rig.animation_data.action is None and scene.render.fps == 24
        states = []
        for s in strips:
            frame = s.frame_start + (s.frame_end-s.frame_start)*0.5
            scene.frame_set(int(frame), subframe=frame-int(frame))
            action, local = queue.playback(rig, scene.frame_current_final)
            assert action == s.action and abs(local-(1+(s.frame_end-s.frame_start)*0.5)) < 2e-5, (action, local,
                s.frame_start, s.frame_end, s.scale, s.repeat, s.influence, s.use_animated_time, s.use_animated_influence)
            states.append(tuple(rig.pose.bones[source['bones'][index]['name']].location))
        assert max(abs(a-b) for a, b in zip(states[0], states[1])) > 0.1, states
        assert max(abs(a-b) for a, b in zip(states[0], states[2])) < 1e-4, states
        before = (set(bpy.data.actions), manual.capture(rig), manual.timeline_state(bpy.context))
        bad = result('Rejected', 1, 0.4)
        bad['clip']['tracks'][0]['bone'] = 999999
        failing = [{'keys': {'body': 'a'}, 'targets': []}, {'keys': {'body': 'bad'}, 'targets': []}]
        try: list(queue.apply_steps(bpy.context, rig, owner, failing, dict(results, bad=bad), 40, False))
        except ValueError: pass
        else: raise AssertionError('Invalid second clip was accepted')
        assert set(bpy.data.actions) == before[0]
        assert not tracks[0].mute and len(rig.animation_data.nla_tracks) == 1
        assert manual.capture(rig) == before[1]
        assert manual.timeline_state(bpy.context) == before[2]
        # Cancelling after the first finished clip must also restore the previous sequence.
        work = queue.apply_steps(bpy.context, rig, owner, entries, results, 50, False)
        for progress in work:
            if progress['stage'].startswith('创建队列片段 2/'):
                work.close(); break
        assert set(bpy.data.actions) == before[0] and not tracks[0].mute
        assert manual.capture(rig) == before[1]
        # Single-clip loading replaces queue playback without deleting its editable strips.
        from endfield_bridge.scene import apply_clip
        single = result('Single after queue', 1, 0.6)
        apply_clip(bpy.context, rig, single['clip'], [b['name'] for b in source['bones']], source['bones'])
        assert tracks[0].mute and len(tracks[0].strips) == 3
        scene.frame_set(31)
        assert abs(rig.pose.bones[source['bones'][index]['name']].location.x-0.3) < 1e-4
        # UI list edits preserve user order, duplicates and an arbitrary item count.
        settings = scene.sora_animation_queue
        for i in range(7): settings.items.add().name = str(i)
        settings.selected = 6
        bpy.ops.sora.animation_queue_edit(operation='UP')
        assert [r.name for r in settings.items] == ['0','1','2','3','4','6','5']
        bpy.ops.sora.animation_queue_edit(operation='REMOVE')
        assert [r.name for r in settings.items] == ['0','1','2','3','4','5']
        bpy.ops.sora.animation_queue_edit(operation='CLEAR')
        assert not settings.items and settings.selected == -1
        print('ANIMATION_QUEUE_TESTS_OK', {'strips': 3, 'poses': states, 'rollback': True, 'cancel': True})
    finally:
        remove_scene(bpy.context, owner)
        for action in list(bpy.data.actions):
            if action not in initial_actions: bpy.data.actions.remove(action, do_unlink=True)
        scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end = old_timing[:4]
        scene.frame_set(old_timing[4], subframe=old_timing[5])


if __name__ == '__main__': run()
