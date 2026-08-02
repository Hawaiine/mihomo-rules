# Shadowsocks (SS) 协议

> 基于 SOCKS5 协议的加密传输，支持多种加密方式。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `ss` |
| `cipher` | string | 加密方式: `chacha20-ietf-poly1305` (推荐), `aes-256-gcm`, `xchacha20-ietf-poly1305`, `2022-blake3-aes-256-gcm` |
| `password` | string | 密码 |
| `plugin` | string | 插件: `obfs` 或 `v2ray-plugin` |
| `plugin-opts` | object | 插件参数 |

## 变体说明

| 文件 | 说明 |
|------|------|
| `shadowsocks-base.yaml` | 基础配置，使用 chacha20-ietf-poly1305 加密 |
| `shadowsocks-2022.yaml` | SS 2022 标准，使用 2022-blake3-aes-256-gcm 加密 |
| `shadowsocks-obfs.yaml` | 使用 obfs 插件进行 TLS 混淆 |
| `shadowsocks-v2ray-plugin.yaml` | 使用 v2ray-plugin 插件进行 WebSocket 伪装 |
