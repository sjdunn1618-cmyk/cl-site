"""Read-only closed Mon-Fri FR aggregate; encrypted artifact only, no site mutations."""
import datetime as dt,json,os
from zoneinfo import ZoneInfo
from fr_pull import HOSTS,fetch_office,agg_renewals,saves_board
from hours_crypto import encrypt

def validate_period(start,end,now=None):
 a,b=dt.date.fromisoformat(start),dt.date.fromisoformat(end);today=(now or dt.datetime.now(ZoneInfo('America/Denver'))).date()
 if a.weekday()!=0 or b!=a+dt.timedelta(days=4) or b>=today:raise ValueError('Expected closed Mon-Fri period')
 return a.isoformat(),b.isoformat()
def aggregates(offices,roster,start,end,asof):
 notes=[n for o in offices for n in o['notes'] if start<=n['d']<=end];subs=[s for o in offices for s in o['subs']];ren=agg_renewals(subs,roster,start,end)
 return {'period':{'start':start,'end':end},'fetchedAt':asof,'saves':saves_board(notes,roster)['overall'],'renewals':[{'name':r,'count':v['n']} for r,v in ren.items()]}
def as_site(current,closed):
 # Wrap the private closed FR aggregate for the same local sealer.
 import copy
 out=copy.deepcopy(current)
 out['minimumsPeriod']=closed['period']
 out['board']['metricsMeta']['fieldroutesAt']=closed['fetchedAt']
 out['board']['saves']['overall']=closed['saves']
 out['board']['renewals']['current']=closed['renewals']
 return out

def main():
 start,end=validate_period(os.environ['PERIOD_START'],os.environ['PERIOD_END']);site=json.load(open('data.json'));roster=[a['name'] for a in site['agents']]
 offices=[fetch_office(n,start,end) for n in HOSTS];out=aggregates(offices,roster,start,end,dt.datetime.now(dt.timezone.utc).isoformat())
 ciphertext=encrypt(out,os.environ['VALIDATION_PUBLIC_KEY']);open('prior-week.enc','w').write(ciphertext);print('Closed-period read and private encryption complete; no publication')
if __name__=='__main__':main()
