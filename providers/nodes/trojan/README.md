# Trojan 协议

Trojan 靠 TLS 承载，密码即认证凭据。mihomo 源码里 `password` 是**字符串**，不是列表。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。

## 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定 `trojan` |
| `password` | string | 单个密码。不是列表，写 YAML 列表会解析失败 |
| `sni` | string | TLS SNI。Trojan 用 `sni`，VLESS/VMess 用 `servername` |
| `ss-opts` | object | Trojan-Go 的 Shadowsocks 封装。字段 `enabled` / `method` / `password` |
| `reality-opts` | object | REALITY 参数，至少 `public-key` |
| `client-fingerprint` | string | uTLS 指纹 |

## 变体

| 文件 | 说明 |
|------|------|
| `trojan-base.yaml` | TCP + TLS |
| `trojan-ws.yaml` | WebSocket + TLS |
| `trojan-reality.yaml` | REALITY |
| `trojan-ss-aead.yaml` | Trojan + Shadowsocks AEAD |

## ss-opts

`method` 只接受三类：

- `aes-128-gcm`
- `aes-256-gcm`
- `chacha20-ietf-poly1305`

注意这里是 `chacha20-ietf-poly1305`（带 ietf），和 SS 老加密名 `chacha20-ietf` 不是一回事。

## REALITY 说明

Trojan + REALITY 的组合确实存在，mihomo 支持。但服务端要配 Trojan（不是 Xray 的 Trojan 传输）同套 REALITY 参数。兼容边界与 REALITY 通用说明一致：不保证兼容 Xray v26.7.11+ 新握手规则。

## 注意事项

- Trojan 默认走 443。换端口需服务端配合，不是改端口就能通。
- `skip-cert-verify` 只应在目标证书确实无法验证时打开，不应作为通行方案。
- WS 变体下 `ws-opts.headers.Host` 决定 Host 头，`sni` 决定 SNI，两者通常一致但字段不同。
- 早期模板把 password 写成列表，已改为字符串。
