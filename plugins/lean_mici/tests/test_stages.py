"""P2 uplink stages. Failure cases: (1) stages do not add up to EOF->send; (3) stamps left from a skipped frame
or a previous connection (the native call zeroes them unless it returns a frame) produce a row."""
from plugins.lean_mici.worker import STAGES, stages, link_stats


def test_stages_add_up_to_eof_to_send():
  times = (1_000_000_000, 1_001_430_000, 1_002_890_000, 1_003_040_000, 1_003_200_000, 1_005_470_000, 1_005_600_000)
  row = stages(times, 1_006_000_000)
  assert tuple(row) == STAGES
  assert abs(sum(row.values()) - 6.0) < 1e-9
  assert row['recv'] == 1.43 and row['encode'] == 2.27


def test_missing_stamps_give_no_row():
  assert stages(None, 1) is None
  assert stages((0,) * 7, 1) is None
  assert stages((1, 2, 3, 0, 5, 6, 7), 8) is None


def test_link_stats_reports_each_stage():
  rows = [{**stages(tuple(range(1, 8)), 8), 'send': .5, 'eof_to_send': 7e-6, 'rtt': 20., 'eof_to_reply': 21.}]
  out = link_stats(rows, 0, 2_000_000)
  assert all(out[f'{k}_ms'] is not None for k in (*STAGES, 'send'))
  assert out['srv_htp_ms'] is None and 'capture_ms' not in out
