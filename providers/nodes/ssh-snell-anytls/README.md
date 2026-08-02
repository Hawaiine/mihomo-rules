# SSH / Snell / AnyTLS 协议

> 三种轻量级隧道协议，适合特殊场景使用。

## 关键参数

| 协议 | `type` | 关键参数 |
|------|--------|----------|
| SSH | `ssh` | `username`, `password`/`private-key` |
| Snell | `snell` | `psk`, `version`, `obfs` |
| AnyTLS | `anytls` | `username`, `password` |

## 变体说明

| 文件 | 说明 |
|------|------|
| `ssh-ssh.yaml` | SSH 代理配置 |
| `ssh-snell.yaml` | Snell v4 配置 |
| `ssh-snell-v3.yaml` | Snell v3 配置 |
| `ssh-anytls.yaml` | AnyTLS 代理配置 |

## 注意事项

- **SSH**: 需要服务器开启 SSH 服务
- **Snell**: 使用 PSK (Pre-Shared Key) 认证，版本 3 和 4 兼容
- **AnyTLS**: 基于 TLS 的轻量级隧道，适合特殊网络环境
