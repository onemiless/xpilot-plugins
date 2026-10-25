"""Onroad LAN beacon and scoped discovery. No file or control HTTP endpoints."""
import ctypes
from contextlib import ExitStack
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import socket
import struct
import subprocess
import sys
import threading


def scope(interface):
  if sys.platform == 'darwin':
    address = subprocess.check_output(['ipconfig', 'getifaddr', interface], text=True).strip()
    mask = subprocess.check_output(['ipconfig', 'getoption', interface, 'subnet_mask'], text=True).strip()
  else:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      request = struct.pack('256s', interface.encode())
      address = socket.inet_ntoa(fcntl.ioctl(sock, 0x8915, request)[20:24])
      mask = socket.inet_ntoa(fcntl.ioctl(sock, 0x891b, request)[20:24])
  return address, ipaddress.IPv4Network(f'{address}/{mask}', strict=False)


def discover(interface='wlan0'):
  _, subnet = scope(interface)
  if sys.platform == 'darwin':
    # Bonjour command tools are confined to the Mac validation path.
    process = subprocess.Popen(['dns-sd', '-B', '_bigmodel._tcp', 'local.'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
      output, _ = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
      process.terminate()
      output, _ = process.communicate()
    names = {line.split('local.', 1)[1].split('_bigmodel._tcp.', 1)[-1].strip()
             for line in output.splitlines() if 'Add' in line and '_bigmodel._tcp.' in line}
    peers = set()
    for name in names:
      process = subprocess.Popen(['dns-sd', '-L', name, '_bigmodel._tcp', 'local.'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
      try:
        resolved, _ = process.communicate(timeout=1)
      except subprocess.TimeoutExpired:
        process.terminate()
        resolved, _ = process.communicate()
      for line in resolved.splitlines():
        if 'can be reached at ' in line:
          target = line.split('can be reached at ', 1)[1].split(' ', 1)[0]
          host, port = target.rsplit(':', 1)
          for info in socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM):
            address = info[4][0]
            if ipaddress.IPv4Address(address) in subnet and int(port) == 7070:
              peers.add((name, address, int(port)))
    return sorted(peers)
  process = subprocess.Popen(['avahi-browse', '-rp', '_bigmodel._tcp'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
  try:
    output, _ = process.communicate(timeout=2)
  except subprocess.TimeoutExpired:
    process.terminate()
    output, _ = process.communicate()
  peers = set()
  for line in output.splitlines():
    fields = line.split(';')
    if len(fields) >= 9 and fields[:3] == ['=', interface, 'IPv4'] and fields[4] == '_bigmodel._tcp':
      address, port = fields[7:9]
      if ipaddress.IPv4Address(address) in subnet and int(port) == 7070:
        peers.add((fields[3], address, int(port)))
  return sorted(peers)


class Beacon:
  def __init__(self, address):
    self.address, self.requests = address, 0

  def __enter__(self):
    self.cleanup = ExitStack()
    try:
      owner = self
      class Root(BaseHTTPRequestHandler):
        def do_GET(self):
          if self.path == '/':
            owner.requests += 1
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
          else:
            self.send_error(404)
        def log_message(self, *args):
          pass
      self.server = HTTPServer((self.address, 0), Root)
      self.cleanup.callback(self.server.server_close)
      self.server.timeout = 1
      self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
      self.thread.start()
      self.cleanup.callback(self.thread.join, 2)
      self.cleanup.callback(self.server.shutdown)
      port = self.server.server_port
      if sys.platform == 'darwin':
        self.bonjour = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        self.reference = ctypes.c_void_p()
        code = self.bonjour.DNSServiceRegister(ctypes.byref(self.reference), 0, socket.if_nametoindex('en0'),
                                             b'xpilot lean mici', b'_lanlink._tcp', b'local.', None,
                                             socket.htons(port), 0, None, None, None)
        if code:
          raise OSError(f'DNSServiceRegister: {code}')
        self.cleanup.callback(self.bonjour.DNSServiceRefDeallocate, self.reference)
      else:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        self.dbus = open_dbus_connection(bus='SYSTEM')
        self.cleanup.callback(self.dbus.close)
        server = DBusAddress('/', bus_name='org.freedesktop.Avahi', interface='org.freedesktop.Avahi.Server')
        path = self.dbus.send_and_get_reply(new_method_call(server, 'EntryGroupNew'), timeout=1).body[0]
        self.group = DBusAddress(path, bus_name='org.freedesktop.Avahi', interface='org.freedesktop.Avahi.EntryGroup')
        body = (socket.if_nametoindex('wlan0'), 0, 0, 'xpilot lean mici', '_lanlink._tcp', '', '', port, [])
        self.dbus.send_and_get_reply(new_method_call(self.group, 'AddService', 'iiussssqaay', body), timeout=1)
        self.dbus.send_and_get_reply(new_method_call(self.group, 'Commit'), timeout=1)
      return self
    except BaseException:
      self.cleanup.close()
      raise

  def __exit__(self, *args):
    self.cleanup.close()
