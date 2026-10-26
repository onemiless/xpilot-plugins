#!/usr/bin/env python3
"""Failure cases frozen before implementation:
100 Hz double integration; zero-speed division; deadzone/sign/holding torque;
saturation windup; inactive state leakage/resume snap; learned safety VM;
disabled baseline/COOP mutation; non-finite input (including held frames);
exception/non-finite output fallback; card phase/limiter-order mismatch.
- M1 mixed switch off: stock Autosteer signal/event is hidden or SP engages.
- M2 mixed switch on: real stock Autosteer setting is cleared, event remains, or SP cannot engage.
- M3 no mixed plugin: stock Autosteer signal/event is hidden or SP engages.
- M4 event filtering reads the enable switch again after the host freezes active plugins at startup.

Runs real discovery, ParamsView, CarPlugins, lateral_controller guard,
LatControlAngle and Tesla CarController/CAN encoding. No device or daemon I/O.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

# Reuse the repository's compiled-Params substitute for the standalone CLI too.
runpy.run_path(str(Path(__file__).resolve().parents[2] / "conftest.py"))

from opendbc.can import CANParser
from opendbc.car import structs, Bus, DT_CTRL
from opendbc.car.lateral import apply_steer_angle_limits_vm, get_max_angle_delta_vm
from opendbc.car.tesla.carcontroller import get_safety_CP
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CarControllerParams, DBC
from opendbc.car.vehicle_model import VehicleModel
from opendbc.sunnypilot.car.tesla.values import TeslaFlagsSP
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
from openpilot.tesla import manifest as mf, panda_rules, plugins
from openpilot.tesla.plugin_params import ParamsView

if __package__:
  from . import reference
else:
  import reference

PLUGIN = Path(__file__).resolve().parents[1]
O_SHA = "65cc8fdbcb6aaaf0533ba296c9e6fe5422a696e8"
ATOL = 1e-10  # deg, unquantized float comparison; CAN is separately checked to 0.05 deg.


class CoreParams:
  def __init__(self):
    self.values = {"TeslaCoopSteering": True}

  def get_bool(self, key, **kwargs):
    return bool(self.values.get(key, False))

  def get(self, key, **kwargs):
    return self.values.get(key)

  def put(self, key, value, **kwargs):
    self.values[key] = value


def state(speed=15.0, torque=0.0, angle=0.0):
  return structs.CarState.new_message(vEgo=speed, vEgoRaw=speed, steeringTorque=torque,
                                      steeringAngleDeg=angle, steeringPressed=abs(torque) > 0.5)


def args(cs, vm, desired=0.0, active=True):
  return (active, cs, vm, NS(roll=0.0, angleOffsetDeg=0.0), False, desired, None, False, 0.1)


def scripted_inputs():
  # Each row is a 20 ms sample, delivered twice to the 100 Hz hook.
  rows = []
  for speed in (0.0, 0.1, 1.0, 5.0, 15.0, 30.0, 40.0):
    for torque in (0.0, 0.49, 0.5, 0.51, 1.5, 2.5, -2.5, -0.5, 0.0):
      for i in range(30):
        rows.append(dict(speed=speed, torque=torque, angle=0.0,
                         desired=0.01 * math.sin(i / 12), active=i >= 3))
  return rows


def run(output):
  output.mkdir(parents=True, exist_ok=True)
  (output / "inputs").mkdir(exist_ok=True)
  rows = scripted_inputs()
  (output / "inputs/sequence.json").write_text(json.dumps(rows, indent=2) + "\n")
  frames, checks, metrics = [], [], {}
  cp = get_safety_CP()
  cp.dashcamOnly = False  # synthetic, supported 3-bit test vehicle; no fingerprint claim.
  vm = VehicleModel(cp)
  flags = TeslaFlagsSP.COOP_STEERING.value | TeslaFlagsSP.HAS_VEHICLE_BUS.value
  core = CoreParams()
  with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
    installed = Path(tmp) / "installed"
    shutil.copytree(PLUGIN, installed / PLUGIN.name, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    stack.enter_context(patch.object(mf, "INSTALL_DIR", installed))
    classes, errors, specs = plugins._discover()
    assert PLUGIN.name not in errors, errors
    stack.enter_context(patch.object(plugins, "PLUGINS", classes))
    stack.enter_context(patch.object(plugins, "INSTALLED_SPECS", specs))
    faults = []
    stack.enter_context(patch.object(plugins, "record_fault", lambda *a: faults.append(str(a))))
    params = ParamsView(core, specs)
    cls = next(c for c in classes if c.NAME == PLUGIN.name)
    assert cls.MANIFEST.rules == []
    assert cls.MANIFEST.params == {"XpTeslaMixedControl": {"type": "BOOL", "default": "0"}}
    sp_cp = structs.CarParamsSP(flags=flags)
    ci = CarInterface(cp, sp_cp)

    def controller():
      return plugins.lateral_controller(cp, sp_cp, ci, core, LatControlAngle(cp, sp_cp, ci, DT_CTRL))

    from opendbc.can import CANPacker
    from opendbc.car.tesla.values import CANBUS, TeslaFlags
    from openpilot.cereal import custom, log
    from openpilot.selfdrive.car.car_events import CarEvents
    from openpilot.selfdrive.selfdrived.events import Events
    from openpilot.selfdrive.selfdrived.state import StateMachine
    from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
    from openpilot.tesla import core_host

    event_cp = get_safety_CP()
    event_cp.flags = int(event_cp.flags & ~TeslaFlags.MISSING_DAS_SETTINGS)
    event_cp.pcmCruise = True
    event_ci = CarInterface(event_cp, sp_cp)
    event_cases = []
    for case, present, enabled in (("M1", True, False), ("M2", True, True), ("M3", False, False)):
      params.put_bool("XpTeslaMixedControl", enabled)
      parsers = event_ci.CS.get_can_parsers(event_cp, sp_cp)
      msg = CANPacker(DBC[cp.carFingerprint][Bus.party]).make_can_msg("DAS_settings", CANBUS.autopilot_party,
                                                                  {"DAS_autosteerEnabled": 1})
      _ = parsers[Bus.ap_party].vl["DAS_settings"]
      parsers[Bus.ap_party].update([(0, [msg])])
      cs, _ = event_ci.CS.update(parsers)
      cs.gearShifter, cs.cruiseState.available, cs.cruiseState.enabled = structs.CarState.GearShifter.drive, True, True
      cs.doorOpen = cs.seatbeltUnlatched = cs.steerFaultPermanent = False
      d = NS(CP=event_cp, CP_SP=sp_cp, params=params, sm={"pandaStates": []}, events=Events(), events_sp=EventsSP(),
             state_machine=StateMachine(), mads=None)
      def update_events(CS):
        for e in CarEvents(event_cp).update(CS, structs.CarState(), structs.CarControl()).names:
          d.events.add(e)
      d.update_events = update_events
      selected = classes if present else tuple(c for c in classes if c.NAME != cls.NAME)
      with patch.object(plugins, "PLUGINS", selected):
        core_host.install(d)
        with patch.object(params, "get_bool", wraps=params.get_bool) as reads:
          d.update_events(cs)
          engaged, active = d.state_machine.update(d.events)
        assert reads.call_count == 0, f"M4: {reads.call_count} per-frame Params reads in {case}"
      event_present = d.events.has(log.OnroadEvent.EventName.invalidLkasSetting)
      event_cases.append(dict(case=case, stock_autosteer=1, invalid_lkas=cs.invalidLkasSetting,
                             plugin_present=present, switch=enabled, event_present=event_present, engaged=engaged,
                             passed=bool(cs.invalidLkasSetting and event_present != enabled and engaged == enabled and active == enabled
                                         and not d.events_sp.has(custom.OnroadEventSP.EventName.xpilotPluginFault))))
    (output / "event-filter.json").write_text(json.dumps(event_cases, indent=2) + "\n")
    assert all(c["passed"] for c in event_cases), event_cases
    assert cls.CATEGORY == "controller" and cls.CLAIMS == ("role:lateral.controller", "event.filter")
    checks.append("M1-M4: zero per-frame Params reads; real DAS_settings CAN -> true CarState -> SP events -> shared filter -> SP engagement")
    params.put_bool("XpTeslaMixedControl", False)

    baseline = LatControlAngle(cp, sp_cp, ci, DT_CTRL)
    disabled = controller()
    assert disabled.__class__ is LatControlAngle
    plugins.CarPlugins(cp, core).setup(sp_cp)
    assert core.values.pop("XpilotPandaRules") == panda_rules.param(panda_rules.encode_rules([]))
    assert sp_cp.flags == flags
    for row in rows:
      a = args(state(row["speed"], row["torque"]), vm, row["desired"], row["active"])
      x, y = disabled.update(*a), baseline.update(*a)
      assert x[:2] == y[:2] and x[2].to_dict() == y[2].to_dict()
    checks.append("disabled: SP output/log bit-identical; COOP unchanged")

    params.put_bool("XpTeslaMixedControl", True)
    plugins.CarPlugins(cp, core).setup(sp_cp)
    assert core.values.pop("XpilotPandaRules") == panda_rules.param(panda_rules.encode_rules([]))
    assert sp_cp.flags == flags & ~TeslaFlagsSP.COOP_STEERING.value
    assert sp_cp.safetyParam == 0 and core.values == {"TeslaCoopSteering": True}
    checks.append("enabled setup: only COOP bit cleared; persistent TeslaCoopSteering untouched")

    ctl = controller()
    ref = reference.CoopSteeringCarController()
    ref_cp = structs.CarParamsSP(flags=TeslaFlagsSP.COOP_STEERING.value)
    pre_last = last = 0.0
    max_diff = 0.0
    ports = [CarInterface(cp, sp_cp), CarInterface(cp, sp_cp)]
    for phase, port in enumerate(ports):
      port.CC.frame = phase
    port_diff = 0.0
    for step, row in enumerate(rows):
      cs = state(row["speed"], row["torque"], row["angle"])
      a = args(cs, vm, row["desired"], row["active"])
      if not row["active"]:
        ctl.reset()  # controlsd calls reset before every inactive update.
      desired = baseline.update(*a)[1]
      pre_last = apply_steer_angle_limits_vm(desired, pre_last, cs.vEgoRaw, cs.steeringAngleDeg,
                                            row["active"], CarControllerParams, vm)
      expected = ref.update(pre_last, row["active"], ref_cp, NS(out=cs), vm).steeringAngleDeg
      first, held = ctl.update(*a)[1], ctl.update(*a)[1]
      max_diff = max(max_diff, abs(first - expected), abs(held - expected))
      assert abs(first - expected) <= ATOL and first == held, (step, row, first, held, expected)
      last = apply_steer_angle_limits_vm(first, last, cs.vEgoRaw, cs.steeringAngleDeg,
                                        row["active"], CarControllerParams, vm)
      assert abs(last - expected) <= ATOL
      assert abs(ctl.plugin.coop.angle_override - ref.angle_override) <= ATOL
      for phase, port in enumerate(ports):
        port.CS.out, port.CS.hands_on_level = cs, 0
        cc = structs.CarControl.new_message(latActive=row["active"])
        cc.actuators.steeringAngleDeg = first
        for half in range(2):
          sent, cans = port.apply(cc.as_reader(), structs.CarControlSP(), (step * 2 + half + 1) * 10_000_000)
          if any(m[0] == 0x488 for m in cans):
            diff = abs(sent.steeringAngleDeg - expected)
            port_diff = max(port_diff, diff)
            assert diff <= 3e-5, (phase, step, diff)  # CarControl/carOutput are float32, up to 360 deg.
      if not row["active"]:
        assert ctl.plugin.coop.angle_override == 0
      frames.append(dict(case="reference", step=step, **row, baseline=desired,
                         pre_limited=pre_last, expected=expected, target=first, held=held,
                         override=ctl.plugin.coop.angle_override, sent=last))
    metrics["reference_max_abs_deg"] = max_diff
    metrics["car_port_reference_max_abs_deg"] = port_diff
    checks.append("50/100 Hz reference, deadzone, sign, standstill, saturation, inactive/resume")

    # Deadzone and sign, independent of the reference oracle.
    for torque in (-0.5, 0.0, 0.5, -1.5, 1.5):
      ctl = controller()
      ctl.update(*args(state(), vm, active=False))
      for _ in range(100):
        angle = ctl.update(*args(state(torque=torque), vm))[1]
      assert angle == 0 if abs(torque) <= 0.5 else angle * torque > 0
    # A torque matching the existing override holds it rather than integrating forever.
    ctl = controller()
    ctl.update(*args(state(), vm, active=False))
    for _ in range(100):
      ctl.update(*args(state(torque=1.5), vm))
    override = ctl.plugin.coop.angle_override
    gain = reference.get_override_torque_to_angle(15, vm, 2.0)
    holding = 0.5 + override / gain
    for _ in range(100):
      ctl.update(*args(state(torque=holding), vm))
    metrics["holding_float32_drift_deg"] = abs(ctl.plugin.coop.angle_override - override)
    assert metrics["holding_float32_drift_deg"] <= 1e-6  # CarState stores steeringTorque as float32.
    # Saturation anti-windup must leave no hidden override when the planner uses all authority.
    ctl = controller()
    for _ in range(800):
      ctl.update(*args(state(speed=30, torque=2.5), vm, desired=-0.1))
    assert abs(ctl.plugin.coop.angle_override) <= ATOL
    ctl.reset()
    assert ctl.plugin.coop.angle_override == 0
    ctl.update(*args(state(angle=12), vm, active=False))
    resumed = ctl.update(*args(state(angle=12), vm, desired=-0.1))[1]
    assert abs(resumed - 12) <= 300 * 0.02**2 + ATOL
    checks.append("independent deadzone/sign/holding/anti-windup/resume assertions")

    # Safety limits must not change when controlsd's learned model changes.
    ctl = controller()
    learned = VehicleModel(cp)
    learned.update_params(0.3, 30)
    assert ctl.plugin.safety_vm.sR == get_safety_CP().steerRatio
    for _ in range(100):
      ctl.update(*args(state(torque=1.5), learned))
    assert ctl.plugin.safety_vm.sR == get_safety_CP().steerRatio
    checks.append("fixed get_safety_CP VM, independent of learned VM")

    # Real host guard -> Tesla controller -> decoded CAN, both possible send phases.
    for phase in (0, 1):
      for failure in ("exception", "nan_output", "inf_output", "steeringTorque", "steeringAngleDeg", "vEgo", "vEgoRaw"):
        ctl = controller()
        port = CarInterface(cp, sp_cp)
        port.CC.frame = phase
        parser = CANParser(DBC[cp.carFingerprint][Bus.party], [("DAS_steeringControl", 50)], 0)
        previous = 0.0
        target_before = 0.0
        for tick in range(130):
          cs = state(torque=1.5 if tick < 100 else 0.0)
          a = args(cs, vm, active=tick >= 4)
          if tick == 101:  # corrupt a held 10 ms frame, after building a nonzero override.
            assert abs(target_before) > 1
            if failure == "exception":
              def fail(*a):
                raise RuntimeError("injected")
              ctl.plugin.update = fail
            elif failure.endswith("output"):
              ctl.plugin.update = lambda *a, value=float(failure.split("_")[0]): (0, value, None)
            else:
              setattr(cs, failure, float("nan"))
          cc = structs.CarControl.new_message(latActive=tick >= 4)
          raw_target = ctl.update(*a)[1]
          # Reproduce controlsd's final non-finite actuator sanitation (state_control lines 138-146).
          # The real guard alone can return NaN from SP too when vEgo itself is NaN.
          cc.actuators.steeringAngleDeg = raw_target if math.isfinite(raw_target) else 0.0
          if tick == 101:
            assert not ctl._plugins and cc.actuators.steeringAngleDeg == 0, (phase, failure, cc.actuators.steeringAngleDeg, ctl._plugins)
            metrics[f"fallback_target_step_{phase}_{failure}"] = abs(target_before)
          if tick >= 101:
            assert not ctl._plugins and cc.actuators.steeringAngleDeg == 0
          port.CS.out = cs
          port.CS.hands_on_level = 0
          # Corrupt input is a controller-only boundary test. Replay valid sensor data to the car port.
          port.CS.out = state(torque=cs.steeringTorque if math.isfinite(cs.steeringTorque) else 0)
          sent, cans = port.apply(cc.as_reader(), structs.CarControlSP(), (tick + 1) * 10_000_000)
          steering = [m for m in cans if m[0] == 0x488]
          if steering:
            parser.update([((tick + 1) * 10_000_000, steering)])
            decoded = -parser.vl["DAS_steeringControl"]["DAS_steeringAngleRequest"]
            assert abs(decoded - sent.steeringAngleDeg) <= 0.050001
            assert parser.vl["DAS_steeringControl"]["DAS_steeringControlType"] == (1 if cc.latActive else 0)
            limit = min(5, get_max_angle_delta_vm(15, vm, CarControllerParams))
            assert abs(sent.steeringAngleDeg - previous) <= limit + 1e-6, (tick, previous, sent.steeringAngleDeg, limit)
            if tick in (101, 102):
              metrics[f"fallback_sent_step_{phase}_{failure}"] = abs(sent.steeringAngleDeg - previous)
            previous = sent.steeringAngleDeg
          frames.append(dict(case=f"fallback_{phase}_{failure}", tick=tick, target=cc.actuators.steeringAngleDeg,
                             raw_target=raw_target if math.isfinite(raw_target) else None,
                             sanitized=not math.isfinite(raw_target), sent=sent.steeringAngleDeg,
                             tx=bool(steering), failed=not bool(ctl._plugins)))
          target_before = cc.actuators.steeringAngleDeg
    assert len(faults) == 14, faults
    checks.append("real host permanent fallback; first fallback TX obeys unchanged VM rate limit, both phases")
    assert core.values == {"TeslaCoopSteering": True}
    (output / "params.json").write_text(json.dumps({"core": core.values, "plugin": {"XpTeslaMixedControl": True},
                                                  "CP_SP": {"flags": sp_cp.flags, "safetyParam": sp_cp.safetyParam}}, indent=2) + "\n")

  (output / "frames.jsonl").write_text("".join(json.dumps(f) + "\n" for f in frames))
  def head(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
  system = Path(plugins.__file__).resolve().parents[3]
  source = dict(reference=O_SHA, reference_sha256=hashlib.sha256((PLUGIN / "tests/reference.py").read_bytes()).hexdigest(),
                system=head(system), plugin=head(PLUGIN), python=sys.version, platform=platform.platform(),
                source_sha256={str(p.relative_to(PLUGIN)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(PLUGIN.rglob("*")) if p.is_file() and "__pycache__" not in p.parts})
  (output / "source.json").write_text(json.dumps(source, indent=2) + "\n")
  result = dict(errors=[], checks=checks, metrics=metrics, float_tolerance_deg=ATOL,
                not_verified=["live controlsd/card scheduling, dropped/reordered/stale messages", "Panda/libsafety/firmware",
                              "vehicle dynamics, physical CAN wiring, closed-course comfort", "stock Autosteer ownership/handoff (path 2)"])
  (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
  (output / "commands.txt").write_text(f"PYTHONPATH={system}:{system}/msgq_repo:{system}/opendbc_repo "
                                      f"{sys.executable} {PLUGIN}/tests/e2e.py --output {output}\n")
  sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(output)}" for p in sorted(output.rglob("*"))
          if p.is_file() and p.name != "SHA256SUMS"]
  (output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
  return result


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--output", type=Path, required=True)
  result = run(parser.parse_args().output.resolve())
  print(json.dumps(result, indent=2))
