# WireGuard 是一个现代、快速的 VPN 协议，基于 UDP，提供低延迟和高吞吐量。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `ip` | list | 虚拟接口 IP 地址（必须填写） |
| `private-key` | string | 本地私钥 (Base64) |
| `public-key` | string | 对等方公钥 |
| `preshared-key` | string | 预共享密钥 (可选) |
| `allowed-ips` | list | 允许的 IP 段 |
| `dialer-proxy` | string | 通过指定代理连接 WG 端点 |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: wg-basic
  type: yaml
  path: providers/nodes/wireguard/wireguard-wireguard.yaml
```
