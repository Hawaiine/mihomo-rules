# VLESS 是 VMess 的改进版协议，去除了 alter-id，支持 XTLS 和 REALITY，性能更优。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `uuid` | string | 用户 UUID |
| `flow` | string | 流控：`xtls-rprx-vision` (XTLS) |
| `network` | string | 传输方式：`ws`、`grpc`、`tcp` |
| `reality-opts` | object | REALITY 参数：`public-key`、`short-id` |
| `tls` | bool | 是否启用 TLS |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: vless-reality
  type: yaml
  path: providers/nodes/vless/vless-reality.yaml
```
