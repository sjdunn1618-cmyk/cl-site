"""Validate private Homebase workflow inputs. Never writes hours into site data."""
import csv, io, json, math, datetime as dt
from zoneinfo import ZoneInfo

def normalized(name): return ' '.join(name.split()).casefold()

def parse_csv(text, roster):
    rows = list(csv.reader(io.StringIO(text)))
    headers = next((i for i,r in enumerate(rows) if 'Actual hours' in r and 'First name' in r and 'Last name' in r), None)
    if headers is None: raise ValueError('Homebase hours header missing')
    h = rows[headers]; lookup = {normalized(n): n for n in roster}; values = {}
    for row in rows[headers+1:]:
        if len(row) != len(h): raise ValueError('Malformed Homebase row')
        r = dict(zip(h,row)); name = normalized(r['First name']+' '+r['Last name'])
        if name not in lookup: continue
        canonical = lookup[name]
        if canonical in values: raise ValueError('Duplicate roster hours row')
        value = float(r['Actual hours'].replace(',',''))
        if not math.isfinite(value) or value < 0 or value > 168: raise ValueError('Invalid hours')
        values[canonical] = value
    if not values: raise ValueError('No roster matches')
    period = next((r[1] for r in rows[:headers] if r and r[0] == 'Payroll Period' and len(r)>1), '')
    a,b = period.split(' To ')
    return {'start':dt.datetime.strptime(a,'%m/%d/%Y').date().isoformat(), 'end':dt.datetime.strptime(b,'%m/%d/%Y').date().isoformat(), 'hours':values}

def validate(payload, roster, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None: raise ValueError('Clock timezone missing')
    if payload.get('source') != 'homebase': raise ValueError('Wrong hours source')
    fetched = dt.datetime.fromisoformat(payload['fetchedAt'])
    if fetched.tzinfo is None: raise ValueError('Snapshot timezone missing')
    age = (now-fetched).total_seconds()
    if age < -60 or age > 90*60: raise ValueError('Stale/future hours snapshot')
    today = now.astimezone(ZoneInfo('America/Denver')).date()
    mon = today-dt.timedelta(days=today.weekday()); fri=mon+dt.timedelta(days=4)
    out = {}
    for period,expected in [('day',(today,today)),('week',(mon,fri))]:
        item = payload[period]
        if (item['start'],item['end']) != tuple(d.isoformat() for d in expected): raise ValueError('Wrong hours range')
        if set(item['hours']) - set(roster): raise ValueError('Unknown roster name')
        valid={}
        for name,value in item['hours'].items():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or value>168: raise ValueError('Invalid hours')
            valid[name]=float(value)
        if not valid: raise ValueError('No roster hours')
        out[period]={'hours':valid,'missing':[n for n in roster if n not in valid], 'start':item['start'],'end':item['end']}
    return out

def revhr(revenue, hours):
    return round(revenue/hours,2) if hours is not None and hours>0 else None
