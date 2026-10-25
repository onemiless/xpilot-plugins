#!/usr/bin/env python3
"""Control-envelope refusal/recovery E2E, cases fixed in FAILURES.md before implementation."""
import argparse,json,hashlib,sys,types
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
sys.modules['openpilot.common.params']=types.SimpleNamespace(Params=type('Params',(),{}),UnknownKeyName=KeyError)
from plugins.lean_mici import profile
from openpilot.selfdrive.modeld.constants import Plan
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
raw=np.full(2066,-2.,dtype=np.float32)
raw[917:1412]=0
raw[917:1412].reshape(33,15)[:,Plan.VELOCITY]=[20,0,0]
raw[887:893]=[20,0,0,0,0,0]
raw[1907:1979]=0
raw[1907:1979].reshape(3,6,4)[:,:,0]=50
raw[2051:2054]=3
raw[2062:2064]=0
local=profile.decode(raw.copy());remote=profile.decode(raw.copy())
assert profile.admit(local,remote)
checks=[]
for name,change in [
 ('position',lambda h:h['plan'].__setitem__((0,0,1),1000)),
 ('action',lambda h:h['action'].__setitem__((0,0),100)),
 ('action_lateral_divergence',lambda h:h['action'].__setitem__((0,0),2)),
 ('action_accel_divergence',lambda h:h['action'].__setitem__((0,1),3)),
 ('acceleration',lambda h:h['plan'].__setitem__((0,0,7),100)),
 ('curvature',lambda h:h['plan'].__setitem__((0,0,14),10)),
 ('lead_distance',lambda h:h['lead'].__setitem__((0,0,0,0),-5)),
 ('lead_probability',lambda h:h['lead_prob'].__setitem__((0,0),2)),
 ('pose_physics',lambda h:h['pose'].__setitem__((0,0),1000))]:
 h={k:v.copy() for k,v in remote.items()};change(h)
 reason=profile.reject_reason(local,h)
 assert reason,name
 checks.append({'case':name,'reason':reason})
h={k:v.copy() for k,v in remote.items()};h['plan'][0,0,3]=0;h['action'][0,0]=.3
assert profile.reject_reason(local | {'plan':h['plan']},h)=='physical_action'
no_action={k:v for k,v in local.items() if k!='action'}
h={k:v.copy() for k,v in remote.items()};h['action'][0,0]=2
assert profile.reject_reason(no_action,h)=='action_curvature_divergence'
h={k:v.copy() for k,v in remote.items()};h['lead_prob'][0,0]=.49;h['lead'][0,0,0,0]=100
assert profile.reject_reason(local,h)=='lead_distance_divergence'
h={k:v.copy() for k,v in remote.items()};h['plan'][0,-1,0]+=40;h['pose'][0,0]+=1
assert profile.admit(local,h),'irrelevant far position/normal pose divergence rejected'
(a.output/'result.json').write_text(json.dumps({'pass':True,'checks':checks,'irrelevant_divergence_accepted':True},indent=2)+'\n')
(a.output/'frames.csv').write_text('case,reject_reason\n'+''.join(f'{x["case"]},{x["reason"]}\n' for x in checks))
(a.output/'wire.log').write_text('Decoded-head envelope replay; S1 structural/freshness tests run in host E2E.\n')
(a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
