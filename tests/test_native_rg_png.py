"""Byte-level tests for the Sora-Core PNG contract; no Blender installation needed."""
import base64
import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib


spec = importlib.util.spec_from_file_location(
    'sora_materials', Path(__file__).resolve().parents[1] / 'endfield_bridge/materials.py')
materials = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'bpy': SimpleNamespace()}):
    spec.loader.exec_module(materials)


def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))


def png(width, height, raw):
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>2I5B', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class NativeRgPngTests(unittest.TestCase):
    def test_preserves_rg_alpha_rows_and_dimensions(self):
        source = png(2, 2, bytes([0, 128, 127, 255, 0, 33, 66, 222, 17,
                                 0, 255, 0, 64, 128, 99, 200, 170, 255]))
        result = materials._native_rg_png(source)
        self.assertEqual(result[:33], source[:33])
        length = struct.unpack_from('>I', result, 33)[0]
        self.assertEqual(zlib.decompress(result[41:41 + length]),
                         bytes([0, 128, 127, 0, 0, 33, 66, 0, 17,
                                0, 255, 0, 0, 128, 99, 200, 0, 255]))
        self.assertEqual(materials._native_rg_png(result), result)

    def test_rejects_corrupt_crc(self):
        data = bytearray(png(1, 1, bytes([0, 128, 128, 255, 255])))
        data[29] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            materials._native_rg_png(data)

    def test_rejects_wrong_pixel_length_and_unexpected_filter(self):
        for raw in (b'\0' * 4, b'\0' * 6, bytes([1, 128, 128, 255, 255])):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                materials._native_rg_png(png(1, 1, raw))

    def test_rejects_excess_dimensions(self):
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            materials._native_rg_png(png(16384, 16384, b''))

    def test_preparation_stops_at_a_cancellation_boundary(self):
        payload = png(1, 1, bytes([0, 128, 127, 255, 91]))
        record = dict(name='normal', png=base64.b64encode(payload).decode(), linear=True)
        document = {'textures': [dict(record), dict(record)],
                    'textureDescriptors': [dict(id='normal', nativeFormat=27)]}
        calls = []
        def cancelled():
            calls.append(1)
            return len(calls) > 1
        prepared = materials.prepare_native_textures(document, True, None, cancelled)
        self.assertEqual(prepared, 1)
        self.assertIn('blenderPng', document['textures'][0])
        self.assertNotIn('blenderPng', document['textures'][1])

    def test_prepared_bytes_match_inline_conversion(self):
        payload = png(2, 2, bytes([0, 128, 127, 255, 0, 33, 66, 222, 17,
                                   0, 255, 0, 64, 128, 99, 200, 170, 255]))
        record = dict(name='normal', png=base64.b64encode(payload).decode(), linear=True)
        document = {'textures': [record], 'textureDescriptors': [dict(id='normal', nativeFormat=27)]}
        self.assertEqual(materials.prepare_native_textures(document, True), 1)
        self.assertEqual(record['blenderPng'], materials._native_rg_png(payload))

    def test_loader_packs_native_bytes_and_leaves_basic_unchanged(self):
        class Image(dict):
            def __init__(self, path):
                self.path = path
                self.colorspace_settings = SimpleNamespace(name='sRGB')

            def pack(self):
                self.packed = Path(self.path).read_bytes()

        def load(path, check_existing):
            self.assertFalse(check_existing)
            return Image(path)

        payload = png(1, 1, bytes([0, 128, 127, 255, 91]))
        record = dict(name='normal', png=base64.b64encode(payload).decode(), linear=True)
        fake_bpy = SimpleNamespace(data=SimpleNamespace(images=SimpleNamespace(load=load)))
        with patch.object(materials, 'bpy', fake_bpy):
            for native, format_ in ((False, 27), (True, 27), (True, 4)):
                with self.subTest(native=native, format=format_):
                    owned = []
                    image = materials.load_images([record], 'test', owned,
                        [dict(id='normal', nativeFormat=format_)], native_channels=native)['normal']
                    expected = materials._native_rg_png(payload) if native and format_ == 27 else payload
                    self.assertEqual(image.packed, expected)
                    self.assertEqual(bool(image.get('sora_native_rg_view')), native and format_ == 27)
                    self.assertEqual(image.alpha_mode, 'CHANNEL_PACKED')
                    self.assertEqual(owned, [image])
        self.assertEqual(base64.b64decode(record['png']), payload)


if __name__ == '__main__':
    unittest.main()
