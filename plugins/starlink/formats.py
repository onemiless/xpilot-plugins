"""Pure subscription/node parsing and managed configuration normalization."""
import base64
import json
import urllib.parse
import yaml

def decode64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)).decode()

def node_uri(value):
    if value.startswith('vmess://'):
        d = json.loads(decode64(value[8:]))
        n = {'name': d.get('ps') or 'VMess', 'type': 'vmess', 'server': d['add'], 'port': int(d['port']), 'uuid': d['id'], 'alterId': int(d.get('aid') or 0), 'cipher': d.get('scy') or 'auto', 'tls': d.get('tls') == 'tls'}
        if d.get('net') not in (None, '', 'tcp', 'ws'):
            raise ValueError('Import this VMess transport as YAML')
        if d.get('net') == 'ws':
            n.update(network='ws', **{'ws-opts': {'path': d.get('path') or '/', 'headers': {'Host': d.get('host') or d['add']}}})
        if d.get('sni'):
            n['servername'] = d['sni']
        return n
    if value.startswith('ss://'):
        part = value[5:].split('#', 1)[0]
        if '@' not in part:
            part = decode64(part)
        u = urllib.parse.urlsplit('ss://' + part)
        credentials = urllib.parse.unquote(u.username or '')
        if u.password is not None:
            method, password = (credentials, urllib.parse.unquote(u.password))
        else:
            method, password = decode64(credentials).split(':', 1)
        if u.query:
            raise ValueError('Import SS plugin options as YAML')
        return {'name': urllib.parse.unquote(value.split('#', 1)[1]) if '#' in value else 'Shadowsocks', 'type': 'ss', 'server': u.hostname, 'port': u.port, 'cipher': method, 'password': password}
    u = urllib.parse.urlsplit(value)
    q = dict(urllib.parse.parse_qsl(u.query))
    if u.scheme not in ('vless', 'trojan'):
        raise ValueError('Use a subscription, Clash YAML or SS/VMess/VLESS/Trojan link')
    n = {'name': urllib.parse.unquote(u.fragment) or u.scheme.upper(), 'type': u.scheme, 'server': u.hostname, 'port': u.port or 443}
    if u.scheme == 'vless':
        n.update(uuid=urllib.parse.unquote(u.username or ''), tls=q.get('security', 'tls') != 'none')
    else:
        n.update(password=urllib.parse.unquote(u.username or ''), sni=q.get('sni') or u.hostname)
    if q.get('sni'):
        n['servername' if u.scheme == 'vless' else 'sni'] = q['sni']
    if q.get('fp'):
        n['client-fingerprint'] = q['fp']
    if q.get('flow'):
        n['flow'] = q['flow']
    if q.get('security') == 'reality':
        n['reality-opts'] = {'public-key': q['pbk'], 'short-id': q.get('sid', '')}
    network = q.get('type', 'tcp')
    if network not in ('tcp', 'ws'):
        raise ValueError('Import this transport as YAML')
    if network == 'ws':
        n.update(network='ws', **{'ws-opts': {'path': q.get('path', '/'), 'headers': {'Host': q.get('host') or u.hostname}}})
    return n

def normalize(raw, secret):
    d = yaml.safe_load(raw)
    if isinstance(d, str):
        text = d.strip()
        if '://' not in text:
            try:
                text = decode64(text)
            except Exception:
                raise ValueError('Unrecognized Clash configuration') from None
        d = {'proxies': [node_uri(line.strip()) for line in text.splitlines() if line.strip()]}
    if not isinstance(d, dict) or not (d.get('proxies') or d.get('proxy-providers')):
        raise ValueError('No nodes or providers in configuration')
    for k in ['listeners', 'external-controller-unix', 'external-controller-pipe', 'external-controller-tls', 'external-ui-url', 'external-ui-name', 'authentication', 'iptables', 'routing-mark']:
        d.pop(k, None)
    d.update({'mixed-port': 17890, 'port': 0, 'socks-port': 0, 'redir-port': 0, 'tproxy-port': 0, 'allow-lan': False, 'bind-address': '127.0.0.1', 'external-controller': '127.0.0.1:17891', 'external-ui': '', 'secret': secret, 'tun': {'enable': True, 'device': 'c3mh0', 'stack': 'gvisor', 'auto-route': False, 'auto-detect-interface': False, 'mtu': 1500}, 'dns': {'enable': True, 'listen': '0.0.0.0:17893', 'enhanced-mode': 'redir-host', 'default-nameserver': ['223.5.5.5', '119.29.29.29'], 'proxy-server-nameserver': ['223.5.5.5', '119.29.29.29'], 'nameserver': ['https://1.1.1.1/dns-query#C3-PROXY']}, 'ipv6': False, 'log-level': 'warning', 'find-process-mode': 'off', 'mode': 'global'})
    for kind in ('proxy-providers', 'rule-providers'):
        for i, provider in enumerate(d.get(kind, {}).values()):
            if isinstance(provider, dict):
                provider['path'] = f'./providers/{kind}-{i}.yaml'
    group = {'name': 'C3-PROXY', 'type': 'select', 'proxies': [n['name'] for n in d.get('proxies', [])]}
    if d.get('proxy-providers'):
        group['use'] = list(d['proxy-providers'])
    d['proxy-groups'] = [group]
    d['rules'] = ['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve', 'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve', 'MATCH,C3-PROXY']
    d.pop('rule-providers', None)
    return d
