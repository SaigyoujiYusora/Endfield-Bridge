import base64
from pathlib import Path
import tempfile

import bpy


def load_images(records, token, owned):
    result = {}
    with tempfile.TemporaryDirectory(prefix="sora-textures-") as directory:
        for index, source in enumerate(records):
            path = Path(directory) / f"texture-{index}.png"
            path.write_bytes(base64.b64decode(source["png"], validate=True))
            image = bpy.data.images.load(str(path), check_existing=False)
            owned.append(image)
            image.name = f"Sora Texture {index}"
            image["sora_instance"] = token
            image["sora_identity"] = source["name"]
            image.colorspace_settings.name = "Non-Color" if source["linear"] else "sRGB"
            image.pack()
            image.filepath = f"//sora-{token}-{index}.png"
            result[source["name"]] = image
    return result


def build_material(records, images, token):
    material = bpy.data.materials.new(records[0]["name"])
    try:
        material["sora_instance"] = token
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
        material.surface_render_method = "DITHERED"
        return material
    except Exception:
        bpy.data.materials.remove(material)
        raise
