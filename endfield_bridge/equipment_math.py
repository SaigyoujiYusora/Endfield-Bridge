"""Exact wire and Blender bone-head matrix conventions for equipment."""
import math

def multiply(a,b):
    return [sum(a[r*4+k]*b[k*4+c] for k in range(4)) for r in range(4) for c in range(4)]

def tail_compensated(local,length):
    if not math.isfinite(length) or length<=0:raise ValueError('Attachment bone length is invalid')
    transform=[1,0,0,0,0,1,0,-length,0,0,1,0,0,0,0,1]
    return multiply(transform,local)

def validate_wire(state,scale):
    for key in ('localMatrix','nativeMountWorldInScene','parentBoneRestMatrix'):
        value=state.get(key)
        if not isinstance(value,list) or len(value)!=16 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in value):
            raise ValueError('Equipment attachment matrix is missing or invalid: '+key)
    if not math.isfinite(scale) or scale<=0:raise ValueError('Equipment declared scale is invalid')
    expected=multiply(state['nativeMountWorldInScene'],[scale,0,0,0,0,scale,0,0,0,0,scale,0,0,0,0,1])
    actual=multiply(state['parentBoneRestMatrix'],state['localMatrix'])
    error=max(abs(a-b) for a,b in zip(actual,expected))
    if error>0.001:raise ValueError('Native equipment matrix invariant failed')
    return error
