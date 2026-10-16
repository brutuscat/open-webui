"""Wait for existing OWU generations without logging user data or credentials."""
import json
import sqlite3
import time
import uuid
from pathlib import Path
import jwt
import requests

db=sqlite3.connect('file:C:/ProgramData/OpenWebUI/data/webui.db?mode=ro',uri=True)
row=db.execute("SELECT value FROM config WHERE key='auth.mfa.enable'").fetchone()
assert not (row and json.loads(row[0]) is True), 'MFA requires interactive authentication'
identity, stamp=db.execute("SELECT u.id,a.session_stamp FROM user u JOIN auth a ON a.id=u.id WHERE u.role='admin'").fetchone()
db.close()
now=int(time.time())
token=jwt.encode({'id':identity,'typ':'session','auth_method':'password','auth_time':now,'session_stamp':stamp,'mfa_verified':False,'iat':now,'exp':now+600,'jti':str(uuid.uuid4())},Path('C:/ProgramData/OpenWebUI/secrets/webui-secret.key').read_text().strip(),algorithm='HS256')
deadline=time.monotonic()+180
while True:
    response=requests.get('http://127.0.0.1:3000/api/tasks',headers={'Authorization':'Bearer '+token},timeout=10)
    response.raise_for_status()
    tasks=response.json()
    if isinstance(tasks,dict) and 'tasks' in tasks:
        tasks=tasks['tasks']
    if not tasks:
        print('Active generation tasks drained')
        break
    if time.monotonic()>=deadline:
        raise RuntimeError('Active generation tasks did not drain; services remain running')
    time.sleep(2)
