# Hysteria 协议

> 专为恶劣网络环境优化的协议，支持端口跳跃和 QUIC 传输。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `hysteria2` (推荐) 或 `hysteria` (v1) |
| `password` / `auth-str` | string | 认证凭据 |
| `up` / `down` | string | 上下行带宽，如 `"50 Mbps"` |
| `ports` | string | 端口跳跃范围，如 `"443-8443"` |
| `hop-interval` | int | 端口跳跃间隔 (秒) |
| `obfs` | string | 混淆密码 (hy2) 或混淆类型 (hy1) |

## 变体说明

| 文件 | 说明 |
|------|------|
| `hysteria-hy2.yaml` | Hysteria2 基础配置 (推荐) |
| `hysteria-hy2-optimized.yaml` | Hysteria2 优化配置 |
| `hysteria-hy2-portjump.yaml` | 端口跳跃配置 |
| `hysteria-hy1.yaml` | Hysteria v1 基础配置 |
| `hysteria-hy1-portjump.yaml` | Hysteria v1 端口跳跃 |

## 注意事项

- **Hysteria2** 是当前推荐版本，性能更优
- `up` 和 `down` 应与实际情况匹配，不填则自动检测
- 端口跳跃需服务器端配合
