# VMess 协议

VMess 是 V2Ray 核心传输协议，用 UUID + alterId + 自选 cipher 认证。mihomo 从 v1.19.31 源码看，`alterId` 键名是 camelCase，且被标为必填（没有 omitempty）。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。粘贴到主配置 `proxies:` 下，不要和 `proxy-providers` 片段混用。

## 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定 `vmess` |
| `uuid` | string | 合法 UUID |
| `alterId` | int | 附加 ID。源码要求 camelCase，写 `alter-id` 会被弱类型解析器忽略，等同 0 |
| `cipher` | string | `auto` / `none` / `zero` / `aes-128-gcm` / `chacha20-poly1305`。不含 `chacha20-ietf` |
| `network` | string | `tcp` / `ws` / `grpc` / `h2` |
| `servername` | string | TLS SNI |
| `packet-encoding` | string | `xudp` / `packetaddr`，缺省 `xudp` |

## 变体

| 文件 | 说明 |
|------|------|
| `vmess-tcp.yaml` | TCP，无 TLS |
| `vmess-ws.yaml` | WebSocket，无 TLS |
| `vmess-ws-tls.yaml` | WebSocket + TLS |
| `vmess-grpc.yaml` | gRPC + TLS |
| `vmess-h2.yaml` | HTTP/2 + TLS |

## 注意事项

- `alterId` 不是「越高越安全」。它是签名标签位，现代服务端普遍要求 0。若服务端配了非 0，这里必须写相同值。
- cipher 写 `auto` 最稳：服务端是 AES-GCM 或 ChaCha20-Poly1305 都能协商。写死 cipher 时，双端必须完全一致。
- `global-padding`、`authenticated-length` 是 VMess 自身加固字段，默认 0-4099；只有双端都用新版 Xray 时才需要。
- VMess 也可以开 REALITY，字段和 VLESS 相同，但服务端必须配置 VMess+REALITY。
- 早期模板里的 `proxy-alive` 没有源码支持，已删除。
- WS 场景下 `ws-opts.headers.Host` 决定实际 Host 头，`servername` 决定 SNI，两者通常一致但不是同一个字段。
