#!/usr/bin/env python3
"""Real v4 phone replay; archived camera bytes, exact stock model and raw wire evidence.
See FAILURES.md for acceptance/failure conditions fixed before implementation.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import types

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici import wire
from plugins.lean_mici.camera import Encoder
from plugins.lean_mici.worker import now as nanos
from plugins.lean_mici.network import Beacon, discover

p=argparse.ArgumentParser()
p.add_argument('--address',required=True)
p.add_argument('--input',type=Path,required=True)
p.add_argument('--stock',type=Path,required=True)
p.add_argument('--system',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--frames',type=int,default=600)
p.add_argument('--start',type=int,default=0)
p.add_argument('--replay',type=Path,help='repeat stock/host selection from retained physical wire without contacting phone')
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
result={'pass':False,'frames':0,'scope':'Mac real-driving route replay over LAN; real phone inference and stock comparison'}
rows=[]
try:
 if a.replay:
  original=json.loads((a.replay/'result.json').read_text())
  result.update(original)
  result['scope']='Retained real-phone wire decoded again for stock and unchanged S1 selection replay'
  saved=np.load(a.replay/'phone-outputs.npz')
  phone=list(saved['outputs']);desires=saved['desire'];actions=saved['action_t']
  traffic=np.load(a.input/'inputs.npz')['traffic'][a.start:a.start+len(phone)]
  with (a.replay/'frames.csv').open() as f: rows=list(csv.DictReader(f))
  for row in rows:
   for key in ('frameId','EOF','arrival','flags'):row[key]=int(row[key])
   row['latency_ms']=float(row['latency_ms'])
  payload=(a.replay/'frames.wire').read_bytes();position=0;decoded=[]
  decoders=[av.CodecContext.create('hevc','r'),av.CodecContext.create('hevc','r')]
  while position<len(payload):
   size=struct.unpack_from('<I',payload,position)[0];position+=4
   data=payload[position:position+size];position+=size
   road_size=wire.HEADER.unpack_from(data)[-1]
   wide_size=struct.unpack_from('<I',data,152+road_size)[0]
   pair=[]
   for j,au in enumerate((data[152:152+road_size],data[156+road_size:156+road_size+wide_size])):
    frames=decoders[j].decode(av.Packet(au));assert len(frames)==1
    pair.append(frames[0].to_ndarray(format='nv12'))
   decoded.append(pair)
  assert len(decoded)==len(phone)==len(rows)
  for name in ('frames.wire','replies.wire','hello.wire'):
   (a.output/name).write_bytes((a.replay/name).read_bytes())
 else:
  images=[np.memmap(a.input/f'camera-{i}.nv12',dtype=np.uint8,mode='r').reshape(-1,384,512) for i in range(2)]
  assert all(a.start+a.frames<=len(x) for x in images)
  encoders=[Encoder(),Encoder(wide=True)]
  decoders=[av.CodecContext.create('hevc','r'),av.CodecContext.create('hevc','r')]
  encoded=[];decoded=[];metas=[]
  for i in range(a.frames):
   pair=[];decoded_pair=[];flags=0
   for stream in range(2):
    data,key=encoders[stream].encode(images[stream][a.start+i],reset=i==0)
    flags|=int(key)<<stream;pair.append(data)
    frames=decoders[stream].decode(av.Packet(data))
    assert len(frames)==1
    decoded_pair.append(frames[0].to_ndarray(format='nv12'))
   encoded.append((flags,*pair));decoded.append(decoded_pair)
  recorded=np.load(a.input/'inputs.npz')
  desires=recorded['desire'][a.start:a.start+a.frames]
  actions=recorded['action_t'][a.start:a.start+a.frames]
  traffic=recorded['traffic'][a.start:a.start+a.frames]
  identity=np.eye(3,dtype=np.float32).ravel()
  phone=[]
  with Beacon(a.address) as beacon:
   deadline=time.monotonic()+30
   peers=[]
   while time.monotonic()<deadline:
    peers=discover('en0' if sys.platform=='darwin' else 'wlan0')
    if len(peers)==1: break
    time.sleep(.5)
   assert len(peers)==1,'no unique phone server'
   with socket.create_connection(peers[0][1:],timeout=2) as conn:
    conn.settimeout(2)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    hello=wire.receive(conn,48);wire.hello(hello)
    (a.output/'hello.wire').write_bytes(hello)
    start=time.monotonic()
    with (a.output/'frames.wire').open('wb') as fw,(a.output/'replies.wire').open('wb') as rw:
     for i,(flags,road,wide) in enumerate(encoded):
      time.sleep(max(0,start+i*.05-time.monotonic()))
      eof=nanos()
      meta=(b'XMS1',1,1,i,i,eof,eof,*identity,*identity,*desires[i],*traffic[i],*actions[i])
      first,second=wire.frame(meta,road,wide,flags,i)
      fw.write(struct.pack('<I',len(first)+len(second))+first+second)
      conn.sendall(first);conn.sendall(second)
      reply=wire.receive_reply(conn);arrival=nanos()
      rw.write(reply)
      if len(reply)==40:
       raise ValueError(f'phone ERR {struct.unpack_from("<II",reply,16)}')
      fid,echo,rflags=wire.reply(reply)
      assert (fid,echo)==(i,eof)
      if rflags&1 and flags!=3:raise ValueError('phone sequence reset requires fresh dual IDR')
      phone.append(np.frombuffer(reply,'<f4',2066,24).copy())
      telemetry=struct.unpack_from('<4I',reply,8288)
      rows.append({'frameId':fid,'EOF':eof,'arrival':arrival,'latency_ms':(arrival-eof)/1e6,'flags':rflags,
                   'road_idr':flags&1,'wide_idr':(flags>>1)&1,'srv_recv_us':telemetry[0],'srv_prep_us':telemetry[1],
                   'srv_htp_us':telemetry[2],'srv_total_us':telemetry[3]})
   result['beacon_requests']=beacon.requests
  # Passive observation does not create clients or confuse residency with inference work.
  from idle import observe
  idle=observe(15)
  (a.output/'idle.json').write_text(json.dumps(idle,indent=2)+'\n')
  result['inference_idle']=idle['inference_idle']
  result['model_selection']=json.loads((a.output.parent/'model-selection.json').read_text())
 # Load the real stock ModelState; only Params hardware boundary is isolated on this Mac.
 sys.path[:0]=[str(a.system),str(a.system/'tinygrad_repo'),str(a.system/'msgq_repo'),str(a.system/'opendbc_repo')]
 from openpilot.selfdrive.modeld import helpers,modeld
 helpers.MODELS_DIR=a.stock;modeld.MODELS_DIR=a.stock
 local=modeld.ModelState(512,256,False)
 local.warmup()
 from plugins.lean_mici import profile
 from plugins.lean_mici.source import PhoneSource
 from plugins.lean_mici.worker import publish
 from unittest.mock import patch
 import tempfile
 mailbox=tempfile.TemporaryDirectory()
 source=PhoneSource(mailbox.name)
 replies=(a.output/'replies.wire').read_bytes()
 host_selected=0;epoch=0
 errors={};passed=0
 local_heads={};remote_heads={};local_times=[]
 for i,pair in enumerate(decoded):
  pair=[np.memmap(a.input/f'camera-{j}.nv12',dtype=np.uint8,mode='r').reshape(-1,384,512)[a.start+i] for j in range(2)]
  inputs={'desire_pulse':desires[i].copy(),'traffic_convention':traffic[i].copy(),'action_t':actions[i].copy()}
  local_start=time.perf_counter_ns()
  outputs=local.run({key:types.SimpleNamespace(data=pair[j].tobytes()) for j,key in enumerate(('img','big_img'))},
                    {key:np.eye(3,dtype=np.float32) for key in ('img','big_img')},inputs)
  local_times.append((time.perf_counter_ns()-local_start)/1e6)
  remote=profile.decode(phone[i])
  rows[i]['envelope_reason']=profile.reject_reason(outputs,remote)
  adopted=profile.admit({k:v.copy() for k,v in outputs.items()},remote)
  rows[i]['numeric_admitted']=int(adopted);passed+=adopted
  wire_reply=replies[i*8480:(i+1)*8480]
  sent_dual=int(rows[i]['road_idr']) and int(rows[i]['wide_idr'])
  if rows[i]['flags']&1 and not sent_dual:raise ValueError('phone sequence reset requires fresh dual IDR')
  if sent_dual:epoch+=1
  epoch=max(epoch,1)
  source.frame=rows[i]['frameId'];source.eof=rows[i]['EOF'];source.sent=True
  publish(source.fd,source.session,epoch,rows[i]['arrival'],3,wire.to_mailbox(wire_reply,source.frame))
  with patch('openpilot.tesla.model_source.nanos_since_boot',return_value=rows[i]['arrival']):
   _,selected=source.pick(outputs)
  rows[i]['selected_source']='phone' if selected else 'local'
  rows[i]['reject_reason']=source.reason
  host_selected+=selected
  for k in ('plan','lead','lead_prob','pose','lane_lines','road_edges'):
   difference=np.abs(remote[k]-outputs[k])
   errors.setdefault(k,[]).append(float(difference.max()))
   local_heads.setdefault(k,[]).append(outputs[k].copy())
   remote_heads.setdefault(k,[]).append(remote[k].copy())
 source.close();mailbox.cleanup()
 np.savez_compressed(a.output/'stock-heads.npz',**{k:np.array(v) for k,v in local_heads.items()})
 np.savez_compressed(a.output/'decoded-phone-heads.npz',**{k:np.array(v) for k,v in remote_heads.items()})
 np.savez_compressed(a.output/'phone-outputs.npz',outputs=np.array(phone),desire=desires,action_t=actions)
 latency=np.array([r['latency_ms'] for r in rows])
 result.update({'frames':len(rows),'latency_ms':dict(zip(('p50','p95','p99'),map(float,np.percentile(latency,[50,95,99])))),
                'within_45ms':float(np.mean(latency<=45)), 'envelope_acceptance_rate':passed/len(rows), 'host_adoption_replay_rate':host_selected/len(rows), 'rejection_counts':dict(__import__('collections').Counter(r['reject_reason'] for r in rows if r['selected_source']=='local')), 'envelope_rejection_counts':dict(__import__('collections').Counter(r['envelope_reason'] for r in rows if r['envelope_reason'])), 'A1':{'status':'WAIVED','reason':'coordinator FINAL: exact v4 wire audit plus independent real-route A2 envelope 99.33% substitute','required_sha256':'ceb38c593005b3338aee4ca057d5ad9ee64c58f8fdba0b41641a9d4cc6c36e36'},
                'stock_comparison_is_divergence_not_equality':True, 'local_inference_ms':{'p50':float(np.median(local_times))},
                'stock_walnut_head_divergence':{k:{'p50':float(np.median(v)),'p99':float(np.percentile(v,99)),'max':max(v)} for k,v in errors.items()},
                'stock_sha256':hashlib.sha256((a.stock/'driving_supercombo.onnx').read_bytes()).hexdigest()})
 result['route_start']=a.start
 result['route_input']=str(a.input)
 result['pass']=len(rows)>=600 and result.get('inference_idle',False) and passed>=.95*len(rows) and result['A1']['status'] in ('PASS','WAIVED')
except BaseException as e:
 result['error']=repr(e)
 raise
finally:
 result['frames']=len(rows)
 (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 with (a.output/'frames.csv').open('w') as f:
  if rows:
   writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
  else:f.write('frameId,EOF,arrival\n')
 (a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
print(json.dumps(result,indent=2))

sys.exit(0 if result["pass"] else 1)
