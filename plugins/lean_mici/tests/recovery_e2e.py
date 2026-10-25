#!/usr/bin/env python3
"""Real TCP/child/IPC recovery, parent death and slow-drip checks; FAILURES.md first."""
import argparse,contextlib,csv,hashlib,json,multiprocessing,os,signal,socket,struct,sys,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici import worker,wire,network


def child(path,parent,address,root,orphan=False):
 from openpilot.tesla import manifest
 manifest.INSTALL_DIR=Path(root)
 count=0
 class Capture:
  def __init__(self):
   nonlocal count
   count+=1
   if orphan:self.resource=os.open('/dev/null',os.O_RDONLY)
   (Path(root)/'camera-count').write_text(str(count))
  def close(self):pass
  def capture(self,meta,reset):
   if orphan:
    (Path(root)/'orphan-camera-ready').touch();time.sleep(.2);return None
   import av
   errors=[ValueError('calibration not ready'),RuntimeError('encoder failed'),struct.error('bad input'),av.error.InvalidDataError(1,'bad HEVC')]
   if count<=len(errors):raise errors[count-1]
   return 3 if reset else 1,b'road',b'wide'
 calls=0
 def discover():
  nonlocal calls
  calls+=1
  if calls==1 and not orphan:raise ValueError('malformed Avahi address/port')
  return [('test phone',*address)]+([('other phone','192.0.2.1',7070)] if isinstance(json.loads((Path(root)/'.params/ExtLeanMiciPeer').read_text()),dict) else [])
 @contextlib.contextmanager
 def beacon(address):
  with socket.socket() as listener:
   listener.bind(('127.0.0.1',0));listener.listen(1)
   (Path(root)/'orphan-beacon-port').write_text(str(listener.getsockname()[1]))
   yield
 with (patch('plugins.lean_mici.camera.Camera',Capture),patch.object(worker.resource,'setrlimit'),
       patch.object(worker,'scope',return_value=('127.0.0.1',None)),patch.object(worker,'Beacon',side_effect=beacon if orphan else lambda a:contextlib.nullcontext()),patch.object(worker,'discover',discover)):
  worker.main(path,parent)


def orphan_parent(path,root,control,address):
 process=multiprocessing.get_context('spawn').Process(target=child,args=(path,os.getpid(),address,root,True))
 process.start();control.send(process.pid)
 while True:time.sleep(1)


def metadata(fd,frame):
 eof=worker.now()
 os.pwrite(fd,worker.META.pack(b'XMS1',1,42,frame,frame,eof,eof,*([0.]*30)),0)


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 checks=[]
 import subprocess
 subprocess.run([sys.executable,'-c',"import sys;from plugins.lean_mici import plugin;assert 'openpilot.selfdrive.modeld.modeld' not in sys.modules;assert not any(x.startswith('tinygrad') for x in sys.modules)"],check=True)
 checks.append('manager_import_is_light')
 # A peer that stays below each recv timeout still cannot hold a message open.
 for kind in ('hello','reply'):
  left,right=socket.socketpair();left.settimeout(.1)
  data=bytes(48) if kind=='hello' else wire.HEADER.pack(b'BGM1',4,0x11,0,0,8440)+bytes(8464)
  def drip():
   try:
    for b in data:right.sendall(bytes([b]));time.sleep(.01)
   except OSError:pass
  t=threading.Thread(target=drip,daemon=True);t.start();start=time.monotonic()
  try:
   (wire.receive(left,48) if kind=='hello' else wire.receive_reply(left))
   raise AssertionError('slow drip was accepted')
  except TimeoutError:pass
  finally:left.close();right.close();t.join(1)
  assert time.monotonic()-start<.35
  checks.append(kind+'_overall_deadline')
 ctx=multiprocessing.get_context('spawn')
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);(root/'.params').mkdir();(root/'.params/ExtLeanMiciPeer').write_text('[1]');path=root/'mailbox';fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600);os.ftruncate(fd,worker.SIZE);metadata(fd,1)
  server=socket.socket();server.bind(('127.0.0.1',0));server.listen(8);server.settimeout(10)
  process=ctx.Process(target=child,args=(str(path),os.getpid(),server.getsockname(),tmp));process.start()
  connections=0;good=0;epochs=[]
  try:
   while good<2:
    conn,_=server.accept();connections+=1;conn.settimeout(2)
    with conn:
     hello=wire.HEADER.pack(b'BGM1',4,1,0,0,16)+struct.pack('<QII',123,1024*1024,0)+bytes(16)
     conn.sendall(hello if connections>1 else b'X'*48)
     try:
      while True:
       data=wire.receive(conn,152,time.monotonic()+2);road=wire.HEADER.unpack_from(data)[-1]
       wire.receive(conn,road);wide=struct.unpack('<I',wire.receive(conn,4))[0];wire.receive(conn,wide+16)
       index=struct.unpack_from('<I',data,8)[0];eof=struct.unpack_from('<Q',data,16)[0]
       # Corrupt reply first, then a real server reset on road-only IDR.
       reset=good==1 and connections==7
       flags=1 if reset else 0
       reply=struct.pack('<4sBBHIIQ',b'BGM1',4,0x11,flags,index,8440,eof)+struct.pack('<2066f',*([1.]*2066))+bytes(192)
       if connections==6:reply=b'X'+reply[1:]
       conn.sendall(reply)
       if connections==6 or reset:
        assert conn.recv(1)==b''
        break
       end=time.monotonic()+2
       while time.monotonic()<end:
        envelope=worker.ENVELOPE.unpack(os.pread(fd,worker.ENVELOPE.size,worker.OFFSET))
        if envelope[-1]:break
        time.sleep(.001)
       assert envelope[-1]==8320
       epochs.append(envelope[1]);good+=1
       os.pwrite(fd,bytes(worker.ENVELOPE.size),worker.OFFSET)
       metadata(fd,2)
       if good==2:break
     except (ConnectionError,ConnectionResetError):pass
   assert connections>=8 and epochs[1]>epochs[0] and process.is_alive()
   pin=json.loads((root/'.params/ExtLeanMiciPeer').read_text());assert pin['name']=='test phone'
   checks+=['bad_avahi_hello_camera_encoder_struct_reply_reconnect','road_only_sequence_reset_reconnect','tofu_persisted','malformed_peer_unpinned']
  finally:
   process.terminate();process.join(3)
   if process.is_alive():process.kill();process.join()
   server.close()
  parent_control,child_control=ctx.Pipe()
  listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(5)
  parent=ctx.Process(target=orphan_parent,args=(str(path),tmp,child_control,listener.getsockname()));parent.start()
  assert parent_control.poll(5);pid=parent_control.recv()
  conn,_=listener.accept();conn.settimeout(5);conn.sendall(hello)
  end=time.monotonic()+5
  while not (root/'orphan-camera-ready').exists() and time.monotonic()<end:time.sleep(.01)
  assert (root/'orphan-camera-ready').exists()
  parent.kill();parent.join()
  end=time.monotonic()+4
  while time.monotonic()<end:
   import subprocess
   state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
   if not state or state.startswith('Z'):break
   time.sleep(.05)
  else:
   os.kill(pid,signal.SIGKILL);raise AssertionError('orphan survived parent death')
  assert conn.recv(1)==b''
  conn.close();listener.close()
  beacon_port=int((root/'orphan-beacon-port').read_text())
  try:
   probe=socket.create_connection(('127.0.0.1',beacon_port),timeout=.1)
  except ConnectionRefusedError:pass
  else:probe.close();raise AssertionError('orphan beacon survived')
  checks.append('parent_death_closes_child_camera_tcp_beacon')
  os.close(fd)
 result={'pass':True,'checks':checks,'scope':'real socket/worker/IPC with camera and discovery fixtures; actual codec checked separately'}
 (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 (a.output/'frames.csv').write_text('case,result\n'+''.join(f'{c},pass\n' for c in checks))
 (a.output/'wire.log').write_text(f'Controlled peer connections={connections}; published epochs={epochs}; parent-death checked.\n')
 (a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
 print(json.dumps(result))


if __name__=='__main__':main()
