# Trojan 是基于 TLS 的安全代理协议，使用 HTTPS 封装，难以被检测。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `password` | string | 密码（支持多个） |
| `sni` | string | TLS Server Name Indication |
| `ss-opts` | object | Shadowsocks AEAD 加密参数（可选） |
| `reality-opts` | object | REALITY 参数：`public-key`、`short-id` |
| `alpn` | list | TLS ALPN 列表 |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: trojan-base
  type: yaml
  path: providers/nodes/trojan/trojan-base.yaml
```
