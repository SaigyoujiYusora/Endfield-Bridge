import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_addon', REPO / 'tools/package_addon.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class PackagingTests(unittest.TestCase):
    def test_source_complete_reproducible_installed_layout(self):
        with tempfile.TemporaryDirectory(prefix='endfield-package-') as folder:
            first = packaging.build(REPO, Path(folder) / 'first')
            second = packaging.build(REPO, Path(folder) / 'second')
            self.assertEqual(first['addon']['sha256'], second['addon']['sha256'])
            self.assertEqual(first['source']['sha256'], second['source']['sha256'])
            with zipfile.ZipFile(first['addon']['path']) as addon, zipfile.ZipFile(first['source']['path']) as source:
                names = addon.namelist()
                self.assertTrue(all(name.startswith('endfield_bridge/') for name in names))
                self.assertFalse(any(name.lower().endswith(('.pyc', '.dll', '.exe', '.sredb')) for name in names))
                manifest = json.loads(addon.read('endfield_bridge/DISTRIBUTION.json'))
                self.assertEqual(manifest['sourceArchiveSha256'], first['source']['sha256'])
                for name, expected in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(addon.read('endfield_bridge/' + name)).hexdigest(), expected)
                for name in ('LICENSE', 'endfield_bridge/vendor/ruri_npr/LICENSE.txt'):
                    installed = 'endfield_bridge/LICENSE' if name == 'LICENSE' else name
                    self.assertEqual(addon.read(installed), (REPO / name).read_bytes())
                self.assertEqual([name for name in names if name.startswith('endfield_bridge/docs/')],
                                 [])
                self.assertEqual([name for name in source.namelist() if name.startswith('docs/')],
                                 [])
                for archive in (addon, source):
                    self.assertFalse(any(name.startswith(('.local/', '.idea/')) for name in archive.namelist()))
                    self.assertFalse(any(name.endswith(('.patch', 'NPR.md', 'RURINPR-NOTICE.txt',
                                                       'RURINPR-PROVENANCE.json')) for name in archive.namelist()))
                for name in source.namelist():
                    if name.startswith('endfield_bridge/'):
                        self.assertEqual(source.read(name), addon.read(name))
                self.assertEqual(hashlib.sha256(source.read('tools/package_addon.py')).hexdigest(), manifest['packagerSha256'])

    def test_optimized_python_keeps_integrity_checks(self):
        with tempfile.TemporaryDirectory(prefix='endfield-package-check-') as folder:
            code = '''import importlib.util,sys
s=importlib.util.spec_from_file_location('p',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
original_output=m.subprocess.check_output
def missing_license(*args,**kwargs):
 result=original_output(*args,**kwargs)
 if '--format=zip' in args[0]:
  output=m.io.BytesIO()
  with m.zipfile.ZipFile(m.io.BytesIO(result)) as source,m.zipfile.ZipFile(output,'w') as target:
   for item in source.infolist():
    data=b'' if item.filename=='endfield_bridge/vendor/ruri_npr/LICENSE.txt' else source.read(item)
    target.writestr(item,data)
  return output.getvalue()
 return result
m.subprocess.check_output=missing_license
try:m.build(sys.argv[2],sys.argv[3])
except ValueError as error:
 if 'Missing or empty license' not in str(error):raise
else:raise RuntimeError('Optimized Python skipped package integrity validation')
'''
            result = subprocess.run([sys.executable, '-O', '-c', code,
                str(REPO / 'tools/package_addon.py'), str(REPO), folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
