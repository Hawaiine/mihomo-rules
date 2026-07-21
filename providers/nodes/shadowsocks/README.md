# Shadowsocks (SS) 是一种基于 SOCKS5 协议的加密传输协议，用于穿透网络审查。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `cipher` | string | 加密方式，如 `chacha20-ietf-poly1305`、`aes-256-gcm`、`2022-blake3-aes-256-gcm` |
| `password` | string | 密码 |
| `plugin` | string | 插件名：`obfs`、`v2ray-plugin`（可选） |
| `plugin-opts` | object | 插件参数 |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用即可。

```yaml
# 在 rules-provider 或 proxy-provider 中引用
- name: ss-base
  type: yaml
  path: providers/nodes/shadowsocks/shadowsocks-base.yaml
```
