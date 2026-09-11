import base64
import json
from pathlib import Path
import struct
import tempfile
import zlib

import bpy


def _native_rg_png(data):
    """Make a durable RG view of Sora-Core's RGBA8, unfiltered PNG output.

    Edit encoded bytes before Blender loads them: colorspace changes discard
    pixel-buffer edits, and pack() can retain the original file payload.
    R, G and alpha stay byte-exact; Basic PBR keeps the original reconstructed Z.
    """
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 64 * 1024 * 1024:
        raise ValueError('Invalid native RG PNG payload')
    chunks = []
    offset = 8
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('Truncated native RG PNG chunk')
        size = struct.unpack_from('>I', data, offset)[0]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError('Truncated native RG PNG payload')
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:end - 4]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack_from('>I', data, end - 4)[0]:
            raise ValueError('Invalid native RG PNG checksum')
        chunks.append((kind, payload))
        offset = end
    if [kind for kind, _ in chunks] != [b'IHDR', b'IDAT', b'IEND'] or len(chunks[0][1]) != 13 or chunks[-1][1]:
        raise ValueError('Unsupported native RG PNG structure')
    header = chunks[0][1]
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>2I5B', header)
    if not (1 <= width <= 16384 and 1 <= height <= 16384 and width * height <= 16777216):
        raise ValueError('Native RG PNG dimensions exceed supported bounds')
    if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
        raise ValueError('Unsupported native RG PNG encoding')
    stride = 1 + width * 4
    decoder = zlib.decompressobj()
    raw = bytearray(decoder.decompress(chunks[1][1], stride * height + 1))
    if len(raw) != stride * height or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid native RG PNG pixel length')
    for row in range(height):
        start = row * stride
        if raw[start] != 0:
            raise ValueError('Unsupported native RG PNG row filter')
        raw[start + 3:start + stride:4] = b'\0' * width

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))

    return (data[:8] + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def _texture_documents(node, found):
    if isinstance(node, dict):
        if isinstance(node.get('textures'), list):
            found.append(node)
        for child in node.values():
            if isinstance(child, dict) or (isinstance(child, list) and child and isinstance(child[0], dict)):
                _texture_documents(child, found)
    elif isinstance(node, list):
        for child in node:
            if isinstance(child, dict):
                _texture_documents(child, found)
    return found


def prepare_native_textures(result, native_channels, report=None, cancelled=None):
    """Convert native RG payloads on the task worker, never on the main thread.

    The conversion is pure python/zlib and touches no Blender state, so it can
    run in the protocol reader thread while the main thread stays free to draw.
    Only nativeFormat 27 records are converted; each is annotated with the
    channel mode it was prepared for and load_images falls back to inline
    conversion whenever that mode does not match. ``cancelled`` is polled
    between textures so a cancel stops the loop at a safe boundary instead of
    abandoning a half-written payload.
    """
    if not native_channels:
        return 0
    pending = []
    for document in _texture_documents(result, []):
        descriptors = {item.get('id'): item for item in (document.get('textureDescriptors') or [])
                       if isinstance(item, dict)}
        for source in document.get('textures') or []:
            if not isinstance(source, dict) or 'blenderPng' in source or not source.get('png'):
                continue
            if descriptors.get(source.get('name'), {}).get('nativeFormat') != 27:
                continue
            pending.append(source)
    if not pending:
        return 0
    if report is not None:
        report(0, len(pending))
    prepared = 0
    for completed, source in enumerate(pending, 1):
        if cancelled is not None and cancelled():
            break
        source['blenderPng'] = _native_rg_png(base64.b64decode(source['png'], validate=True))
        source['blenderPngNativeChannels'] = True
        prepared = completed
        if report is not None:
            report(completed, len(pending))
    return prepared


def load_images(records, token, owned, descriptors=None, native_channels=False):
    metadata = {item["id"]: item for item in (descriptors or [])}
    result = {}
    with tempfile.TemporaryDirectory(prefix="sora-textures-") as directory:
        for index, source in enumerate(records):
            path = Path(directory) / f"texture-{index}.png"
            info = metadata.get(source["name"], {})
            native_rg = native_channels and info.get('nativeFormat') == 27
            prepared = source.get("blenderPng") if source.get("blenderPngNativeChannels") is native_channels else None
            if isinstance(prepared, (bytes, bytearray)):
                png = bytes(prepared)
            else:
                png = base64.b64decode(source["png"], validate=True)
                if native_rg:
                    png = _native_rg_png(png)
            path.write_bytes(png)
            image = bpy.data.images.load(str(path), check_existing=False)
            owned.append(image)
            image.name = f"Sora Texture {index}"
            image["sora_instance"] = token
            image["sora_identity"] = source["name"]
            image.colorspace_settings.name = "Non-Color" if source["linear"] else "sRGB"
            image.alpha_mode = "CHANNEL_PACKED"
            if native_rg:
                image['sora_native_rg_view'] = True
            image.pack()
            image.filepath = f"//sora-{token}-{index}.png"
            image["sora_texture_descriptor"] = json.dumps(info)
            image["sora_filter"] = info.get("filter") or ""
            image["sora_wrap"] = info.get("wrapU") or ""
            image["sora_wrap_v"] = info.get("wrapV") or ""
            result[source["name"]] = image
    return result


def build_material_steps(records, images, token, mode="BASIC"):
    """Generator form of build_material(); the NPR path delegates to the adapter."""
    if mode in {"RURI", "NPR"}:
        from .ruri_adapter import build_material_steps as ruri_material_steps
        return (yield from ruri_material_steps(records, images, token))
    yield {"stage": "Building material", "detail": records[0]["name"]}
    return _build_basic_material(records, images, token, mode)


def build_material(records, images, token, mode="BASIC"):
    """Synchronous driver for build_material_steps(); behaviour is unchanged."""
    work = build_material_steps(records, images, token, mode)
    while True:
        try:
            next(work)
        except StopIteration as finished:
            return finished.value


def _build_basic_material(records, images, token, mode):
    material = bpy.data.materials.new(records[0]["name"])
    try:
        material["sora_instance"] = token
        native = records[0].get("npr")
        if native:
            material["sora_material_descriptor"] = json.dumps(native)
        if mode == "NPR":
            material["sora_npr_diagnostics"] = "Basic PBR fallback: unknown, unsupported part, or descriptor version"
        material.diffuse_color = records[0]["baseColor"]
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        previous = tree.nodes.new("ShaderNodeBsdfTransparent").outputs[0]
        for index, source in enumerate(records):
            shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
            shader.label = source["name"]
            shader.location = (0, -index * 400)
            shader.inputs["Base Color"].default_value = source["baseColor"]
            shader.inputs["Metallic"].default_value = source["metallic"]
            shader.inputs["Roughness"].default_value = source["roughness"]
            alpha = tree.nodes.new("ShaderNodeValue").outputs[0]
            alpha.default_value = source["baseColor"][3]
            base = source.get("baseTexture")
            if base:
                texture = tree.nodes.new("ShaderNodeTexImage")
                texture.image = images[base]
                texture.location = (-600, -index * 400)
                if not source.get("grayAlpha", False):
                    tint = tree.nodes.new("ShaderNodeMixRGB")
                    tint.blend_type = "MULTIPLY"
                    tint.inputs[0].default_value = 1
                    tint.inputs[1].default_value = source["baseColor"]
                    tree.links.new(texture.outputs["Color"], tint.inputs[2])
                    tree.links.new(tint.outputs[0], shader.inputs["Base Color"])
                if source.get("grayAlpha", False):
                    separate = tree.nodes.new("ShaderNodeSeparateColor")
                    tree.links.new(texture.outputs["Color"], separate.inputs[0])
                    sampled_alpha = separate.outputs["Red"]
                else:
                    sampled_alpha = texture.outputs["Alpha"]
                if source.get("transparent", False) or source.get("alphaClip", False):
                    multiply = tree.nodes.new("ShaderNodeMath")
                    multiply.operation = "MULTIPLY"
                    tree.links.new(sampled_alpha, multiply.inputs[0])
                    multiply.inputs[1].default_value = source["baseColor"][3]
                    alpha = multiply.outputs[0]
            normal = source.get("normalTexture")
            if normal:
                texture = tree.nodes.new("ShaderNodeTexImage")
                texture.image = images[normal]
                normal_map = tree.nodes.new("ShaderNodeNormalMap")
                tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
                tree.links.new(normal_map.outputs["Normal"], shader.inputs["Normal"])
            if source.get("alphaClip", False):
                threshold = tree.nodes.new("ShaderNodeMath")
                threshold.operation = "GREATER_THAN"
                tree.links.new(alpha, threshold.inputs[0])
                threshold.inputs[1].default_value = source.get("alphaCutoff", 0.5)
                alpha = threshold.outputs[0]
            mixture = tree.nodes.new("ShaderNodeMixShader")
            tree.links.new(alpha, mixture.inputs[0])
            tree.links.new(previous, mixture.inputs[1])
            tree.links.new(shader.outputs[0], mixture.inputs[2])
            previous = mixture.outputs[0]
        tree.links.new(previous, output.inputs["Surface"])
        material.surface_render_method = "BLENDED" if any(source.get("transparent") or source.get("grayAlpha") for source in records) else "DITHERED"
        return material
    except Exception:
        bpy.data.materials.remove(material)
        raise
