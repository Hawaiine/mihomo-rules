# Trojan 协议

> 基于 TLS 的安全代理协议，使用 HTTPS 封装。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `trojan` |
| `password` | string | 密码 (支持多个) |
| `sni` | string | TLS Server Name Indication |
| `alpn` | list | TLS ALPN 列表 |
| `network` | string | 传输方式: `tcp`, `ws` |
| `reality-opts` | object | REALITY 参数 |

## 变体说明

| 文件 | 说明 |
|------|------|
| `trojan-base.yaml` | 基础 TCP 配置 |
| `trojan-ws.yaml` | WebSocket 传输 |
| `trojan-reality.yaml` | REALITY 抗封锁 |
| `trojan-ss-aead.yaml` | Trojan + Shadowsocks 双层加密 |

## 注意事项

- 建议使用 TLS 1.3
- `alpn` 建议包含 `h2` 和 `http/1.1`
- REALITY 模式可大幅降低被检测概率
