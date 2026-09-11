"""Explicit selected-equipment alignment to an authored body WeaponAnim event."""
import json
import math


def mapping(owner, body_rig, selected, scene_fps):
    animation = body_rig.animation_data
    action = animation.action if animation else None
    if action is None or action.get('sora_instance') != body_rig.get('sora_instance'):
        raise ValueError('请先为当前角色加载原生身体动作')
    if any(not track.mute for track in animation.nla_tracks):
        raise ValueError('身体 NLA 正在参与求值；无法确认原生事件时间')
    metadata = json.loads(action.get('sora_clip_metadata', '{}'))
    source = (metadata.get('native') or {}).get('source') or {}
    if not source.get('cab') or not source.get('pathId'):
        raise ValueError('身体动作缺少原生片段身份')
    identity = source['cab'] + ':' + source['pathId']
    assembly = json.loads(owner['sora_equipment_contract'])
    clips = ((assembly.get('animationConfig') or {}).get('clips') or [])
    matches = [clip for clip in clips if clip.get('sourceId') == identity]
    if len(matches) != 1:
        raise ValueError('身体片段不在当前角色的唯一原生控制器事件记录中')
    states = [state for state in selected.get('controllerStates') or []
              if state.get('status') == 'native-single-leaf-state'
              and state.get('controllerId') == selected['equipment']['controllerId']
              and state.get('clipId') == selected['cab'] + ':' + selected['pathId']
              and state.get('speed') == 1 and state.get('cycleOffset') == 0]
    events = [(event['sourceIndex'], event, state, transition)
              for event in matches[0].get('decodedWeaponEvents') or [] for state in states
              for transition in state.get('triggerTransitions') or []
              if event.get('functionName') == 'WeaponAnim' and event.get('paramType') == 0
              and event.get('slotId') == selected['equipment']['slotId']
              and event.get('targetStatus') == 'native-declaration-slot'
              and event.get('triggerName') == transition.get('triggerName')
              and transition.get('status') == 'native-trigger-any-state' and transition.get('offset') == 0]
    if len(events) != 1:
        raise ValueError('所选装备片段没有唯一匹配的身体 WeaponAnim 事件与原生状态')
    index, event, state, transition = events[0]
    seconds = event.get('time')
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= metadata.get('duration', -1):
        raise ValueError('身体事件时间超出原生片段')
    timing = json.loads(action.get('sora_timeline_mapping', '{}'))
    fps, origin = timing.get('actionFps', metadata.get('fps')), timing.get('frameOrigin', 1)
    if fps != scene_fps or not isinstance(origin, (int, float)) or not math.isfinite(origin):
        raise ValueError('身体动作与当前时间轴帧率不一致')
    return {'fps': fps, 'origin': origin + seconds * fps,
            'proof': {'bodyAction': action.name, 'bodyClipId': identity, 'eventIndex': index,
                      'event': event, 'controllerState': state, 'triggerTransition': transition,
                      'targetSelection': 'native-declaration-slot',
                      'scope': 'native trigger destination clip aligned to event; transition crossfade and retiming not evaluated'}}
