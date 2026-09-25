# TUIC 协议

TUIC 基于 QUIC，客户端版本决定认证方式。mihomo v1.19.31 源码判断规则很直接：有 `uuid` 字段走 v4，只有 `token` 走 v5。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。

## 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定 `tuic` |
| `token` | string | v5 认证。v5 只有这一个凭据 |
| `uuid` | string | v4 认证凭据之一，与 `password` 成对使用 |
| `password` | string | v4 认证凭据，与 `uuid` 成对 |
| `congestion-controller` | string | `cubic` / `bbr`，默认 `bbr` |
| `udp-relay-mode` | string | `native` / `quic`，默认 `native` |
| `alpn` | list | 一般 `h3` |
| `heartbeat-interval` | int | 毫秒 |
| `request-timeout` | int | 毫秒 |
| `max-udp-relay-packet-size` | int | 字节 |

## 版本区分

| 版本 | 字段 |
|------|------|
| v4 | `uuid` + `password` |
| v5 | `token` |

v4 和 v5 的字段不能混用。旧版 `token+uuid` 混填会按 v4 逻辑走，认证直接失败。

## 变体

| 文件 | 说明 |
|------|------|
| `tuic-v5.yaml` | v5，token 认证 |
| `tuic-v4.yaml` | v4，uuid + password |

TUIC 没有多 token 字段，多个凭据要建多个节点。原来的 `tuic-v5-multi.yaml` 内容与 v4 模板重复、文件名声称的多账号认证无源码支持，已删除。

## 注意事项

- `ip` 字段不在 TUIC option 里，别照抄 WireGuard 的写法。
- `ip-version` 等通用字段来自 BasicOption，写不写看需要。
- `skip-cert-verify` 只应在证书确实无法验证时打开。
- TUIC 走 QUIC，注意目标网络对 UDP 和 443 的 QoS。
