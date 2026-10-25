#!/usr/bin/env python3
"""Real route -> separate production worker/phone/IPC -> concurrent stock/S1.
The separate process is essential: POSIX range locks are per process, not thread.
Camera/encoding are hash-pinned archived HEVC; network, IPC, stock and selection are live.
Mac interface and AS-limit adaptation replace device hardware.
Failure modes are fixed in FAILURES.md; no phone response or selector is mocked.
"""
import argparse,csv,hashlib,json,multiprocessing,sys,tempfile,time,types,pickle,os
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici import worker,network
from archive_camera import Encoder
from plugins.lean_mici.source import PhoneSource
from openpilot.selfdrive.modeld import helpers,modeld
from idle import observe, sample


def archive_worker(path,input_dir,address,output_dir,stop,ack,parent):
 from openpilot.tesla import manifest
 manifest.INSTALL_DIR=Path(output_dir)/"store"
 cache=pickle.loads((Path(output_dir)/'encoded-cache.pkl').read_bytes())
 class ArchiveCamera:
  def close(self):pass
  def capture(self,meta,reset):
   if reset and meta[3]!=0:raise RuntimeError('archive reference chain interrupted; requires a new encoded sequence')
   return cache[meta[3]]
 original_sleep=time.sleep
 original_meta=worker.metadata;original_frame=worker.frame;original_receive=worker.receive_reply;original_publish=worker.publish
 class EndWorker(Exception):pass
 out=Path(output_dir)
 with (out/'frames.wire').open('wb',buffering=0) as fw,(out/'replies.wire').open('wb',buffering=0) as rw,(out/'arrivals.csv').open('w',buffering=1) as af:
  writer=csv.DictWriter(af,fieldnames=['frameId','arrival','latency_ms','epoch']);writer.writeheader()
  def sleep(seconds):
   if stop.is_set():raise EndWorker
   original_sleep(seconds)
  def metadata(fd):
   if stop.is_set():raise EndWorker
   return original_meta(fd)
  def frame(*args):
   first,second=original_frame(*args);fw.write(len(first+second).to_bytes(4,'little')+first+second);return first,second
  def receive(conn):
   data=original_receive(conn);rw.write(data);return data
  def publish(fd,session,epoch,arrival,paired,data):
   fid=int.from_bytes(data[8:12],'little');eof=int.from_bytes(data[16:24],'little')
   writer.writerow({'frameId':fid,'arrival':arrival,'latency_ms':(arrival-eof)/1e6,'epoch':epoch})
   original_publish(fd,session,epoch,arrival,paired,data)
   ack.value=fid
  try:
   with (patch('plugins.lean_mici.camera.Camera',return_value=ArchiveCamera()),patch.object(worker.resource,'setrlimit'),
         patch.object(worker,'scope',return_value=(address,None)),patch.object(worker,'discover',side_effect=lambda:network.discover('en0')),
         patch.object(worker.time,'sleep',sleep),patch.object(worker,'metadata',metadata),patch.object(worker,'frame',frame),patch.object(worker,'receive_reply',receive),patch.object(worker,'publish',publish)):
    worker.main(path,parent)
  except EndWorker:pass
  except BaseException as e:
   (out/'worker-error.json').write_text(json.dumps({'error':repr(e)}));raise


def main():
 p=argparse.ArgumentParser()
 for key in ['system','stock','input','output']:p.add_argument('--'+key,type=Path,required=True)
 p.add_argument('--address',required=True);p.add_argument('--frames',type=int,default=700);p.add_argument('--encoded-cache',type=Path)
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 state=sample()
 (a.output/'phone-before.json').write_text(json.dumps(state,indent=2)+'\n')
 if state['bgm_clients']:raise RuntimeError('phone busy; no connection started')
 helpers.MODELS_DIR=a.stock;modeld.MODELS_DIR=a.stock
 local=modeld.ModelState(512,256,False);local.warmup()
 images=[np.memmap(a.input/f'camera-{j}.nv12',mode='r',dtype=np.uint8).reshape(-1,384,512) for j in range(2)]
 if a.encoded_cache:
  cache=pickle.loads(a.encoded_cache.read_bytes())
  assert len(cache)>=a.frames
 else:
  encoders=[Encoder(),Encoder(wide=True)];cache=[]
  for i in range(a.frames):
   out=[e.encode(images[j][i],i==0) for j,e in enumerate(encoders)]
   cache.append((int(out[0][1]) | int(out[1][1])<<1,out[0][0],out[1][0]))
 (a.output/'encoded-cache.pkl').write_bytes(pickle.dumps(cache,protocol=5))
 inputs=np.load(a.input/'inputs.npz');rows=[];ctx=multiprocessing.get_context('spawn');stop=ctx.Event();ack=ctx.Value('i',-1)
 with tempfile.TemporaryDirectory() as directory:
  source=PhoneSource(directory)
  process=ctx.Process(target=archive_worker,args=(source.path,str(a.input),a.address,str(a.output),stop,ack,os.getpid()));process.start()
  try:
   time.sleep(4)
   start=time.monotonic();origin=worker.now();previous=np.zeros(8,np.float32)
   for i in range(a.frames):
    time.sleep(max(0,start+i*.05-time.monotonic()))
    eof=origin+i*50_000_000
    desire=inputs['desire'][i].copy();traffic=inputs['traffic'][i].copy();action=inputs['action_t'][i].copy()
    ins={'desire_pulse':desire,'traffic_convention':traffic,'action_t':action}
    meta=types.SimpleNamespace(frame_id=i,timestamp_eof=eof)
    transforms={k:np.eye(3,dtype=np.float32) for k in ('img','big_img')}
    source.send_frame(meta,meta,transforms,ins,previous)
    if i<100:
     deadline=time.monotonic()+2
     while not source.sent and time.monotonic()<deadline:
      time.sleep(.001);source.send_frame(meta,meta,transforms,ins,previous)
    outputs=local.run({k:types.SimpleNamespace(data=images[j][i].tobytes()) for j,k in enumerate(('img','big_img'))},transforms,ins)
    _,selected=source.pick(outputs);previous=desire
    rows.append({'frameId':i,'EOF':eof,'publish':worker.now(),'source':'phone' if selected else 'local','reason':source.reason,'startup':i<100})
    if not process.is_alive():raise RuntimeError('optional worker exited')
    if i<100:
     deadline=time.monotonic()+2
     while ack.value<i and time.monotonic()<deadline:time.sleep(.001)
     if ack.value<i:raise RuntimeError('archive warm-up response missing')
   time.sleep(.25)
  finally:
   stop.set();process.join(2)
   if process.is_alive():process.terminate();process.join(2)
   if process.is_alive():process.kill();process.join()
   source.close()
  if process.exitcode not in (0,-15):raise RuntimeError(f'worker exited {process.exitcode}')
 idle=observe(15);(a.output/'idle.json').write_text(json.dumps(idle,indent=2)+'\n')
 arrivals={}
 with (a.output/'arrivals.csv').open() as f:
  for r in csv.DictReader(f):arrivals[int(r['frameId'])]={'arrival':int(r['arrival']),'latency_ms':float(r['latency_ms']),'epoch':int(r['epoch'])}
 for row in rows:row.update(arrivals.get(row['frameId'],{'arrival':0,'latency_ms':None,'epoch':0}))
 normal=rows[100:];lat=[r['latency_ms'] for r in normal if r['latency_ms'] is not None]
 counts=dict(__import__('collections').Counter(r['reason'] for r in normal if r['source']=='local'))
 rate=sum(r['source']=='phone' for r in normal)/len(normal)
 result={'pass':len(normal)>=600 and rate>=.90 and idle['pass'],'scope':'FINAL real-phone adoption with system engaged bounded wait; A1 coordinator-waived',
  'A1':{'status':'WAIVED','reason':'coordinator FINAL: original ONNX unavailable; exact v4 wire audit plus independent real-route A2 envelope 99.33% substitute'},
  'frames_published':len(rows),'phone_replies':len(arrivals),'normal_frames':len(normal),'normal_adoption_rate':rate,
  'all_frame_adoption_rate':sum(r['source']=='phone' for r in rows)/len(rows),
  'latency_ms':dict(zip(('p50','p95','p99'),map(float,np.percentile(lat,[50,95,99])))),
  'rejection_counts':counts,'all_rejection_counts':dict(__import__('collections').Counter(r['reason'] for r in rows if r['source']=='local')),
  'idle_pass':idle['pass'],'limits':['real phone, real stock and real source pick concurrently; archived preencoded driving frames, encoding excluded from replay EOF latency','no car or control output']}
 (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 with (a.output/'frames.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 (a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(a.output)}\n' for p in sorted(a.output.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'))
 print(json.dumps(result,indent=2))


if __name__=='__main__':
 try:main()
 except Exception as error:
  output=Path(sys.argv[sys.argv.index('--output')+1]);output.mkdir(parents=True,exist_ok=True)
  (output/'result.json').write_text(json.dumps({'pass':False,'error':repr(error)},indent=2)+'\n')
  if not (output/'frames.csv').exists():(output/'frames.csv').write_text('frameId,source,reason\n')
  (output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(output)}\n' for p in sorted(output.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'))
  raise
