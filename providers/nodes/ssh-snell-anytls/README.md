# SSH / Snell / AnyTLS 是三种轻量级隧道协议，适合特殊场景使用。

## 关键参数

| 协议 | `type` | 关键参数 |
|------|--------|----------|
| SSH | `ssh` | `username`、`password`/`private-key` |
| Snell | `snell` | `psk`、`version`、`obfs` |
| AnyTLS | `anytls` | `password`、`sni` |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: ssh-tunnel
  type: yaml
  path: providers/nodes/ssh-snell-anytls/ssh-ssh.yaml
```
