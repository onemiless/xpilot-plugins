#!/usr/bin/env python3
"""Real store/discovery/runner/S1 publication; QCOM inference/cameras are S1 replay doubles.
Failure cases were fixed in FAILURES.md before implementation. Run with system on PYTHONPATH.
"""
import argparse
import csv
import contextlib
import fcntl
import socket
import signal
import time
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
from unittest.mock import patch

import numpy as np


def load(path, name):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def tcp_peer(path, control):
  def terminate(signum, frame):
    raise SystemExit(0)
  signal.signal(signal.SIGTERM, terminate)
  from plugins.lean_mici import worker, wire
  from openpilot.tesla.model_source import META, ENVELOPE, OFFSET, SIZE
  h = load(Path(os.environ['X39_SYSTEM']) / 'tools/sp_tesla_e2e/model_source_e2e.py', 'tcp_replay')
  fd = os.open(path, os.O_RDWR)
  server = socket.socket()
  server.bind(('127.0.0.1', 0))
  server.listen(1)
  endpoint = server.getsockname()
  class Capture:
    def capture(self, meta, reset):
      payload = bytes.fromhex('000000012601')
      return 3 if reset else 0, payload, payload
  def run_worker():
    with (patch('plugins.lean_mici.camera.Camera', return_value=Capture()),
          patch.object(worker.resource, 'setrlimit'),
          patch.object(worker.signal, 'signal'),
          patch.object(worker, 'scope', return_value=('127.0.0.1', None)),
          patch.object(worker, 'Beacon', side_effect=lambda address: contextlib.nullcontext()),
          patch.object(worker, 'discover', return_value=[('test phone', *endpoint)]),
          patch('openpilot.tesla.manifest.INSTALL_DIR', Path(path).parent),
          patch.object(worker, 'now', side_effect=lambda: META.unpack(os.pread(fd, META.size, 0))[5] + 20_000_000)):
      worker.main(path, os.getppid())
  thread = threading.Thread(target=run_worker, daemon=True)
  thread.start()
  try:
    conn, _ = server.accept()
    conn.settimeout(2)
    conn.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    with conn:
      hello = wire.HEADER.pack(b'BGM1', 4, 1, 0, 0, 16) + struct.pack('<QII', 12, 1024 * 1024, 0) + bytes(16)
      conn.sendall(hello)
      while True:
        scenario, epoch, now, expected = control.recv()
        data = wire.receive(conn, 152)
        magic, version, kind, flags, frame, road_size = wire.HEADER.unpack_from(data)
        assert (magic, version, kind) == (b'BGM1', 4, 0x10)
        road = wire.receive(conn, road_size)
        wide_size = struct.unpack('<I', wire.receive(conn, 4))[0]
        wide = wire.receive(conn, wide_size + 16)
        meta = os.pread(fd, META.size, 0)
        values = META.unpack(meta)
        assert frame == values[3] - 1
        assert struct.unpack_from('<Q', data, 16)[0] == values[5]
        np.testing.assert_array_equal(np.frombuffer(data, '<f4', 12, 24), expected)
        reply = struct.pack('<4sBBHIIQ', b'BGM1', 4, 0x11, 0, frame, 8440, values[5])
        reply += h.raw_output(.1).tobytes() + bytes(192)
        # Exercise arbitrary fragmentation, including across the wire header.
        for start in range(0, len(reply), 137):
          conn.sendall(reply[start:start + 137])
        end = time.monotonic() + 2
        while time.monotonic() < end:
          fcntl.lockf(fd, fcntl.LOCK_EX, SIZE - OFFSET, OFFSET)
          envelope = os.pread(fd, ENVELOPE.size, OFFSET)
          fcntl.lockf(fd, fcntl.LOCK_UN, SIZE - OFFSET, OFFSET)
          if ENVELOPE.unpack(envelope)[-1]:
            control.send((envelope + wire.to_mailbox(reply, values[3]), meta))
            break
          time.sleep(.001)
        else:
          raise AssertionError('worker did not commit REPLY')
  finally:
    server.close()
    os.close(fd)


def main():
  p = argparse.ArgumentParser()
  p.add_argument('--system', type=Path, required=True)
  p.add_argument('--output', type=Path, required=True)
  args = p.parse_args()
  os.environ['X39_SYSTEM'] = str(args.system.resolve())
  out = args.output.resolve()
  out.mkdir(parents=True, exist_ok=False)
  result = {'pass': False, 'limits': ['synthetic QCOM inference/VisionIPC camera capture/local preparation',
                                    'real phone/stock qualification runs separately; no C3XL device proof']}
  rows = []
  try:
    h = load(args.system / 'tools/sp_tesla_e2e/model_source_e2e.py', 's1_replay')
    from openpilot.tesla import manifest as mf, plugins, store
    from openpilot.tesla.plugin_params import ParamsView
    from openpilot.tesla.model_source import ModelSource
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    from plugins.lean_mici import profile, runner, wire
    raw = h.raw_output()
    expected = h.decode(raw.copy())
    actual = profile.decode(raw.copy())
    assert actual.keys() == expected.keys() | {'action_stds'}
    np.testing.assert_array_equal(actual['action_stds'], np.exp(np.clip(raw[2064:2066][None], -np.inf, 11)))
    for key in expected:
      np.testing.assert_array_equal(actual[key], expected[key], err_msg=key)
    for n in (0, 2065, 2067):
      try:
        profile.decode(np.ones(n, dtype=np.float32))
        raise AssertionError('wrong raw shape accepted')
      except ValueError:
        pass
    assert profile.admit(expected, actual) is True
    for key in ('plan','pose','action'):
      jumped = {k:v.copy() for k,v in actual.items()}
      jumped[key] += 100
      assert profile.admit(expected, jumped) is False
    good = struct.pack('<4sBBHIIQ', b'BGM1', 4, 0x11, 0, 1, 8440, 123) + raw.tobytes() + bytes(192)
    assert len(good) == 8480 and wire.reply(good)[0] == 1
    for bad in (good[:-1], good + b'\0', good[:4] + b'\2' + good[5:], good[:6] + b'\4\0' + good[8:]):
      try:
        wire.reply(bad)
        raise AssertionError('bad wire accepted')
      except ValueError:
        pass
    (out / 'profile.wire').write_bytes(good)
    # S2 transaction calls a fresh interpreter. Only stock preparation is substituted there.
    with tempfile.TemporaryDirectory() as tmp:
      installed = Path(tmp) / 'installed'
      installed.mkdir()
      class Params:
        values = {'IsOffroad': b'1'}
        def get_bool(self, key):
          return self.values.get(key) == b'1'
        def get(self, key, **kwargs):
          return self.values.get(key)
        def put_bool(self, key, value, **kwargs):
          self.values[key] = b'1' if value else b'0'
        def remove(self, key):
          self.values.pop(key, None)
      params = Params()
      real_run = store.subprocess.run
      def probe(command, **kw):
        command = list(command)
        command[2] = 'import sys, types\nsys.modules["openpilot.common.params"] = types.SimpleNamespace(Params=type("Params", (), {}), UnknownKeyName=KeyError)\nfrom unittest.mock import patch\nfrom openpilot.tesla import plugins\nwith patch("xpilot_ext.lean_mici.plugin.ready", return_value=True):\n' + ''.join('  ' + line + '\n' for line in command[2].splitlines())
        return real_run(command, **kw)
      archive = (root / 'dist/lean_mici-0.5.0.tar.gz').read_bytes()
      entry = store.IndexEntry('lean_mici', 'lean mici', '0.5.0', 1, 'perception_model', 'xpilot', '', 'local',
                               hashlib.sha256(archive).hexdigest(), installable=True)
      with (patch.object(mf, 'INSTALL_DIR', installed), patch.object(store, '_get', return_value=archive),
            patch.object(store.subprocess, 'run', probe), patch('openpilot.common.params.Params', return_value=params)):
        error = store.install(entry, activate=True)
        assert error is None, error
        assert params.get_bool('DoReboot')
        classes, errors, _ = plugins._discover()
        assert 'lean_mici' not in errors, errors
        cls = next(c for c in classes if c.NAME == 'lean_mici')
        assert ParamsView(params).get_bool(cls.ENABLE_PARAM)
        winners, _ = mf.resolve([(c.NAME, c.PRIORITY, c.CLAIMS) for c in classes], {c.NAME for c in classes})
        assert cls.NAME in winners and 'sp_models' not in winners
        assert [p.name for p in cls.processes()] == ['lean_modeld']
        view = ParamsView(params)
        for enabled in (False, True):
          view.put_bool(cls.ENABLE_PARAM, enabled)
          assert store.install(entry) is None
          assert view.get_bool(cls.ENABLE_PARAM) is enabled
        before = (installed / 'lean_mici/plugin.json').read_bytes()
        with patch.object(store, '_get', return_value=b'corrupt'):
          assert store.install(entry, activate=True)
        assert (installed / 'lean_mici/plugin.json').read_bytes() == before
        assert store.install(entry, activate=True, offroad=lambda: False) == 'park first'
        with patch.object(h.modeld, 'run_with_model_source'):
          del h.modeld.run_with_model_source
          from plugins.lean_mici.prepare import ready
          try:
            ready()
          except ImportError:
            pass
          else:
            raise AssertionError('old host preparation accepted')
      h.decode = profile.decode
      ctx = multiprocessing.get_context('fork')
      # Reuse the system's same-frame fault peer and real Capnp/msgq health replay.
      for name, scenarios in [('absent', ['absent'] * 20), ('good', ['good'] * 20), ('tcp', ['good'] * 20),
                              ('recovery', ['good'] * 10 + ['late'] * 4 + ['good'] * 10)] + [
          (case, ['good'] * 10 + [case] + ['good'] * 8) for case in
          ('magic', 'version', 'flags', 'length', 'nan', 'inf', 'zero', 'session', 'frame', 'eof',
           'future', 'jump_plan', 'jump_lead', 'jump_pose', 'jump_action')]:
        source = ModelSource(tmp, profile.decode, h.admit)
        parent, child = ctx.Pipe()
        peer_ctx = multiprocessing.get_context('spawn') if name == 'tcp' else ctx
        peer = peer_ctx.Process(target=tcp_peer if name == 'tcp' else h.peer, args=(source.path, child))
        peer.start()
        try:
          records, _ = h.replay(h.modeld, out, name, scenarios, source, parent)
          rows.extend(records)
          if name in ('good', 'tcp'):
            assert records[4]['source'] == 'local' and records[5]['source'] == 'phone'
          elif name not in ('absent', 'recovery'):
            assert source.locked and all(r['source'] == 'local' for r in records[10:])
        finally:
          peer.terminate()
          peer.join(2)
          if peer.is_alive():
            peer.kill()
            peer.join()
          source.close()
      from plugins.lean_mici.source import PhoneSource
      for name,scenarios in [('envelope_recovery',['good']*10+['jump_plan']+['good']*8),
                             ('late_recovery',['good']*10+['late']+['good']*8),
                             ('protocol_latch',['good']*10+['magic']+['good']*8)]:
        source=PhoneSource(tmp)
        parent,child=ctx.Pipe();peer=ctx.Process(target=h.peer,args=(source.path,child));peer.start()
        try:
          records,_=h.replay(h.modeld,out,name,scenarios,source,parent)
          rows.extend(records)
          assert records[10]['source']=='local'
          if name=='protocol_latch':
            assert source.locked and all(r['source']=='local' for r in records[10:])
          else:
            assert not source.locked
            if name=='envelope_recovery':
              assert all(r['source']=='local' for r in records[11:16]) and records[16]['source']=='phone'
            else:
              assert records[11]['source']=='phone'
        finally:
          peer.terminate();peer.join(2)
          if peer.is_alive():peer.kill();peer.join()
          source.close()
      # Frozen/killed child cannot hold up the host; base inference failures propagate.
      source = ModelSource(tmp, profile.decode, h.admit)
      parent, child = ctx.Pipe()
      peer = ctx.Process(target=h.peer, args=(source.path, child))
      peer.start()
      parent.send('freeze')
      assert parent.poll(2) and parent.recv() == 'frozen'
      records, _ = h.replay(h.modeld, out, 'frozen', ['absent'] * 20, source, paced=True)
      rows.extend(records)
      peer.kill()
      peer.join(2)
      records, _ = h.replay(h.modeld, out, 'crashed', ['absent'] * 20, source, paced=True)
      rows.extend(records)
      source.close()
      for failure in ('load', 'run'):
        source = ModelSource(tmp, profile.decode, h.admit)
        try:
          h.replay(h.modeld, out, 'local_' + failure, ['absent'], source, local_failure=failure)
          raise AssertionError('base inference failure swallowed')
        except RuntimeError as error:
          assert str(error) == 'local ' + failure + ' failed'
        finally:
          source.close()
      # Exercise the actual runner's finally path without device/native work.
      with (patch.object(runner, 'run_with_model_source', side_effect=h.EndReplay),
            patch.object(runner, 'data_dir', return_value=Path(tmp)),
            patch.object(runner.multiprocessing, 'Process') as process):
        try:
          runner.main()
        except h.EndReplay:
          pass
        assert process.return_value.terminate.called
        assert not list(Path(tmp).glob('model-source-*'))
    result.update({'pass': True, 'frames': len(rows), 'store_install_enable_discover': True,
                   'rollback': True, 'user_toggles_preserved': True, 'old_host_rejected': True, 'runner_cleanup': True,
                   'synthetic_decoder_match': True, 'production_profile_admission': True, 'tcp_worker_pipeline': True, 'frozen_crashed_fallback': True, 'base_failures_propagate': True})
  except BaseException as error:
    result['error'] = repr(error)
    raise
  finally:
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    with (out / 'frames.csv').open('w') as f:
      if rows:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
      else:
        f.write('epoch,frameId,EOF,arrival,publish,source,reject_reason\n')
    (out / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}\n'
                                          for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS'))


if __name__ == '__main__':
  with tempfile.TemporaryDirectory(prefix='msgq_x39_', dir='/tmp' if sys.platform == 'darwin' else '/dev/shm') as ipc:
    os.environ['OPENPILOT_PREFIX'] = Path(ipc).name.removeprefix('msgq_')
    main()
