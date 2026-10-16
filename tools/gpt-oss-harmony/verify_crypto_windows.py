"""Audit pinned crypto bytes, apply the Windows path fix, and exercise rejection."""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

PIN = '91ea4e3421d1d8069f13d4a463fcb5986d94ea60'
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = 'scripts/encryption/artifact.mjs'
VENDOR = 'src/lib/encryption/vendor'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def fingerprint(root):
    inputs = sorted([p.relative_to(root).as_posix() for p in (root/'crypto/encryption').rglob('*')
                     if p.is_file() and not any(x == 'target' or x.startswith('.') for x in p.relative_to(root/'crypto/encryption').parts)]
                    + ['scripts/encryption/build.py', SCRIPT])
    return digest('\n'.join(f'{p}\0{digest((root/p).read_bytes())}' for p in inputs).encode())

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    paths = subprocess.check_output(['git','ls-tree','-r','--name-only',PIN,'--','crypto/encryption','scripts/encryption/build.py',SCRIPT,VENDOR],cwd=ROOT,text=True).splitlines()
    original = json.loads(subprocess.check_output(['git','show',f'{PIN}:{VENDOR}/manifest.json'],cwd=ROOT))
    # Independent implementation, using pristine git blobs rather than the verifier.
    with tempfile.TemporaryDirectory(prefix='OWU original crypto ') as directory:
        pristine = Path(directory)
        for name in paths:
            blob = subprocess.check_output(['git','show',f'{PIN}:{name}'],cwd=ROOT)
            p = pristine/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(blob)
            if args.apply or name not in (SCRIPT, VENDOR+'/manifest.json'):
                assert (ROOT/name).read_bytes() == blob, f'Pinned input mismatch: {name}'
        assert fingerprint(pristine) == original['sourceFingerprint'], 'Original source fingerprint mismatch'
        actual = {p.relative_to(pristine/VENDOR).as_posix():digest(p.read_bytes()) for p in (pristine/VENDOR).rglob('*') if p.is_file() and p.name != 'manifest.json'}
        assert actual == original['checksums'], 'Original vendor checksum mismatch'
    if args.apply:
        p = ROOT/SCRIPT
        text = p.read_text(encoding='utf-8')
        text = text.replace("import { join, relative } from 'node:path';", "import { join, posix } from 'node:path';\nimport { fileURLToPath } from 'node:url';")
        text = text.replace("new URL('../../', import.meta.url).pathname", "fileURLToPath(new URL('../../', import.meta.url))")
        text = text.replace('const path = join(dir, e.name);', 'const path = posix.join(dir, e.name);')
        text = text.replace('relative(artifact, p)', 'posix.relative(artifact, p)')
        p.write_text(text,encoding='utf-8',newline='\n')
        updated = dict(original, sourceFingerprint=fingerprint(ROOT))
        (ROOT/VENDOR/'manifest.json').write_text(json.dumps(updated,indent=2)+'\n',encoding='utf-8',newline='\n')
    current = json.loads((ROOT/VENDOR/'manifest.json').read_text())
    assert {k:v for k,v in current.items() if k != 'sourceFingerprint'} == {k:v for k,v in original.items() if k != 'sourceFingerprint'}
    assert current['sourceFingerprint'] == fingerprint(ROOT)
    subprocess.run([args.node,SCRIPT],cwd=ROOT,check=True)
    with tempfile.TemporaryDirectory(prefix='OWU crypto path with spaces ') as directory:
        fixture = Path(directory)
        for name in paths:
            p=fixture/name; p.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,p)
        subprocess.run([args.node,SCRIPT],cwd=fixture,check=True)
        for name, expected in [('crypto/encryption/Cargo.toml','Encryption source changed'),(VENDOR+'/'+next(iter(current['checksums'])),'Crypto artifact checksum mismatch')]:
            p=fixture/name; data=p.read_bytes(); p.write_bytes(data+b'\nTAMPER')
            result=subprocess.run([args.node,SCRIPT],cwd=fixture,capture_output=True,text=True)
            p.write_bytes(data)
            assert result.returncode != 0 and expected in result.stderr, f'Tamper accepted: {name}'
    print('Pinned inputs, unchanged vendor metadata/checksums, space paths and tamper rejection verified')

if __name__ == '__main__':
    main()
