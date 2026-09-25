# VLESS 协议

VLESS 是 mihomo 的标准出站之一，UUID 作为身份标识，没有alter-id，本身不加密，安全靠 TLS/REALITY。

## 前提

Mihomo 目标版本：v1.19.31。

以下模板都是 `proxies:` 下的**节点列表片段**。把列表项粘贴到主配置的 `proxies:` 下面，不要和 `proxy-providers` 片段混用。节点文件本身不带 `proxies:` 顶层键，不能单独当配置文件使用。

## 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定 `vless` |
| `uuid` | string | 合法 UUID |
| `flow` | string | 可选 `xtls-rprx-vision`，仅 TCP |
| `network` | string | `tcp` / `ws` / `grpc` / `h2` |
| `tls` | bool | 是否启用 TLS |
| `servername` | string | TLS SNI |
| `reality-opts` | object | REALITY 参数，至少 `public-key` |
| `packet-encoding` | string | `xudp` / `packetaddr`，缺省 `xudp` |
| `client-fingerprint` | string | uTLS 指纹，`chrome` / `firefox` / `safari` / `random` |

## 变体

| 文件 | 说明 |
|------|------|
| `vless-reality.yaml` | REALITY + TCP |
| `vless-reality-vision.yaml` | REALITY + Vision |
| `vless-ws.yaml` | WebSocket，无 TLS |
| `vless-ws-tls.yaml` | WebSocket + TLS |
| `vless-grpc.yaml` | gRPC + TLS |

## 版本与兼容

REALITY 与 Xray 的兼容性有明确边界，不是「最新配最新就一定行」：

- mihomo 按源码 v1.19.31 标注。
- 官方文档标明不保证兼容 Xray v26.7.11 及以后版本。
- Xray 稳定版目前到 v26.3.27。v26.7.11 与 v26.9.9 是预发布，不要当默认目标。
- 未经互通实测的模板，README 只写「语法与核心解析已验证」，不写「实测可用」。

## 注意事项

- `servername` 必须与服务端 REALITY 的 target 证书名一致，不是任意的「大站域名」。
- `support-x25519mlkem768` 默认 false。模板以注释给出，只有服务端已启用该密钥交换时才打开。
- 早期文档建议用 firefox 规避握手失败，那是 uTLS 指纹差异导致，不是兼容性证据。当前 uTLS 只有 chrome 指纹真的带 X25519MLKEM768。
- SNI、REALITY target、public-key、short-id 各管各的事：SNI 是 TLS 握手展示名；target 是服务端回退目标；public-key 必须与服务端一致；short-id 是十六进制。
- `client-fingerprint` 是 TLS 伪造，与证书链上的指纹不是一回事。
- XHTTP、VLESS Encryption 在目标版本 v1.19.31 上未经验证，不提供模板。
- TUIC 与 UDP over TCP 无关系，不要把 `udp-over-tcp` 写成 TUIC 专用。
- 真实证书链请保留校验。
