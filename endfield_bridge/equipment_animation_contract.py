"""Dedicated clip identity/rig validation, independent of Blender and game parsing."""
import math
import struct

SELECTOR_KEYS=('slotId','resourceId','animatorId','controllerId')
IDENTITY_KEYS=('ownerAssetId','characterId','declarationId','slotId','resourceId','resourcePath','animatorId','controllerId')


def supported(capabilities):
    feature=capabilities.get('equipmentAnimation') or {}
    return (feature.get('rig')=='native-equipment-source-path' and
            {'animation-clips','animation-import'} <= set(capabilities.get('methods',())))


def frame_grid(times,fps,origin):
    """Keep native sample times as distinct float32 Action keys; never resample."""
    if not math.isfinite(fps) or fps<=0 or not math.isfinite(origin):raise ValueError('Invalid equipment timeline mapping')
    try:frames=[struct.unpack('f',struct.pack('f',origin+time*fps))[0] for time in times]
    except (OverflowError,struct.error) as error:raise ValueError('Equipment timeline exceeds float32 frame precision') from error
    if any(not math.isfinite(frame) for frame in frames) or any(b<=a for a,b in zip(frames,frames[1:])):
        raise ValueError('Current timeline cannot preserve distinct native sample times; choose a suitable frame rate/start frame')
    return frames


def sources(assembly):
    resources={}
    for resource in assembly.get('resources',[]):
        identity=resource['resourceId']
        if identity in resources:raise ValueError('Dedicated resource identity is ambiguous')
        resources[identity]=resource
    rows=[]
    seen=set()
    for slot in assembly.get('slots',[]):
        if slot['slotId'] in seen:raise ValueError('Dedicated slot identity is ambiguous')
        seen.add(slot['slotId'])
        resource=resources.get(slot['resourceId'])
        if not resource:raise ValueError('Dedicated slot resource metadata is missing')
        controllers=[]
        for controller in resource.get('controllers',[]):
            pair={key:controller.get(key) for key in ('animatorId','controllerId')}
            if not all(isinstance(v,str) and v for v in pair.values()):continue
            if pair in controllers:raise ValueError('Dedicated Animator/controller identity is ambiguous')
            controllers.append(pair)
        rows.append({'slotId':slot['slotId'],'resourceId':resource['resourceId'],
                     'resourcePath':resource['resourcePath'],'controllers':controllers})
    return rows


def validate_identity(identity, expected):
    if not isinstance(identity,dict):raise ValueError('Equipment animation identity proof is missing')
    for key in IDENTITY_KEYS:
        if not expected.get(key) or identity.get(key)!=expected[key]:
            raise ValueError('Equipment animation '+key+' differs from the selected owner/slot/controller')
    if identity.get('rigKind')!='native-equipment-source-path' or not identity.get('manifestHash'):
        raise ValueError('Equipment animation native rig/manifest proof is missing')


def validate_discovery(rows, expected):
    if not isinstance(rows,list):raise ValueError('Equipment clip discovery did not return a list')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or any(not isinstance(row.get(key),str) or not row[key] for key in ('name','cab','pathId','originalSourceId')):
            raise ValueError('Discovered equipment clip fields are incomplete')
        validate_identity(row.get('equipment'),expected)
        if row.get('resourcePath')!=expected['resourcePath'] or not row.get('cab') or not row.get('pathId'):
            raise ValueError('Discovered equipment clip source identity differs')
        identity=(row['cab'],row['pathId'])
        if identity in seen:raise ValueError('Discovered equipment clip identity is duplicated')
        seen.add(identity)
        chain=row.get('controllerChain')
        if not isinstance(chain,list) or not chain or any(not isinstance(item,str) or not item for item in chain) or chain[0]!=expected['controllerId']:
            raise ValueError('Discovered equipment controller-chain proof is missing')
    return rows


def validate_import(result, expected, selected, live_bones, canonical=False):
    proof=result.get('equipment') or {}
    validate_identity(proof.get('identity'),expected)
    if proof['identity']!=selected['equipment']:
        raise ValueError('Equipment identity changed since clip discovery')
    for key, value in (('clipId',selected['cab']+':'+selected['pathId']),
                       ('originalSourceId',selected['originalSourceId']),('controllerChain',selected['controllerChain'])):
        if proof.get(key)!=value:raise ValueError('Equipment '+key+' changed since discovery')
    bones=result.get('bones')
    if not isinstance(bones,list) or not bones or len(bones)!=len(live_bones):
        raise ValueError('Equipment source bone count differs from the imported rig')
    by_index={bone['index']:bone for bone in live_bones}
    if len(by_index)!=len(live_bones) or set(by_index)!=set(range(len(bones))):
        raise ValueError('Imported equipment source bone indices are missing or duplicated')
    paths=set()
    for index,source in enumerate(bones):
        live=by_index[index]
        path=source.get('sourcePath')
        if not path or path in paths or path!=live['sourcePath'] or source.get('parent')!=live['parent']:
            raise ValueError('Equipment bone source path/index/parent differs at '+str(index))
        paths.add(path)
        if source.get('sourceHash') is not None and str(source['sourceHash'])!=str(live.get('sourceHash')):
            raise ValueError('Equipment bone source hash differs: '+path)
        rest=source.get('restMatrix')
        if not isinstance(rest,list) or len(rest)!=16 or any(type(x) not in (float,int) or not math.isfinite(x) for x in rest):
            raise ValueError('Equipment native Rest matrix is missing or invalid')
        expected_rest=[-value if canonical and offset<8 else value for offset,value in enumerate(rest)]
        if max(abs(a-b) for a,b in zip(expected_rest,live['restMatrix']))>2e-4:
            raise ValueError('Equipment Rest matrix differs: '+path+'; reimport the matching source')
    times=proof.get('times')
    clip=result.get('clip') or {}
    if not isinstance(times,list) or not times or len(times)!=proof.get('samples'):
        raise ValueError('Equipment authored sample grid is missing')
    if any(type(t) not in (float,int) or not math.isfinite(t) for t in times) or times[0]!=0 or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('Equipment authored sample grid is invalid')
    if abs(times[-1]-float(clip.get('duration',-1)))>1e-7 or clip.get('fps')!=proof.get('sampleRate'):
        raise ValueError('Equipment clip interval/sample rate differs from its proof')
    channels=set()
    for track in clip.get('tracks',[]):
        index,channel=track.get('bone'),track.get('channel')
        if type(index) is not int or index not in by_index or channel not in ('location','rotation','scale') or (index,channel) in channels:
            raise ValueError('Equipment track bone/channel is invalid or duplicated')
        channels.add((index,channel))
        keys=track.get('keys',[])
        if len(keys)!=len(times):raise ValueError('Equipment track omits authored samples')
        dimension=4 if channel=='rotation' else 3
        for key,t in zip(keys,times):
            values=key.get('value')
            if key.get('time')!=t or not isinstance(values,list) or len(values)!=dimension or any(type(v) not in (float,int) or not math.isfinite(v) for v in values):
                raise ValueError('Equipment key time/TRS value differs from the authored grid')
            if channel=='rotation' and abs(sum(v*v for v in values)-1)>.01:
                raise ValueError('Equipment xyzw quaternion is not normalized')
    if channels!={(i,c) for i in by_index for c in ('location','rotation','scale')}:
        raise ValueError('Equipment clip does not cover every native bone channel')
    for binding in proof.get('bindings',[]):
        index=binding.get('bone')
        if type(index) is not int or index not in by_index or binding.get('sourcePath')!=bones[index]['sourcePath']:
            raise ValueError('Equipment binding proof points outside the imported rig')
    return clip, bones, proof


def guarded_steps(factory, check, capture, restore):
    """Restore exact target state on cancel/error, preserving intervening user edits.
    The wrapped builder mutates its target only in its final non-yielding binding step.
    """
    saved=capture()
    work=factory()
    complete=False
    try:
        while True:
            try:check()
            except Exception:
                saved=capture() # Preserve user changes, not stale launch-time channels.
                raise
            try:value=next(work)
            except StopIteration as finished:
                complete=True
                return finished.value
            yield value
    except GeneratorExit:
        try:check()
        except Exception:saved=capture()
        raise
    finally:
        if not complete:
            try:work.close()
            finally:restore(saved)
