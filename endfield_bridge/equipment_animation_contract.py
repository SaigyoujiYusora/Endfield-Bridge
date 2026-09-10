"""Dedicated clip identity/rig validation, independent of Blender and game parsing."""
import math
import struct
import hashlib
from pathlib import Path

PROOF_CONTRACT='native-equipment-clip-proof-v2'

SELECTOR_KEYS=('slotId','resourceId','animatorId','controllerId')
IDENTITY_KEYS=('ownerAssetId','characterId','declarationId','slotId','resourceId','resourcePath','animatorId','controllerId')


def supported(capabilities):
    if not isinstance(capabilities,dict):return False
    feature=capabilities.get('equipmentAnimation') or {}
    methods=capabilities.get('methods')
    return (capabilities.get('product')=='Sora-Core' and isinstance(feature,dict) and
            feature.get('proofContract')==PROOF_CONTRACT and
            feature.get('rig')=='native-equipment-source-path' and
            feature.get('sampling')=='non-ACL native frame grid' and
            feature.get('transport')=='animation-clips / animation-import with equipment selector' and
            feature.get('runtime')=='single clip only; no controller/events/visibility/damping' and
            isinstance(methods,list) and all(isinstance(v,str) for v in methods) and
            {'animation-clips','animation-import'} <= set(methods))


def backend_fingerprint(executable):
    """Bind the apphost and its managed/native dependencies, outside UI draw()."""
    path=Path(executable).expanduser()
    if not path.is_absolute() or not path.is_file():raise ValueError('Select an existing absolute Core executable')
    path=path.resolve()
    files={path,*path.parent.glob('*.dll')}
    for name in ('Sora-Core.deps.json','Sora-Core.runtimeconfig.json','source-snapshot.json','distribution-files.json'):
        candidate=path.parent/name
        if candidate.is_file():files.add(candidate)
    return {str(file.resolve()):hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(files)}


def catalog_manifest(inspected):
    source=inspected.get('catalogSource') if isinstance(inspected,dict) else None
    manifest=source.get('manifestHash') if isinstance(source,dict) else None
    if not isinstance(manifest,str) or not manifest:raise ValueError('Equipment clips require a current unified catalog manifest')
    return manifest


def source_clip(selected,expected):
    identity=selected['cab']+':'+selected['pathId']
    matches=[c for c in expected.get('controllerClips',[]) if c.get('sourceId')==identity]
    if len(matches)!=1:raise ValueError('Clip is absent or ambiguous in the imported controller declaration')
    source=matches[0]
    for key,source_key in (('name','name'),('originalSourceId','originalSourceId'),('controllerChain','controllerChain')):
        if selected.get(key)!=source.get(source_key):raise ValueError('Discovered clip '+key+' differs from imported source metadata')
    paths=source.get('bindingPaths')
    if not isinstance(paths,list) or any(type(v) is not int or not 0<=v<=0xffffffff for v in paths) or len(set(paths))!=len(paths):
        raise ValueError('Imported controller binding-path authority is missing or ambiguous')
    return source


def discovery_schema(selected,expected):
    if selected.get('proofContract')!=PROOF_CONTRACT:raise ValueError('Core discovery binding-proof contract is unsupported')
    source=selected.get('bindingSchemaSource')
    required={'resourcePath':expected['resourcePath'],'cab':selected['cab'],'pathId':selected['pathId'],'manifestHash':expected['manifestHash']}
    if not isinstance(source,dict) or any(source.get(k)!=v for k,v in required.items()):
        raise ValueError('Discovery binding schema source differs from selected native identity')
    rows=selected.get('bindingSchema')
    if not isinstance(rows,list) or len(rows)>16384:raise ValueError('Discovery binding schema is missing or excessive')
    for row in rows:
        if not isinstance(row,dict) or any(type(row.get(k)) is not int for k in ('pathHash','attribute','typeId','customType','isPPtrCurve')):
            raise ValueError('Discovery binding schema fields are invalid')
        if not 0<=row['pathHash']<=0xffffffff:raise ValueError('Discovery path hash is outside its wire range')
        path=row.get('sourcePath');resolution=row.get('resolution')
        if resolution=='native-path':
            if not isinstance(path,str) or path not in expected.get('nodeSourcePaths',[]):
                raise ValueError('Discovery binding path is absent from the imported native hierarchy')
        elif resolution not in ('unmapped-path-hash','ambiguous-path-hash') or path is not None:
            raise ValueError('Discovery unresolved binding proof is inconsistent')
    declared=source_clip(selected,expected)
    if {r['pathHash'] for r in rows}!=set(declared['bindingPaths']):
        raise ValueError('Discovery schema does not cover the imported controller binding paths')
    return rows


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
    for key in (*IDENTITY_KEYS,'manifestHash'):
        if not isinstance(expected.get(key),str) or not expected[key] or identity.get(key)!=expected[key]:
            raise ValueError('Equipment animation '+key+' differs from the selected owner/catalog/controller')
    if identity.get('rigKind')!='native-equipment-source-path':raise ValueError('Equipment native rig proof is missing')
    path=identity.get('animatorSourcePath')
    if not isinstance(path,str) or not path or path not in expected.get('nodeSourcePaths',[]):
        raise ValueError('Equipment Animator source path is absent from the imported native hierarchy')
    if expected.get('animatorSourcePath') is not None and path!=expected['animatorSourcePath']:
        raise ValueError('Equipment Animator source path differs from the recorded native initial-pose authority')


def validate_discovery(rows, expected):
    if not isinstance(rows,list):raise ValueError('Equipment clip discovery did not return a list')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or any(not isinstance(row.get(key),str) or not row[key] for key in ('name','cab','pathId','originalSourceId')):
            raise ValueError('Discovered equipment clip fields are incomplete')
        validate_identity(row.get('equipment'),expected)
        if row.get('resourcePath')!=expected['resourcePath'] or not row.get('cab') or not row.get('pathId'):
            raise ValueError('Discovered equipment clip source identity differs')
        source_clip(row,expected)
        discovery_schema(row,expected)
        identity=(row['cab'],row['pathId'])
        if identity in seen:raise ValueError('Discovered equipment clip identity is duplicated')
        seen.add(identity)
        chain=row.get('controllerChain')
        if not isinstance(chain,list) or not chain or any(not isinstance(item,str) or not item for item in chain) or chain[0]!=expected['controllerId']:
            raise ValueError('Discovered equipment controller-chain proof is missing')
    return rows


def validate_import(result, expected, selected, live_bones, canonical=False):
    validate_discovery([selected],expected)
    proof=result.get('equipment') or {}
    validate_identity(proof.get('identity'),expected)
    if proof['identity']!=selected['equipment']:
        raise ValueError('Equipment identity changed since clip discovery')
    for key, value in (('clipId',selected['cab']+':'+selected['pathId']),
                       ('originalSourceId',selected['originalSourceId']),('controllerChain',selected['controllerChain'])):
        if proof.get(key)!=value:raise ValueError('Equipment '+key+' changed since discovery')
    if proof.get('proofContract')!=PROOF_CONTRACT:raise ValueError('Core import binding-proof contract is unsupported')
    if proof.get('scope')!='single-native-generic-clip; no controller transitions, events, visibility or damping':
        raise ValueError('Equipment clip sampling scope is unsupported')
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
    if not isinstance(times,list) or not 2<=len(times)<=100001 or type(proof.get('samples')) is not int or len(times)!=proof['samples']:
        raise ValueError('Equipment authored sample grid is missing')
    if any(type(t) not in (float,int) or not math.isfinite(t) for t in times) or times[0]!=0 or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('Equipment authored sample grid is invalid')
    if abs(times[-1]-float(clip.get('duration',-1)))>1e-7 or clip.get('fps')!=proof.get('sampleRate'):
        raise ValueError('Equipment clip interval/sample rate differs from its proof')
    native_source=(clip.get('native') or {}).get('source')
    required_source={'resourcePath':expected['resourcePath'],'cab':selected['cab'],'pathId':selected['pathId'],'manifestHash':expected['manifestHash']}
    if not isinstance(native_source,dict) or any(native_source.get(k)!=v for k,v in required_source.items()):
        raise ValueError('Equipment clip.native.source differs from selected native identity')
    if clip.get('name')!=selected['name']:raise ValueError('Equipment clip name differs from selected source')
    rate=proof.get('sampleRate')
    if type(rate) not in (int,float) or not math.isfinite(rate) or not 1<=rate<=240 or any(abs(t-i/rate)>1e-9 for i,t in enumerate(times)):
        raise ValueError('Equipment proof times differ from the authored frame grid')
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
    schema={}
    for row in discovery_schema(selected,expected):
        pair=(row['pathHash'],row['attribute'])
        if pair in schema:raise ValueError('Discovered binding path/attribute is duplicated')
        if row['attribute'] not in (1,2,3) or row['typeId']!=4 or row['customType']!=0 or row['isPPtrCurve']!=0 or row['resolution']!='native-path':
            raise ValueError('Selected discovery schema has unsupported or unresolved bindings')
        schema[pair]=row
    bindings=proof.get('bindings')
    if not isinstance(bindings,list) or not bindings:raise ValueError('Equipment binding proof is missing')
    pairs=set();animator_path=proof['identity']['animatorSourcePath']
    for binding in bindings:
        if not isinstance(binding,dict):raise ValueError('Equipment binding row is invalid')
        index=binding.get('bone');attribute=binding.get('attribute');path_hash=binding.get('pathHash')
        if type(index) is not int or index not in by_index or binding.get('sourcePath')!=bones[index]['sourcePath']:
            raise ValueError('Equipment binding proof points outside the imported rig')
        if type(attribute) is not int or attribute not in (1,2,3) or type(path_hash) is not int or not 0<=path_hash<=0xffffffff:
            raise ValueError('Equipment binding attribute/path hash is invalid')
        pair=(path_hash,attribute)
        if pair in pairs:raise ValueError('Equipment binding path/attribute is duplicated')
        if pair not in schema or schema[pair]['sourcePath']!=binding['sourcePath']:
            raise ValueError('Equipment binding differs from the selected discovery schema')
        path=binding['sourcePath']
        if path!=animator_path and not path.startswith(animator_path+'/'):
            raise ValueError('Equipment binding is outside its Animator hierarchy')
        pairs.add(pair)
    if pairs!=set(schema):raise ValueError('Equipment proof does not cover the complete discovered binding schema')
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
