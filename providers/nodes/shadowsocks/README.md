# Shadowsocks 协议

Shadowsocks 是经典加密代理，mihomo 支持 AEAD 族、SS 2022 和两种插件。端口跳跃靠 `ports` 字段。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。

## 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定 `ss` |
| `cipher` | string | 见下表 |
| `password` | string | 密码 |
| `plugin` | string | `obfs` / `v2ray-plugin` |
| `plugin-opts` | object | map，参数随插件而变 |
| `udp-over-tcp` | bool | Shadowsocks 的 UDP over TCP |
| `client-fingerprint` | string | uTLS 指纹 |

## cipher 可选值

AEAD：

- `aes-128-gcm` / `aes-192-gcm` / `aes-256-gcm`
- `chacha20-ietf-poly1305`
- `xchacha20-ietf-poly1305`

SS 2022：

- `2022-blake3-aes-128-gcm`
- `2022-blake3-aes-256-gcm`
- `2022-blake3-chacha20-poly1305`

stream 加密（只在服务端明确要求时用）：`aes-128-ctr` / `aes-192-ctr` / `aes-256-ctr`。

注意 `chacha20-ietf` 不是有效 cipher 名。有效的是 `chacha20-ietf-poly1305`。

## SS 2022 密钥长度

v1.19.31 实测：客户端 `password` 只写 `base64(主密钥)` 一段。写成 `base64(主密钥):base64(预共享密钥)` 会报 `bad key length, required 32, got 16`，因为第二段被当成主密钥解析。预共享密钥放服务端配置。

主密钥长度：

- `2022-blake3-aes-128-gcm`：16 字节 base64
- `2022-blake3-aes-256-gcm`：32 字节 base64
- `2022-blake3-chacha20-poly1305`：32 字节 base64

多用户时由服务端用 `users` 列表下发，客户端仍是单主密钥。

生成：`openssl rand -base64 32`。

## obfs 插件

`plugin-opts` 用 `mode`（`tls` / `http`）和 `host`。没有 `plain` 模式。

## v2ray-plugin 插件

`plugin-opts` 用 `mode`、`host`、`path`，`tls` 可选。

## 变体

| 文件 | 说明 |
|------|------|
| `shadowsocks-base.yaml` | AEAD，无插件 |
| `shadowsocks-2022.yaml` | SS 2022 |
| `shadowsocks-obfs.yaml` | obfs 插件 |
| `shadowsocks-v2ray-plugin.yaml` | v2ray-plugin |

## 注意事项

- 早期模板把 `udp-over-tcp` 标成「TUIC 专用」。它是 Shadowsocks option 里的字段，与 TUIC 无关。已修正。
- `password` 是字符串，不是列表。
- 插件需要对应二进制存在，否则启动即失败。mihomo 不会内置插件实现。
- stream 加密不提供完整性校验，优先用 AEAD。
