#!/usr/bin/env python3
"""Hash-pinned public driving route -> calibrated dual model NV12 and real auxiliary inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import types

import av
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici.camera import warp
from openpilot.tools.lib.logreader import LogReader
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.common.transformations.model import get_warp_matrix

p=argparse.ArgumentParser()
p.add_argument('--route',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--route-id',default='98395b7c5b27882e|0000002b--2686b5a2d0/1')
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
logs=list(LogReader(str(a.route/'rlog.zst')))
bykind={k:[m for m in logs if m.which()==k] for k in ['deviceState','narrowRoadCameraState','extrinsicsCalibration','modelV2','carState','carParams','driverMonitoringStateDEPRECATED','lateralDelay']}
device=str(bykind['deviceState'][0].deviceState.deviceType)
sensor=str(bykind['narrowRoadCameraState'][0].narrowRoadCameraState.sensor)
config=DEVICE_CAMERAS[(device,sensor)]
rpy=np.array(bykind['extrinsicsCalibration'][0].extrinsicsCalibration.rpyCalib,np.float32)
matrices=[get_warp_matrix(rpy,config.narrow_road.intrinsics,False).astype(np.float32),get_warp_matrix(rpy,config.wide_road.intrinsics,True).astype(np.float32)]
count=0
for j,name in enumerate(['fcamera.hevc','ecamera.hevc']):
 with (a.output/f'camera-{j}.nv12').open('wb') as out,av.open(str(a.route/name)) as container:
  n=0
  for frame in container.decode(video=0):
   data=frame.to_ndarray(format='nv12')
   h,w=frame.height,frame.width
   buf=types.SimpleNamespace(data=data,width=w,height=h,stride=w,uv_offset=w*h)
   cam=config.narrow_road if j==0 else config.wide_road
   intrinsics=np.diag([w/cam.width,h/cam.height,1.]) @ cam.intrinsics
   matrix=get_warp_matrix(rpy,intrinsics,bool(j)).astype(np.float32)
   out.write(warp(buf,matrix).tobytes());n+=1
  assert not count or n==count
  count=n
models=bykind['modelV2'][:count]
assert len(models)==count
# Recorded model action_t fields, where supplied; otherwise derive the same system delays.
long_delay=bykind['carParams'][0].carParams.longitudinalActuatorDelay+.3+.075
latdelays=bykind['lateralDelay'];idx=0
actions=[];desire=[];speed=[];rhd=[]
for m in models:
 while idx+1<len(latdelays) and latdelays[idx+1].logMonoTime<=m.logMonoTime:idx+=1
 actions.append([latdelays[idx].lateralDelay.lateralDelay+.075,long_delay])
 # Desire state is recorded model output; feed only rising edges, exactly as S1 does.
 d=np.zeros(8,np.float32)
 state=np.array(m.modelV2.meta.desireState)
 if len(state)==8:d[int(state.argmax())]=1
 desire.append(d)
 speed.append(float(m.modelV2.velocity.x[0]))
desire=np.array(desire,np.float32);prev=np.vstack([np.zeros((1,8)),desire[:-1]])
pulses=np.where(desire-prev>.99,desire,0).astype(np.float32);pulses[:,0]=0
np.savez(a.output/'inputs.npz',desire=pulses,action_t=np.array(actions,np.float32),traffic=np.tile([1.,0.],(count,1)).astype(np.float32),speed=np.array(speed,np.float32))
result={'pass':True,'route':a.route_id,'frames':count,'device':device,'sensor':sensor,'rpy':rpy.tolist(),'source':'official public openpilot model replay route','input_provenance':'desire reconstructed from recorded model state; recorded lateral delay/system-derived action_t; LHD fixture',
 'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(a.route.iterdir()) if p.is_file()}}
(a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(a.output/'frames.csv').write_text('frameId,source\n'+''.join(f'{i},real_route\n' for i in range(count)))
(a.output/'wire.log').write_text('Offline source preparation; no network inference.\n')
(a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
print(json.dumps(result,indent=2))
