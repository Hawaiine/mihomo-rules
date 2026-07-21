# TUIC 是一个基于 QUIC 协议的代理协议，低延迟、高吞吐，支持多路复用。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `token` | string/list | TUIC v5 认证 token（支持多 token） |
| `uuid` / `password` | string | TUIC v4 认证凭据 |
| `alpn` | list | TLS ALPN，通常包含 `h3` |
| `congestion-controller` | string | 拥塞控制：`cubic`、`bbr` |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: tuic-v5
  type: yaml
  path: providers/nodes/tuic/tuic-v5.yaml
```
