"""Pure geometry for constructed A/T targets in an authoritative body frame."""
import math


def arm_directions(left, right, hips, head, degrees):
    def sub(a,b):return tuple(x-y for x,y in zip(a,b))
    def dot(a,b):return sum(x*y for x,y in zip(a,b))
    def unit(a):
        size=math.sqrt(dot(a,a))
        if size<1e-6:raise ValueError('Humanoid body axes are degenerate')
        return tuple(x/size for x in a)
    if degrees not in (0,45):raise ValueError('Only explicit T=0 and A=45 constructed arm targets are supported')
    up=unit(sub(head,hips));across=sub(right,left)
    projection=dot(across,up)
    across=unit(tuple(x-projection*y for x,y in zip(across,up)))
    angle=math.radians(degrees)
    return tuple(tuple(side*math.cos(angle)*x-math.sin(angle)*y for x,y in zip(across,up)) for side in (-1,1))
