"""Take SQLite online backups and verify stable surrounding data assets."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

ROOT=Path('C:/ProgramData/OpenWebUI')
DEST=ROOT/'backups/upgrade-0.12.3-harmony-20261016/data-canary'

def inventory(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.suffix not in ('.db','.sqlite3')
            and not p.name.endswith(('-wal','-shm'))}

def main():
    assert not DEST.exists(), 'Refuse to replace a prior snapshot'
    source=ROOT/'data'
    before=inventory(source)
    shutil.copytree(source,DEST,ignore=shutil.ignore_patterns('*.db','*.sqlite3','*-wal','*-shm'))
    for path in source.rglob('*'):
        if not path.is_file() or path.suffix not in ('.db','.sqlite3'):
            continue
        target=DEST/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(target) as snapshot:
            original.backup(snapshot)
            assert snapshot.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert before==inventory(source)==inventory(DEST), 'Data assets changed during snapshot; candidate is unusable'
    with sqlite3.connect(DEST/'webui.db') as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts={name:db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        auth_hash=hashlib.sha256(json.dumps(db.execute('SELECT * FROM auth ORDER BY id').fetchall(),default=str).encode()).hexdigest()
        assert not db.execute("SELECT 1 FROM config WHERE key='openai.api_keys'").fetchone()
        revision=db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    result={'counts':counts,'assets':before,'auth_hash':auth_hash,'migration_revision':revision}
    (DEST.parent/'deployment/baseline.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Consistent SQLite copy and stable asset inventory recorded')

if __name__=='__main__':
    main()
