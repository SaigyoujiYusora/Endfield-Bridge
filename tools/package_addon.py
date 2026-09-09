"""Package addon sources and original licenses."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def build(repo, destination):
    repo, destination = Path(repo).resolve(), Path(destination).resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    subprocess.run(['git', '-C', str(repo), 'diff', '--exit-code', 'HEAD', '--',
                    'endfield_bridge', 'LICENSE', 'tools/package_addon.py',
                    '.gitignore', '.gitattributes'], check=True, stdout=subprocess.PIPE)
    commit = git('rev-parse', 'HEAD').decode().strip()
    with zipfile.ZipFile(io.BytesIO(git('archive', '--format=zip', commit))) as source:
        records = {item.filename: source.read(item) for item in source.infolist() if not item.is_dir()}
        date = source.infolist()[0].date_time
    code = ast.parse(records['endfield_bridge/__init__.py'].decode('utf8'))
    info = next(ast.literal_eval(n.value) for n in code.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in n.targets))
    version = '.'.join(map(str, info['version']))
    stem = f'Endfield-Bridge-{version}-{commit[:8]}'
    runtime_name, source_name = stem + '.zip', stem + '-source.zip'
    runtime = {name: data for name, data in records.items() if name.startswith('endfield_bridge/')}
    require(runtime and all(not name.lower().endswith(('.pyc', '.dll', '.exe', '.sredb'))
                            and '__pycache__' not in name for name in runtime), 'Unexpected binary, game data or cache in addon sources')
    for name in ('LICENSE', 'endfield_bridge/vendor/ruri_npr/LICENSE.txt'):
        require(records.get(name, b'').strip(), 'Missing or empty license: ' + name)
    for name in ('LICENSE',):
        runtime['endfield_bridge/' + name] = records[name]
    packager = Path(__file__).read_bytes()
    records['tools/package_addon.py'] = packager
    metadata = {'product': 'Endfield-Bridge', 'version': version, 'runtimeSourceCommit': commit,
                'sourceArchive': source_name, 'packagerSha256': digest(packager),
                'license': 'AGPL-3.0', 'coreBundled': False,
                'layout': 'Single endfield_bridge/ addon root with LICENSE and vendor/ruri_npr/LICENSE.txt.',
                'files': {name.removeprefix('endfield_bridge/'): digest(data) for name, data in sorted(runtime.items())}}
    destination.mkdir(parents=True, exist_ok=True)
    def write(name, files):
        path = destination / name
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, data in sorted(files.items()):
                require(not relative.startswith('/') and '..' not in Path(relative).parts, 'Unsafe archive path')
                item = zipfile.ZipInfo(relative, date)
                item.create_system = 3
                item.external_attr = 0o100644 << 16
                item.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(item, data)
        return {'path': str(path), 'sha256': digest(path.read_bytes()), 'files': len(files)}
    source_info = write(source_name, records)
    metadata['sourceArchiveSha256'] = source_info['sha256']
    runtime['endfield_bridge/DISTRIBUTION.json'] = (json.dumps(metadata, indent=2) + '\n').encode()
    return {'addon': write(runtime_name, runtime), 'source': source_info,
            'runtimeSourceCommit': commit}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.output), indent=2))
