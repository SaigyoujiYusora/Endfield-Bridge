"""Pure identity validation for Core's optional initial equipment pose DTO."""
SCOPE = 'initial-pose-only; controller transitions and event playback are not evaluated'


def validate_initial_pose_identity(resource, pose):
    for field in ('resourceId', 'resourcePath'):
        if not isinstance(resource.get(field), str) or not resource[field] or pose.get(field) != resource[field]:
            raise ValueError('Initial equipment pose resource identity differs')
    if pose.get('scope') != SCOPE or pose.get('time') != 0 or pose.get('status') != 'native-controller-default-at-zero':
        raise ValueError('Invalid native initial equipment pose contract')
    for field in ('animatorId', 'controllerId', 'clipId', 'clipName', 'animatorSourcePath'):
        if not isinstance(pose.get(field), str) or not pose[field]:
            raise ValueError('Initial equipment pose is missing native provenance')
    controllers = [c for c in resource.get('controllers', [])
                   if c.get('animatorId') == pose['animatorId'] and c.get('controllerId') == pose['controllerId']]
    if len(controllers) != 1:
        raise ValueError('Initial equipment pose controller identity differs')
    clips = [c for c in controllers[0].get('clips', [])
             if c.get('sourceId') == pose['clipId'] and c.get('name') == pose['clipName']]
    if len(clips) != 1:
        raise ValueError('Initial equipment pose clip identity differs')
    nodes = resource.get('scene', {}).get('nodes', [])
    if sum(n.get('sourcePath') == pose['animatorSourcePath'] for n in nodes) != 1:
        raise ValueError('Initial equipment pose Animator hierarchy path differs')
    sources = resource['scene']['bones']
    paths = [s.get('sourcePath') for s in sources]
    if not all(isinstance(p, str) and p for p in paths) or len(set(paths)) != len(paths):
        raise ValueError('Initial equipment pose scene has ambiguous bone paths')
    rows = pose.get('bones', [])
    if (not isinstance(rows, list) or len(rows) != len(sources)
            or any(type(r.get('bone')) is not int for r in rows)
            or {r['bone'] for r in rows} != set(range(len(sources)))):
        raise ValueError('Initial equipment pose bone coverage differs from its scene')
    for row in rows:
        if row.get('sourcePath') != sources[row['bone']]['sourcePath']:
            raise ValueError('Initial equipment pose source bone identity differs')
    return sources, rows
