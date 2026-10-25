#!/usr/bin/env python3
"""Repeat the calibration statistics from retained real-route phone and stock heads."""
import argparse,hashlib,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici.profile import control_values,DIVERGENCE
p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
local=np.load(a.input/'stock-heads.npz');remote=np.load(a.input/'phone-heads.npz')
metrics={k:[] for k in DIVERGENCE}
for i in range(len(local['plan'])):
 l=control_values({k:local[k][i] for k in local.files});r=control_values({k:remote[k][i] for k in remote.files})
 metrics['curvature'].append(float(np.max(np.abs(r['curvature']-l['curvature'])*np.maximum(np.maximum(r['speed'],l['speed'])**2,1))))
 for key in ('acceleration','lead_probability'):metrics[key].append(float(np.max(np.abs(r[key]-l[key]))))
 metrics['lead_distance'].append(abs(r['lead_distance']-l['lead_distance']) if max(r['lead_probability'][0],l['lead_probability'][0])>.5 else 0.)
rows={k:{'p99_5':float(np.percentile(v,99.5)),'p99':float(np.percentile(v,99)),'max':max(v),'bound':DIVERGENCE[k],
         'margin_over_reference':DIVERGENCE[k]/float(np.percentile(v,99 if k.startswith('lead_') else 99.5))-1} for k,v in metrics.items()}
assert all(x['margin_over_reference']>0 for x in rows.values())
result={'pass':True,'frames':len(local['plan']),'calibration_only':True,'bounds':rows,
        'units':{'curvature':'lateral-acceleration equivalent m/s^2','acceleration':'m/s^2','lead_probability':'absolute probability','lead_distance':'m'}}
(a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(a.output/'frames.csv').write_text('frameId,'+','.join(metrics)+'\n'+''.join(str(i)+','+','.join(str(metrics[k][i]) for k in metrics)+'\n' for i in range(len(local['plan']))))
(a.output/'wire.log').write_text('Derived from retained real-route wire/stock heads; no new inference.\n')
(a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
