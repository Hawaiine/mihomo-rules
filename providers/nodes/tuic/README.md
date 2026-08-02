# TUIC 协议

> 基于 QUIC 协议的代理协议，低延迟、高吞吐。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `tuic` |
| `token` | string/list | TUIC v5 认证 token (支持多 token) |
| `uuid` / `password` | string | TUIC v4 认证凭据 |
| `alpn` | list | TLS ALPN，通常包含 `h3` |
| `congestion-controller` | string | 拥塞控制: `cubic`, `bbr` |

## 变体说明

| 文件 | 说明 |
|------|------|
| `tuic-v5.yaml` | TUIC v5 基础配置 |
| `tuic-v5-multi.yaml` | TUIC v5 多 Token 配置 |
| `tuic-v4.yaml` | TUIC v4 配置 |

## 注意事项

- **v5** 使用 token 认证，**v4** 使用 uuid + password
- 建议使用 `h3` ALPN
- `congestion-controller` 推荐 `bbr`
