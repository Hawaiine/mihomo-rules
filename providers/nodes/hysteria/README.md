# Hysteria 协议

Hysteria 有两个世代，字段结构不同。mihomo v1.19.31 源码里两者是独立 option 结构。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。

## Hysteria2（推荐，`type: hysteria2`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `password` | string | 认证密码 |
| `obfs` | string | 混淆类型，如 `salamander` |
| `obfs-password` | string | 混淆密码，与 `obfs` 配对 |
| `up` / `down` | string | 带宽，如 `"50 Mbps"` / `"200 Mbps"` |
| `ports` | string | 端口跳跃范围，如 `"443-8443"` |
| `hop-interval` | string | 端口跳跃间隔 |

## Hysteria v1（`type: hysteria`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `auth-str` | string | 认证字符串。**不是** `auth_str` |
| `auth` | string | 认证字符串的 base64 形式，与 `auth-str` 二选一 |
| `obfs` | string | 混淆字符串，xplus 混淆 |
| `obfs-protocol` | string | 协议名，兼容 Stash 写法 |
| `protocol` | string | 协议名 |
| `up` / `down` | string | 带宽 |
| `up-speed` / `down-speed` | int | 以 Mbps 为单位的带宽 |

## 变体

| 文件 | 说明 |
|------|------|
| `hysteria-hy2.yaml` | Hysteria2 基础 |
| `hysteria-hy2-portjump.yaml` | Hysteria2 + 端口跳跃 |
| `hysteria-hy2-optimized.yaml` | 固定带宽写法 |
| `hysteria-hy1.yaml` | Hysteria v1 |
| `hysteria-hy1-portjump.yaml` | Hysteria v1 + 端口跳跃 |

## 注意事项

- 早期模板把 v1 写成 `auth_str`，源码里是 `auth-str`，yaml 不会自动转换。已修正。
- 早期模板把 v1 的 `obfs` 写成 `{type: salamander, password: ...}` 对象。v1 的 `obfs` 是字符串，salamander 属于 v2 时代的写法。已修正。
- `hysteria-hy2-optimized.yaml` 只是把 `up`/`down` 写死成固定值。源码里 `up-speed`/`down-speed`（Mbps 整数）会覆盖 `up`/`down`，所以它不是「自动优化」，只是另一种带宽声明。服务端限速低于这个值时不会更快。若不需要，可合并进基础模板。
- 端口跳跃要求服务端开放连续端口段，客户端 `ports` 指定范围，`hop-interval` 控制换端口节奏。
- QUIC 依赖 UDP。网络对 UDP 或 443 限速时，这两个协议都不适用。
- `up`/`down` 不填时按目标网络自动探测，不是「无限带宽」。服务端也会按自己的配置限速。
- `skip-cert-verify` 只应在证书确实无法验证时打开。
- Hysteria2 的 `obfs-min-packet-size` / `obfs-max-packet-size` 单位是字节，不是 KiB。
