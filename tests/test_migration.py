import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

spec = importlib.util.spec_from_file_location('migration', Path(__file__).resolve().parents[1] / 'endfield_bridge/migration.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def scene(shared='', legacy='legacy'):
    return NS(sora=NS(game_root=shared, assets=['old'], selected=2, result_database='old', offset=30, total=50, status='old'),
              sora_animation=NS(game_root=legacy, rows=['old'], clips=['old'], selected=1, selected_clip=1, clip_root='old', clip_resource='old'))


class MigrationTests(unittest.TestCase):
    def test_valid_legacy_moves_once_and_invalidates_selections(self):
        state = scene()
        self.assertTrue(m.migrate_scene(state, lambda p: 'resolved/' + p, lambda p: True))
        self.assertEqual(state.sora.game_root, 'resolved/legacy')
        self.assertEqual(state.sora_animation.game_root, 'legacy')
        self.assertEqual(state.sora.assets, [])
        self.assertEqual(state.sora_animation.rows, [])
        self.assertEqual(state.sora_animation.clips, [])
        self.assertEqual(state.sora.selected, -1)
        self.assertEqual(state.sora_animation.clip_root, '')
        self.assertFalse(m.migrate_scene(state, lambda p: p, lambda p: True))

    def test_existing_shared_root_is_never_overwritten(self):
        state = scene(shared='new')
        self.assertFalse(m.migrate_scene(state, lambda p: p, lambda p: True))
        self.assertEqual(state.sora.game_root, 'new')
        self.assertEqual(state.sora.assets, ['old'])

    def test_empty_or_invalid_legacy_is_not_migrated(self):
        for legacy in ('', '   ', 'missing'):
            state = scene(legacy=legacy)
            self.assertFalse(m.migrate_scene(state, lambda p: p, lambda p: False))
            self.assertEqual(state.sora.game_root, '')
            self.assertEqual(state.sora_animation.rows, ['old'])

    def test_path_error_preserves_old_settings(self):
        state = scene()
        def resolve(_): raise OSError('unavailable')
        self.assertFalse(m.migrate_scene(state, resolve, lambda p: True))
        self.assertEqual(state.sora.assets, ['old'])


if __name__ == '__main__': unittest.main()
