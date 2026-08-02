# VMess 协议

> V2Ray 的核心传输协议，支持多种传输方式。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `vmess` |
| `uuid` | string | 用户 UUID |
| `alter-id` | int | 附加 ID，建议设为 0 |
| `cipher` | string | 加密方式: `auto` (推荐), `aes-128-gcm`, `chacha20-ietf` |
| `network` | string | 传输方式: `tcp`, `ws`, `grpc`, `h2` |

## 变体说明

| 文件 | 说明 |
|------|------|
| `vmess-tcp.yaml` | 基础 TCP 传输 |
| `vmess-ws-tls.yaml` | WebSocket + TLS (抗封锁) |
| `vmess-grpc.yaml` | gRPC 传输 |
| `vmess-h2.yaml` | HTTP/2 传输 |

## 注意事项

- **alter-id**: 建议设为 0，过高会降低安全性
- **cipher**: 推荐使用 `auto`，自动选择最优加密方式
- **ws-opts**: WebSocket 配置中 `path` 和 `headers.Host` 需与服务器一致
