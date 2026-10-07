"""Ciphertext-only public sidecar validator. Never handles PINs or keys."""
import base64,datetime as dt,json,sys

def validate(obj,site):
 if set(obj)!= {'v','updatedAt','sourceFieldroutesAt','agents'} or obj['v']!=1:raise ValueError('Invalid overlay schema')
 for k in ('updatedAt','sourceFieldroutesAt'):
  d=dt.datetime.fromisoformat(obj[k].replace('Z','+00:00'))
  if d.tzinfo is None:raise ValueError('Timezone missing')
 if obj['sourceFieldroutesAt']!=site['board']['metricsMeta']['fieldroutesAt']:raise ValueError('FR snapshot mismatch')
 ids={a['id'] for a in site['agents']}
 if set(obj['agents'])!=ids:raise ValueError('Roster mismatch')
 for a in obj['agents'].values():
  if set(a)-{'lock','mlock'} or 'lock' not in a:raise ValueError('Plaintext/unknown field')
  for box in a.values():
   if set(box)!= {'iv','ct'}:raise ValueError('Invalid encrypted box')
   if len(base64.b64decode(box['iv'],validate=True))!=12 or len(base64.b64decode(box['ct'],validate=True))<17:raise ValueError('Invalid ciphertext')
 return obj
if __name__=='__main__':
 import os
 data=json.loads(os.environ['MINIMUMS_CIPHERTEXT']);validate(data,json.load(open('data.json')))
 with open('minimums.json','w') as f:json.dump(data,f,separators=(',',':'))
 print('Encrypted minimums schema/source validation passed')
