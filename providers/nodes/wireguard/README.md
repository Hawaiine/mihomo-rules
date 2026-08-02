# WireGuard 协议

> 现代、快速的 VPN 协议，基于 UDP。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `wireguard` |
| `ip` | list | 虚拟接口 IP 地址 (必填) |
| `private-key` | string | 本地私钥 (Base64) |
| `public-key` | string | 对等方公钥 |
| `preshared-key` | string | 预共享密钥 |

## 变体说明

| 文件 | 说明 |
|------|------|
| `wireguard-wireguard.yaml` | 标准 WireGuard 配置 |
| `wireguard-tunnel-http.yaml` | HTTP 隧道配置 |
| `wireguard-tunnel-socks5.yaml` | SOCKS5 隧道配置 |

## 注意事项

- `ip` 和 `ipv6` 是必填项
- 私钥必须是 Base64 编码
- 支持 tunnel 模式用于特殊场景
