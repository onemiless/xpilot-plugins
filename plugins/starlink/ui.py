"""Native Starlink controls. Only labels are translated; node identities stay intact."""
import json
import queue
import threading
import time

import pyray as rl

from openpilot.common.qrcode import make_texture
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.keyboard import Keyboard
from openpilot.system.ui.widgets.label import gui_label
from openpilot.system.ui.widgets.list_view import button_item, text_item
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.tesla.plugins import PluginMsgs, view
from openpilot.tesla.ui.pages.base import ListPage, Header
from openpilot.tesla.ui.tile import Tile
from openpilot.tesla.ui.tiles import TileSpec

from . import client, session


def allowed():
  return ui_state.is_offroad() and not ui_state.engaged


def label(name):
  # Decorative non-BMP emoji may lack a glyph; preserve the raw name for API calls.
  return ''.join(c for c in str(name) if c.isprintable() and ord(c) < 65536).strip()


class AsyncPage(ListPage):
  def __init__(self):
    super().__init__()
    self.busy = False
    self.results = queue.Queue()
    self.message = ''

  def run(self, operation, value=None):
    if self.busy:
      return
    if operation != 'status' and not allowed():
      self.message = tr('Park before changing the VPN')
      return
    self.busy = True
    if operation != 'status':
      self.message = tr('Working...')

    def worker():
      try:
        if operation in ('pair', 'import'):
          client.ensure_core()
          client.call('preflight')
        result = client.pair() if operation == 'pair' else client.call(operation, value)
      except Exception as error:
        result = {'error': str(error)}
      self.results.put((operation, result))
    threading.Thread(target=worker, daemon=True).start()

  def _update_state(self):
    while not self.results.empty():
      operation, result = self.results.get()
      self.busy = False
      if result.get('error'):
        self.message = tr(result['error'])
      else:
        self.receive(operation, result)

  def receive(self, operation, result):
    pass


class StarlinkPage(AsyncPage):
  def __init__(self):
    super().__init__()
    self.state = {}
    self.last = 0.
    self.keyboard = Keyboard(max_text_size=8192, min_text_size=1)
    self.add(
      Header('Starlink'),
      text_item(tr('Connection'), self.connection),
      text_item(tr('Selected node'), lambda: label((self.state.get('groups') or [{}])[0].get('now', '')) or tr('Not selected')),
      text_item(tr('Saved nodes'), lambda: str(self.state.get('nodes', 0))),
      button_item(tr('Start VPN'), tr('START'), callback=lambda: self.connect(True), enabled=lambda: allowed() and not self.busy),
      button_item(tr('Stop VPN'), tr('STOP'), callback=lambda: self.connect(False), enabled=lambda: allowed() and not self.busy),
      button_item(tr('Import subscription or node'), tr('IMPORT'), callback=self.edit, enabled=lambda: allowed() and not self.busy),
      button_item(tr('Add from phone'), tr('PAIR'), callback=lambda: self.run('pair'), enabled=lambda: allowed() and not self.busy),
      button_item(tr('Nodes and latency'), tr('OPEN'), callback=lambda: self.open(tr('Nodes and latency'), NodePage()),
                  enabled=lambda: allowed() and not self.busy),
      button_item(tr('Test GitHub access'), tr('TEST'), callback=lambda: self.run('test'), enabled=lambda: allowed() and not self.busy),
      text_item(tr('Status'), lambda: self.message),
      text_item(tr('Internet routing'), tr('Public IPv4 uses the VPN; local networks stay direct.')),
    )

  def connection(self):
    return tr('VPN connected') if self.state.get('gateway') else tr('Core running; routes pending') if self.state.get('running') else tr('VPN stopped')

  def connect(self, enabled):
    if not allowed():
      return
    session.set_requested(enabled)
    try:
      view(ui_state.params).put_bool('ExtStarlinkConnect', enabled, block=True)
    except Exception:
      session.clear()
      raise
    self.message = tr('Starting VPN...') if enabled else tr('Stopping VPN...')
    self.last = 0.

  def edit(self):
    self.keyboard.reset()
    self.keyboard.set_title(tr('Subscription / node link / Clash YAML'))
    self.keyboard.set_callback(lambda result: self.run('import', self.keyboard.text.strip()) if result == DialogResult.CONFIRM else None)
    gui_app.push_widget(self.keyboard)

  def receive(self, operation, result):
    if operation == 'pair':
      self.open(tr('Phone pairing'), PairPage(result['url'], result['expires']))
    elif operation == 'test':
      self.message = tr('GitHub reached through the VPN') if result.get('ok') else tr('Connection test failed ({code})').format(code=result.get('http_code') or '-')
    else:
      self.state = result
      if operation != 'status':
        self.message = tr('Configuration saved') if operation == 'import' else tr('Operation completed')
    self.last = time.monotonic()

  def _update_state(self):
    super()._update_state()
    if not self.busy and time.monotonic() - self.last > 5:
      self.last = time.monotonic()
      self.run('status')

  def show_event(self):
    super().show_event()
    self.last = 0.


class NodePage(AsyncPage):
  def __init__(self):
    super().__init__()
    self.rows = []
    self.rebuild()
    self.run('nodes')

  def rebuild(self):
    self._scroller = Scroller([], spacing=0, line_separator=True)
    ready = lambda: allowed() and not self.busy
    self.add(
      button_item(tr('Refresh nodes'), tr('REFRESH'), callback=lambda: self.run('nodes'), enabled=ready),
      button_item(tr('Test node latency'), tr('TEST'), callback=lambda: self.run('measure'), enabled=ready),
      button_item(tr('Test and select fastest'), tr('SELECT'), callback=lambda: self.run('fastest'), enabled=ready),
      text_item(tr('Status'), lambda: self.message),
      text_item(tr('Latency test'), tr('Tests up to 128 nodes; latency is not download bandwidth.')),
    )
    for index, row in enumerate(self.rows):
      name = row['name']
      display = label(name) or tr('Node {number}').format(number=index + 1)
      delay = f"{row['delay']} ms" if row.get('delay') else tr('Unavailable') if row.get('tested') else tr('Not tested')
      action = tr('CURRENT') if row.get('selected') else tr('SELECT')
      self.add(button_item(display + '  ' + delay, action, callback=lambda n=name: self.run('select', n), enabled=ready))
    self._scroller.show_event()

  def receive(self, operation, result):
    self.rows = result.get('nodes', [])
    self.message = tr('Select a node; lowest latency does not guarantee highest bandwidth.')
    self.rebuild()


class PairPage(Widget):
  def __init__(self, url, expires):
    super().__init__()
    self.expires = expires
    self.texture = make_texture(url)

  def _render(self, rect):
    side = min(540, rect.height - 180)
    gui_label(rl.Rectangle(rect.x + 30, rect.y + 20, rect.width - side - 90, 200),
              tr('Connect your phone to the same private Wi-Fi and scan to import a subscription. The page expires after ten minutes or one successful import.'), 42)
    gui_label(rl.Rectangle(rect.x + 30, rect.y + 260, rect.width - side - 90, 80),
              tr('Pairing expired') if time.time() >= self.expires else tr('Keep the pairing link private.'), 40)
    if time.time() < self.expires:
      rl.draw_texture_pro(self.texture, rl.Rectangle(0, 0, self.texture.width, self.texture.height),
                          rl.Rectangle(rect.x + rect.width - side - 30, rect.y + 40, side, side),
                          rl.Vector2(0, 0), 0, rl.WHITE)

  def __del__(self):
    if getattr(self, 'texture', None) and self.texture.id:
      rl.unload_texture(self.texture)


class StarlinkTile(Tile):
  SPEC = TileSpec('starlink', 'Starlink', sizes=('small', 'medium', 'wide'), show='offroad', hz=1, order=95)
  PAGE = StarlinkPage

  def __init__(self):
    super().__init__()
    self.messages = PluginMsgs()

  def state(self, sm):
    try:
      state = self.messages.fresh('network.vpn').get('starlink', {})
      return bool(state.get('gateway'))
    except (OSError, ValueError):
      return False

  def draw(self, rect, size, state):
    self.draw_value(rect, tr('Connected') if state else tr('Stopped'))
