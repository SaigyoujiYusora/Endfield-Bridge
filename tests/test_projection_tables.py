"""The fast path must never bypass a real table consumer or missing table."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

path=Path(__file__).parents[1]/'endfield_bridge/projection_tables.py'
spec=importlib.util.spec_from_file_location('projection_tables_test_module',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def image(name='Table', *, users=0, fake=False, extra=False, library=None, size=(64,4), floating=True):
    return SimpleNamespace(name=name,users=users,use_fake_user=fake,use_extra_user=extra,
                           library=library,size=size,is_float=floating)


class ProjectionTableTests(unittest.TestCase):
    def run_resolver(self,images):
        original=Mock(return_value='vendor result')
        runtime=SimpleNamespace(_table_base_name=lambda n:n[:-4] if n[-4:-3]=='.' and n[-3:].isdigit() else n)
        with patch.dict(sys.modules,{'bpy':SimpleNamespace(data=SimpleNamespace(images=images))}):
            result=module.resolve(runtime,original,'Table',64,4)
        return result,original

    def test_valid_local_without_duplicate_avoids_remap(self):
        local=image(users=12)
        result,original=self.run_resolver([local,image('Unrelated',users=17)])
        self.assertIs(result,local);original.assert_not_called()

    def test_unused_and_fake_only_duplicates_avoid_remap(self):
        local=image(users=12)
        result,original=self.run_resolver([image('Table.001',users=1,fake=True),local,
                                          image('Table.002',users=0),image('Table.003',users=1,extra=True)])
        # The vendor chooses the first valid local, even if it has a suffix.
        original.assert_called_once_with('Table',64,4)
        self.assertEqual(result,'vendor result')
        result,original=self.run_resolver([local,image('Table.001',users=1,fake=True),image('Table.002')])
        self.assertIs(result,local);original.assert_not_called()

    def test_real_duplicate_consumer_requires_vendor_migration(self):
        result,original=self.run_resolver([image(users=5),image('Table.001',users=2,fake=True)])
        self.assertEqual(result,'vendor result');original.assert_called_once()

    def test_linked_consumer_preserves_vendor_diagnostics(self):
        _,original=self.run_resolver([image(),image('Table.001',users=1,library=object())])
        original.assert_called_once()

    def test_no_valid_local_preserves_creation_path(self):
        for bad in [image(size=(1,1)),image(floating=False),image(library=object())]:
            _,original=self.run_resolver([bad]);original.assert_called_once()

    def test_reinstall_keeps_single_original_resolver(self):
        original=lambda *_: None
        runtime=SimpleNamespace(_projection_table_image=original)
        module.install(runtime);module.install(runtime)
        self.assertIs(runtime._projection_table_image._endf_projection_original,original)


if __name__=='__main__':unittest.main()
