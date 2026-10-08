"""Read-only, roster-only renewal reconciliation. Output is encrypted; IDs never leave memory."""
import os,json,datetime as dt
from fr_pull import FR,HOSTS,norm,roster_name,required_rows,between,RENEWAL_SRC
from hours_crypto import encrypt

def relation(value):
    if isinstance(value,list):return [str(x) for x in value]
    if value is None:return []
    return [x.strip() for x in str(value).split(',') if x.strip()]

def read_office(n,roster,a,b):
    fr=FR(n)
    emp={str(e['employeeID']):norm(f"{e.get('fname','')} {e.get('lname','')}") for e in required_rows(fr.call('employee/search',includeData=1),'employees')}
    ids=required_rows(fr.call('subscription/search',includeData=0,dateAdded=between(a,b)),'subscriptionIDs')
    subs=[s for s in fr.bulk(ids,'subscription','subscriptions') if roster_name(emp.get(str(s.get('soldBy')),''),roster)]
    matched=[s for s in subs if norm(s.get('source')).lower() in RENEWAL_SRC]
    contracts=[]
    for i in range(0,len(matched),500):
        batch=[s['subscriptionID'] for s in matched[i:i+500]]
        cids=required_rows(fr.call('contract/search',includeData=0,subscriptionIDs=batch),'contractIDs')
        contracts.extend(fr.bulk(cids,'contract','contracts'))
    mapped={str(s['subscriptionID']):[] for s in matched}
    schema=set()
    for c in contracts:
        schema.update(c.keys())
        links=relation(c.get('subscriptionIDs',c.get('subscriptionID')))
        for sid in links:
            if sid in mapped:mapped[sid].append({'dateSigned':c.get('dateSigned'),'dateAdded':c.get('dateAdded'),'state':c.get('documentState')})
    return {'office':n,'contractSchema':sorted(schema),'rows':[{'rep':roster_name(emp[str(s['soldBy'])],roster),'source':norm(s.get('source')),'sold':s.get('dateAdded'),'value':s.get('contractValue'),'created':s.get('contractAdded'),'active':s.get('active'),'contracts':mapped.get(str(s['subscriptionID']),[])} for s in subs]}

def main():
    roster=[a['name'] for a in json.load(open('data.json'))['agents']]
    out={'period':['2026-10-05','2026-10-09'],'fetchedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'offices':[read_office(n,roster,'2026-10-05','2026-10-09') for n in HOSTS]}
    open('renewal-diagnostic.enc','w').write(encrypt(out,os.environ['VALIDATION_PUBLIC_KEY']))
    print('Roster-minimized reconciliation encrypted; no publication or IDs in output')
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('Read-only diagnostic failed safely:',type(e).__name__)
        raise SystemExit(1)
