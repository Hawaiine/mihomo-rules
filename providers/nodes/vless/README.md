# VLESS 协议

> VMess 的改进版，去除 alter-id，支持 XTLS 和 REALITY。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `vless` |
| `uuid` | string | 用户 UUID |
| `tls` | bool | 是否启用 TLS |
| `network` | string | 传输方式: `tcp`, `ws`, `grpc` |
| `reality-opts` | object | REALITY 参数: `public-key`, `short-id` |
| `flow` | string | 流控: `xtls-rprx-vision` |

## 变体说明

| 文件 | 说明 |
|------|------|
| `vless-reality.yaml` | REALITY 抗封锁 (推荐) |
| `vless-reality-vision.yaml` | REALITY + XTLS Vision |
| `vless-ws-tls.yaml` | WebSocket + TLS |
| `vless-grpc.yaml` | gRPC 传输 |

## 注意事项

- **REALITY**: 无需证书，抗封锁能力最强
- **servername**: 回退域名，建议使用大站如 `yahoo.com`
- **fingerprint**: 建议设为 `random` 对抗检测
