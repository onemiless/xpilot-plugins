"""Passive phone inference-idle evidence; never opens a BGM connection or changes settings."""
import json
import subprocess
import time

SERIAL='3B162S00PHS00000'


def sample():
  command='''p=$(pidof com.chipmunk.inference:npu); echo PID:$p; if [ -n "$p" ]; then cat /proc/$p/stat; for t in /proc/$p/task/*; do cat $t/wchan; echo; done; fi; echo SOCKETS; cat /proc/net/tcp /proc/net/tcp6'''
  result=subprocess.run(['adb','-s',SERIAL,'shell','su -c '+__import__('shlex').quote(command)],capture_output=True,text=True,timeout=5)
  if result.returncode:raise RuntimeError('ADB identity read failed')
  lines=result.stdout.splitlines();pid=lines[0].removeprefix('PID:').strip()
  cut=lines.index('SOCKETS');sockets=[]
  for line in lines[cut+1:]:
    fields=line.split()
    if len(fields)>3 and ':1B9E' in fields[1]:sockets.append(fields[3])
  ticks=0;start=0
  if pid:
    fields=lines[1].rsplit(')',1)[1].split()
    ticks=int(fields[11])+int(fields[12]);start=int(fields[19])
  return {'time':time.monotonic(),'pid':pid,'ticks':ticks,'process_start':start,'bgm_clients':sockets.count('01'),
          'listener':sockets.count('0A')>0,'server_waiting_accept':any('inet_csk_accept' in s for s in lines[:cut])}


def observe(seconds=15):
  rows=[];deadline=time.monotonic()+seconds
  while True:
    rows.append(sample())
    if time.monotonic()>=deadline:break
    time.sleep(1)
  stable=[r for r in rows if r['time']>=rows[0]['time']+5]
  cpu=[]
  for previous,current in zip(stable,stable[1:]):
    if previous['pid']==current['pid'] and previous['process_start']==current['process_start']:
      cpu.append(max(0,current['ticks']-previous['ticks'])/(current['time']-previous['time']))
  # CLK_TCK=100 on the verified phone: one jiffy per second is 1% of one core.
  idle=bool(stable) and all(r['bgm_clients']==0 for r in stable) and (not cpu or max(cpu)<=2.)
  idle &= all(not r['pid'] or r['server_waiting_accept'] for r in stable)
  return {'pass':bool(idle),'samples':rows,'cpu_one_core_percent_max':max(cpu,default=0.),
          'criterion':'within 15s: no BGM client, server blocked in accept or absent, <=2% one CPU core during final 10s',
          'inference_idle':bool(idle),'listener_shutdown_required':False}
