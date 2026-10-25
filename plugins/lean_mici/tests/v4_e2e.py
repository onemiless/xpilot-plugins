#!/usr/bin/env python3
"""v4 -> unchanged S1 wire and Python camera/encoder E2E. Cases fixed in FAILURES.md."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import types

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.modules['openpilot.common.params']=types.SimpleNamespace(Params=type('Params',(),{}),UnknownKeyName=KeyError)
from plugins.lean_mici import wire
from plugins.lean_mici.tests import archive_camera as camera

p=argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
a=p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
result={'pass':False}
try:
 raw=np.arange(2066,dtype='<f4') / 1000
 data=struct.pack('<4sBBHIIQ',b'BGM1',4,0x11,0,23,8440,456)+raw.tobytes()+bytes(16)+bytes(range(160))+bytes(16)
 assert len(data)==8480
 meta=(b'XMS1',1,1,23,23,456,456,*np.eye(3).ravel(),*np.eye(3).ravel(),*np.zeros(8),1,0,.225,.575)
 chunks=wire.frame(meta,b'road',b'wide',3,23)
 assert len(chunks[0])==152+4 and chunks[0][152:]==b'road'
 legacy=wire.to_mailbox(data,105)
 assert len(legacy)==8320
 assert struct.unpack_from('<4sBBHIIQ',legacy)==(b'BGM1',2,0x11,0,105,8280,456)
 np.testing.assert_array_equal(np.frombuffer(legacy,'<f4',2066,24),raw)
 for bad in [data[:-1],data+b'\0',data[:4]+b'\2'+data[5:],data[:6]+b'\4\0'+data[8:]]:
  try:
   wire.to_mailbox(bad,105)
   raise AssertionError('bad v4 accepted')
  except ValueError:
   pass
 class Buffer:
  width,height,stride,uv_offset=512,256,640,640*288
  data=np.arange(640*(288+128),dtype=np.uint8)
 buf=Buffer()
 image=camera.warp(buf,np.eye(3,dtype=np.float32))
 expected=np.concatenate([buf.data[:640*256].reshape(256,640)[:,:512].ravel(),buf.data[buf.uv_offset:].reshape(128,640)[:,:512].ravel()]).reshape(384,512)
 np.testing.assert_array_equal(image,expected)
 encoded=[[],[]];keys=[[],[]]
 for stream in range(2):
  encoder=camera.Encoder(wide=bool(stream))
  decoder=av.CodecContext.create('hevc','r')
  for i in range(45):
   payload,key=encoder.encode(image,reset=i==0)
   encoded[stream].append(payload);keys[stream].append(key)
   frames=decoder.decode(av.Packet(payload))
   assert len(frames)==1 and (frames[0].width,frames[0].height)==(512,256)
  (a.output/f'camera-{stream}.hevc').write_bytes(b''.join(encoded[stream]))
 assert keys[0][0] and keys[1][0] and keys[0][20] and keys[1][10]
 assert not keys[1][20] and not keys[0][10]
 (a.output/'reply-v4.wire').write_bytes(data)
 (a.output/'reply-s1.wire').write_bytes(legacy)
 result.update({'pass':True,'source_geometry':{'stride':640,'uv_offset':640*288},'frames':90,'v4_to_s1_exact_outputs':True,'road_idr':[i for i,k in enumerate(keys[0]) if k],'wide_idr':[i for i,k in enumerate(keys[1]) if k]})
finally:
 (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 (a.output/'frames.csv').write_text('frameId,source\n'+''.join(f'{i},python_encoder\n' for i in range(result.get('frames',0))))
 (a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
