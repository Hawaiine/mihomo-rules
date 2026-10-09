#!/usr/bin/env python3
"""重排 providers/nodes/<协议>/ 下的节点模板文件顺序。

用法：`python3 scripts/reorder_node_files.py`

仓库根按以下顺序解析（禁止硬编码机器专属绝对路径）：
1. 环境变量 ``MIHOMO_RULES_REPO``（显式指定，优先级最高）；
2. 本脚本所在的仓库根目录（``scripts/`` 的上一级）。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
repo = Path(os.environ.get('MIHOMO_RULES_REPO', str(ROOT)))
nodes = repo / 'providers' / 'nodes'

if not nodes.is_dir():
    raise SystemExit(
        f'找不到节点模板目录: {nodes}\n'
        '请从仓库根目录运行本脚本，或设置 MIHOMO_RULES_REPO 指向仓库根目录。'
    )

# 定义每个目录的期望顺序
order = {
    'shadowsocks': ['shadowsocks-base', 'shadowsocks-2022', 'shadowsocks-obfs', 'shadowsocks-v2ray-plugin'],
    'vmess': ['vmess-tcp', 'vmess-ws', 'vmess-ws-tls', 'vmess-h2', 'vmess-grpc'],
    'vless': ['vless-ws', 'vless-ws-tls', 'vless-grpc', 'vless-reality', 'vless-reality-vision'],
    'trojan': ['trojan-base', 'trojan-ws', 'trojan-ss-aead', 'trojan-reality'],
    'hysteria': ['hysteria-hy1', 'hysteria-hy1-portjump', 'hysteria-hy2', 'hysteria-hy2-optimized', 'hysteria-hy2-portjump'],
    'tuic': ['tuic-v4', 'tuic-v5'],
    'wireguard': ['wireguard-wireguard'],
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
