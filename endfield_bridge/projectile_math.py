"""Deterministic fixed-point trajectory preview; no combat branch or collision simulation."""
import math


def equivalent_hand(sources, helper):
    """Unique hand weapon anchor whose native rest frame matches the helper."""
    if '/IK_Root/IK_Weapon_' not in helper.get('sourcePath', ''):
        return None
    rest = helper.get('restMatrix')
    if not isinstance(rest, list) or len(rest) != 16:
        raise ValueError('预览挂点缺少完整静止矩阵')
    candidates = [s for s in sources if s.get('sourcePath', '').rsplit('/', 1)[0].endswith('_Hand')
                  and s.get('name', '').startswith('wep_') and len(s.get('restMatrix') or []) == 16
                  and all(math.isfinite(a) and math.isfinite(b) and abs(a-b) < 1e-4
                          for a, b in zip(s['restMatrix'], rest))]
    if len(candidates) != 1:
        raise ValueError('预览弓挂点没有唯一静止姿态匹配的手部挂点')
    return candidates[0]


def select_launches(plan, duration):
    """Keep unconditional shots, or one authored no-target alternative per element."""
    choices = {}
    for row in plan.get('launches') or []:
        if (row.get('status') not in {'ready', 'ready-forward-preview'} or not 0 <= row['time'] < duration
                or not (row['branch'] == 'root' or row['projectileId'].endswith('_notarget'))):
            continue
        key = row['elementIndex']
        choices.setdefault(key, row['branch'])
    return [row for row in plan.get('launches') or []
            if row.get('status') in {'ready', 'ready-forward-preview'}
            and row['elementIndex'] in choices and row['branch'] == choices[row['elementIndex']]]


def speed_integral(curve, age):
    """Exact integral of unweighted cubic Hermite keys; preview curve time is seconds."""
    if age <= 0:
        return 0.0
    if not curve:
        return age
    total = min(age, curve[0][0])*curve[0][1]
    for left, right in zip(curve, curve[1:]):
        if age <= left[0]: break
        width = right[0]-left[0]
        if width <= 0: raise ValueError('Speed curve times are not increasing')
        u = min(1, (age-left[0])/width)
        # Integrated h00, h10, h01, h11 basis functions from 0 to u.
        total += width*((u**4/2-u**3+u)*left[1]
            +(u**4/4-2*u**3/3+u*u/2)*width*left[3]
            +(-u**4/2+u**3)*right[1]+(u**4/4-u**3/3)*width*right[2])
    if age > curve[-1][0]: total += (age-curve[-1][0])*curve[-1][1]
    if not math.isfinite(total) or total < 0: raise ValueError('Invalid integrated projectile speed')
    return total


def held_visibility(events, seconds, weapon_index):
    """Rebuild the arrow-show buff state from time, including backwards seeks.

    Create/finish are state changes, not the short action windows that issue them.
    The visibility therefore persists after the CreateBuff action's end frame.
    """
    rows = [r for r in events if r['weaponIndex'] == weapon_index and r['time'] <= seconds + 1e-8]
    if not rows:
        return None
    return max(rows, key=lambda r: (r['time'], r['elementIndex'], r['offset']))['visible']


def sample(shot, seconds):
    start, end = shot['start'], shot['end']
    speed, duration, distance = (float(shot[k]) for k in ('speed', 'duration', 'distance'))
    numbers = [*start, *end, speed, duration, distance, seconds, shot['time']]
    if not all(math.isfinite(x) for x in numbers) or min(speed, duration, distance) <= 0:
        raise ValueError('Invalid projectile trajectory')
    delta = [b-a for a, b in zip(start, end)]
    length = math.sqrt(sum(v*v for v in delta))
    if length < 1e-8:
        return False, list(start)
    age = seconds-shot['time']
    limit = min(distance, length) if shot.get('finishOnReach') else distance
    distance_at_age = speed*speed_integral(shot.get('speedCurve'), max(0, age))
    travel = min(limit, distance_at_age)
    return 0 <= age < duration and distance_at_age < limit, [a+v*travel/length for a, v in zip(start, delta)]
