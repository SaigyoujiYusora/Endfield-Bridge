"""Local fallback textures for the attributed ENDF NPR-Shader frontend."""
import hashlib
import json

import bpy


def _image(color, non_color):
    key = json.dumps([list(color), bool(non_color)])
    name = 'ENDF NPR-Shader Neutral ' + hashlib.sha256(key.encode()).hexdigest()[:16]
    image = next((i for i in bpy.data.images if i.library is None
                  and i.get('endf_npr_neutral') == key), None)
    if image is None:
        image = bpy.data.images.new(name, 1, 1, float_buffer=True, alpha=True)
        image.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
        image.alpha_mode = 'CHANNEL_PACKED'
        image.generated_color = color
        image['endf_npr_neutral'] = key
        image['ruri_placeholder'] = 1
        image.use_fake_user = True
    return image


def neutral_image(rgb, alpha, non_color):
    from .vendor.ruri_npr.ruri_endfield import _linear_to_srgb
    values = [round(float(c), 6) for c in rgb]
    if not non_color:
        values = [_linear_to_srgb(c) for c in values]
    return _image((*values, round(float(alpha), 6)), non_color)


def localize_material(material):
    # Older ENDF imports could select a linked upstream fallback by name.
    # Replace only our direct material bindings; never edit the shared source.
    if material.library or not material.get('sora_instance') or not material.node_tree:
        return
    for node in material.node_tree.nodes:
        image = getattr(node, 'image', None)
        if (image is not None and image.get('ruri_placeholder')
                and not image.get('endf_npr_neutral') and tuple(image.size) == (1, 1)
                and image.source == 'GENERATED'):
            node.image = _image(tuple(image.generated_color), image.colorspace_settings.name == 'Non-Color')
