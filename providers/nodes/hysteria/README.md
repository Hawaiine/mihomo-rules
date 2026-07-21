# Hysteria 是一个专为恶劣网络环境优化的协议，支持端口跳跃和 QUIC 传输。Hysteria2 为当前推荐版本。

## 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | string | `hysteria2` (推荐) 或 `hysteria` (v1 旧协议) |
| `password` / `auth-str` | string | 认证凭据 |
| `up` / `down` | string | 上下行带宽，如 `"50 Mbps"` |
| `ports` | string | 端口跳跃范围，如 `"443-8443"` |
| `hop-interval` | int | 端口跳跃间隔 (秒) |
| `obfs` | string | 混淆密码 (hy2) 或混淆类型 (hy1) |

## 用法

将 YAML 文件放在 `providers/nodes/` 下，在 `proxies` 或 `proxy-providers` 中引用。

```yaml
- name: hy2-basic
  type: yaml
  path: providers/nodes/hysteria/hysteria-hy2.yaml
```
