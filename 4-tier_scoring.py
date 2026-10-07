"""Local tier prototype. Tie/zero-hours policy must be selected before deployment."""
from hours_input import revhr

def metric_points(values, maximum, step):
    # Proposed competition ranks: 1,1,3; equal values get equal points.
    ordered=sorted(values.items(),key=lambda x:x[1],reverse=True)
    ranks={};prior=None;rank=0
    for i,(name,value) in enumerate(ordered):
        if i==0 or value!=prior:rank=i+1
        prior=value;ranks[name]=maximum-step*(rank-1)
    return ranks

def tiers(names,revenue,hours,saves,cancels,*,tie_policy=None,zero_hours_policy=None):
    if tie_policy!='revhr-only' or zero_hours_policy!='zero-bottom':
        raise ValueError('Unconfirmed scoring policy: tiers held')
    missing=[n for n in names if n not in hours]
    if missing:return {'status':'unavailable','missing':missing,'rows':[]}
    rph={n:(revenue.get(n,0)/hours[n] if hours[n]>0 else None) for n in names}
    # Explicit verified-zero-hours policy, separate from missing rows.
    rv={n:(rph[n] if rph[n] is not None else 0) for n in names}
    pct={n:(100*saves.get(n,0)/(saves.get(n,0)+cancels.get(n,0)) if saves.get(n,0)+cancels.get(n,0) else 0) for n in names}
    rp=metric_points(rv,60,3);sp=metric_points(pct,40,2)
    order=sorted(names,key=lambda n:(-(rp[n]+sp[n]),-rv[n],n.casefold()))
    ambiguous=[]
    for boundary in (1,3,6):
        if boundary<len(order):
            a,b=order[boundary-1:boundary+1]
            if (rp[a]+sp[a],rv[a])==(rp[b]+sp[b],rv[b]):ambiguous.append(boundary)
    if ambiguous:return {'status':'tie-review','boundaries':ambiguous,'rows':[]}
    # Names order display-only ties within the same band. Never decide a reward.
    rows=[]
    for i,n in enumerate(order):
        tier='Tier 1' if i<3 else 'Tier 2' if i<6 else 'Tier 3'
        rows.append({'name':n,'place':i+1,'rph':rph[n],'savePct':pct[n],'revPts':rp[n],'savePts':sp[n],'total':rp[n]+sp[n],'tier':tier,'reward':7 if i==0 else 5 if i<3 else 3 if i<6 else 0,'zeroHours':hours[n]==0})
    return {'status':'ok','rows':rows}
