# VMess 是 V2Ray 的核心传输协议，基于 HTTP/2 和 WebSocket 等传输层，支持多种网络环境。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `uuid` | string | 用户 UUID |
| `alter-id` | int | 附加 ID，用于生成多个子密钥 |
| `cipher` | string | 加密方式：`auto`、`aes-128-gcm`、`chacha20-ietf` |
| `network` | string | 传输方式：`tcp`、`ws`、`grpc`、`h2` |
| `tls` | bool | 是否启用 TLS |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: vmess-ws-tls
  type: yaml
  path: providers/nodes/vmess/vmess-ws-tls.yaml
```
