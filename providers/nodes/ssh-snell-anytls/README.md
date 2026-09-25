# SSH / Snell / AnyTLS

三个轻量出站。字段都按 mihomo v1.19.31 源码校对，早期模板里 `user`、`passphrase`、`auth_str`、`plain`、`username` 等错误键名已修正。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。

## SSH（`type: ssh`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `username` | string | 用户名。**不是** `user` |
| `password` | string | 密码 |
| `private-key` | string | 私钥内容或路径 |
| `private-key-passphrase` | string | 私钥口令。**不是** `passphrase` |
| `host-key` | list | 服务端公钥 |
| `host-key-algorithms` | list | 公钥算法 |

SSH 只支持 TCP，`udp` 无意义。默认端口 22。

## Snell（`type: snell`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `psk` | string | 预共享密钥 |
| `version` | int | 3 或 4 |
| `reuse` | bool | 是否复用连接 |
| `obfs-opts` | object | `mode`：`tls` / `http`。**没有** `plain` |
| `client-fingerprint` | string | uTLS 指纹 |

v3 与 v4 由 `version` 区分，不是两个协议名。默认端口通常 44000（Snell 服务端自选，不是固定值）。

## AnyTLS（`type: anytls`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `password` | string | 密码 |
| `sni` | string | TLS SNI |
| `idle-session-check-interval` | int | 秒 |
| `idle-session-timeout` | int | 秒 |
| `min-idle-session` | int | 最小空闲会话数 |
| `client-metadata` | string | JSON 格式客户端元数据 |

AnyTLS 没有 `username` 和 `client-id` 字段。早期模板里的 `username` / `client-id` 不在源码里，已删除。若服务端启用用户隔离，用 `client-metadata`。

## 变体

| 文件 | 说明 |
|------|------|
| `ssh-ssh.yaml` | SSH |
| `ssh-snell.yaml` | Snell v4 |
| `ssh-snell-v3.yaml` | Snell v3 |
| `ssh-anytls.yaml` | AnyTLS |

## 注意事项

- SSH 的 `udp` 不是这个 outbound 的功能。写上去不报错，但不会产生 UDP 转发。
- AnyTLS 目前版本区间没有公开版本号字段，模板不写版本要求，只以本仓库标注的 mihomo 目标版本为准。
- `private-key` 放私钥内容即可，不需要额外文件。
- `skip-cert-verify` 只应在证书确实无法验证时打开。
