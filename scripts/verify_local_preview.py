"""Verify local password login and read-only UI APIs; never print secrets/titles."""
from pathlib import Path
import json
import time
import httpx

root=Path(__file__).resolve().parents[1]
lines=(root/'.venv/LOCAL-LOGIN.txt').read_text().splitlines()
passwords=dict(line.split(': ',1) for line in lines if ': ' in line)
for username in ('provider-test','preview'):
    with httpx.Client(base_url='http://127.0.0.1:7340',timeout=30) as client:
        response=client.post('/login',data={'username':username,'password':passwords[username],'action':'login','next':'/home'})
        assert response.status_code in (302,303),f'Login failed: {response.status_code}'
        assert client.cookies.get('token'),'Login did not set session cookie'
        state={'cookies':[{'name':'token','value':client.cookies.get('token'),'domain':'127.0.0.1','path':'/',
            'expires':int(time.time())+1800,'httpOnly':True,'secure':False,'sameSite':'Lax'}],'origins':[]}
        (root/f'.venv/{username}-browser.json').write_text(json.dumps(state))
        for path in ('/home',f'/user/{username}/movies',f'/user/{username}/series','/recent-events',f'/user/{username}/library','/browse'):
            page=client.get(path,follow_redirects=True)
            assert page.status_code==200,f'{path}: {page.status_code}'
        for kind in ('movie','series'):
            response=client.get(f'/api/proxy/tracking/profile/{username}/{kind}')
            response.raise_for_status()
            result=response.json()
            assert result['owner']
            print(username,kind,'entries:',len(result['entries']))
        connections=client.get('/api/proxy/auth/connections',headers={'Authorization':f'Bearer {client.cookies.get("token")}'})
        connections.raise_for_status()
        assert all(
            not connection.get('token')
            for connection in connections.json()
            if connection.get('type') in ('stremio','nuvio','arvio')
        ),'Cloud connection secret leaked through the response model'
        print(username,'login and local page checks passed')
