"""Build the already-reviewed candidate with Windows Python and Node; no service control."""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from importlib.metadata import distributions
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN = '91ea4e3421d1d8069f13d4a463fcb5986d94ea60'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-directory', type=Path, required=True)
    parser.add_argument('--frontend-baseline', type=Path, required=True)
    parser.add_argument('--dependencies-only', action='store_true')
    args = parser.parse_args()
    env = os.environ.copy()
    env['PATH'] = str(args.node_directory.resolve()) + os.pathsep + env['PATH']
    env['NODE_OPTIONS'] = '--max-old-space-size=8192'
    node = str(args.node_directory.resolve()/'node.exe')
    npm = str(args.node_directory.resolve()/'npm.cmd')
    assert platform.system() == 'Windows' and platform.python_version() == '3.11.13'
    assert subprocess.check_output([node,'--version'],text=True).strip() == 'v22.23.3'
    assert subprocess.check_output(['git','rev-parse','v0.12.3'],cwd=ROOT,text=True).strip() == PIN
    subprocess.run(['git','merge-base','--is-ancestor',PIN,'HEAD'],cwd=ROOT,check=True)
    assert not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(), 'Commit reviewed candidate changes before building'
    # Retain the deployed public browser configuration without logging it.
    html = (args.frontend_baseline/'index.html').read_text(encoding='utf-8')
    for variable, name in [('PUBLIC_BETTER_STACK_RUM_TOKEN','better-stack-rum-token'),('PUBLIC_BETTER_STACK_SENTRY_DSN','better-stack-sentry-dsn')]:
        match = re.search(r'<meta\s+name=["\']'+name+r'["\']\s+content=["\']([^"\']*)["\']',html)
        if not match:
            raise RuntimeError(f'Missing deployed telemetry metadata: {name}')
        env[variable] = match[1]
    if args.dependencies_only:
        subprocess.run([npm,'ci','--no-audit','--no-fund'],cwd=ROOT,env=env,check=True)
        subprocess.run([npm,'run','pyodide:fetch'],cwd=ROOT,env=env,check=True)
        subprocess.run([sys.executable,'-m','build','--version'],env=env,check=True)
        return
    subprocess.run([npm,'run','crypto:verify'],cwd=ROOT,env=env,check=True)
    env['APP_BUILD_HASH'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    subprocess.run([npm,'run','build'],cwd=ROOT,env=env,check=True)
    assert (ROOT/'build/index.html').is_file(), 'Frontend build missing'
    env['HATCH_BUILD_NO_HOOKS'] = 'true'
    subprocess.run([sys.executable,'-m','build','--no-isolation','--wheel','--outdir',str(ROOT/'dist')],cwd=ROOT,env=env,check=True)
    wheels = list((ROOT/'dist').glob('open_webui-0.12.3-*.whl'))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        for path in (ROOT/'backend/open_webui/integrations/gpt_oss_harmony').glob('*.py'):
            assert wheel.read('open_webui/integrations/gpt_oss_harmony/'+path.name) == path.read_bytes(), f'Wheel lost Harmony source: {path.name}'
        assert wheel.read('open_webui/frontend/index.html') == (ROOT/'build/index.html').read_bytes()
    record = {'release':'v0.12.3','upstream':PIN,'source':env['APP_BUILD_HASH'],
              'python':platform.python_version(),'node':'22.23.3','node_heap_mib':8192,
              'build_dependencies':{d.metadata['Name']:d.version for d in distributions()},
              'npm_lock_sha256':hashlib.sha256((ROOT/'package-lock.json').read_bytes()).hexdigest(),
              'built_at':datetime.now(timezone.utc).isoformat(),
              'wheel':str(wheels[0]),'wheel_sha256':hashlib.sha256(wheels[0].read_bytes()).hexdigest()}
    (ROOT/'dist/native-build.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print('Native wheel built; identity and checksum recorded in dist/native-build.json')

if __name__ == '__main__':
    main()
