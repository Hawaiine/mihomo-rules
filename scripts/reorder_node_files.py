#!/usr/bin/env python3
import os
from pathlib import Path

repo = Path('/opt/data/mihomo-rules')
nodes = repo / 'providers' / 'nodes'

# 定义每个目录的期望顺序
order = {
    'shadowsocks': ['shadowsocks-base', 'shadowsocks-2022', 'shadowsocks-obfs', 'shadowsocks-v2ray-plugin'],
    'vmess': ['vmess-tcp', 'vmess-ws', 'vmess-ws-tls', 'vmess-h2', 'vmess-grpc'],
    'vless': ['vless-ws', 'vless-ws-tls', 'vless-grpc', 'vless-reality', 'vless-reality-vision'],
    'trojan': ['trojan-base', 'trojan-ws', 'trojan-ss-aead', 'trojan-reality'],
    'hysteria': ['hysteria-hy1', 'hysteria-hy1-portjump', 'hysteria-hy2', 'hysteria-hy2-optimized', 'hysteria-hy2-portjump'],
    'tuic': ['tuic-v4', 'tuic-v5', 'tuic-v5-multi'],
    'wireguard': ['wireguard-wireguard', 'wireguard-tunnel-http', 'wireguard-tunnel-socks5'],
    'ssh-snell-anytls': ['ssh-ssh', 'ssh-snell', 'ssh-snell-v3', 'ssh-anytls'],
}

for dirname, expected in order.items():
    dirpath = nodes / dirname
    files = [f.stem for f in dirpath.glob('*.yaml') if f.name != 'README.md']
    
    # 重建目录
    for f in files:
        src = dirpath / f'{f}.yaml'
        dst = dirpath / f'.tmp_{f}.yaml'
        if src.exists():
            src.rename(dst)
    
    # 按期望顺序恢复
    for name in expected:
        src = dirpath / f'.tmp_{name}.yaml'
        dst = dirpath / f'{name}.yaml'
        if src.exists():
            src.rename(dst)
    
    # 清理剩余
    for f in dirpath.glob('.tmp_*.yaml'):
        f.unlink()
    
    print(f'{dirname}: {", ".join(expected)}')
