# WireGuard 协议

mihomo 的 WireGuard 出站实现完整 WireGuard，靠 QUIC-over-UDP 承载。它**不提供 HTTP 或 SOCKS5 隧道能力**。

## 前提

目标版本：mihomo v1.19.31。

模板都是 `proxies:` 下的节点列表片段。

## 关键字段

顶层字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ip` | string | 本机虚拟接口地址，如 `10.0.0.2/32` |
| `ipv6` | string | IPv6 地址，如 `::2/128` |
| `private-key` | string | 本机私钥，Base64 |
| `peers` | list | 对端列表 |
| `mtu` | int | MTU |
| `persistent-keepalive` | int | 保活秒数 |
| `workers` | int | 工作协程数 |
| `remote-dns-resolve` | bool | 是否远端解析域名 |
| `dns` | list | DNS 服务器 |
| `refresh-server-ip-interval` | int | 服务端 IP 刷新间隔秒数 |

`peers` 每项（`WireGuardPeerOption`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `server` | string | 对端地址（覆盖顶层 server） |
| `port` | int | 对端端口 |
| `public-key` | string | 对端公钥，Base64 |
| `pre-shared-key` | string | 预共享密钥，Base64 |
| `reserved` | list | 保留字节，通常是 3 个 |
| `allowed-ips` | list | 允许的 IP 段 |

## 变体

| 文件 | 说明 |
|------|------|
| `wireguard-wireguard.yaml` | 标准 WireGuard 单 peer |

WireGuard 只保留一份标准模板。`wireguard-tunnel-http.yaml` 与 `wireguard-tunnel-socks5.yaml` 已删除：它们与标准模板逐字相同、只是改了 `name`，而 mihomo 的 WireGuard outbound 没有 HTTP/SOCKS5 隧道字段，文件名具有误导性。要代理链请用 `relay` 策略组或在上游套用 HTTP/SOCKS 出站。

## 常见错误

- `public-key` / `pre-shared-key` / `reserved` / `allowed-ips` 属于 peer 层，不能直接写在顶层。写成 peer下的字段。
- `peer-dns` 不是 WireGuard option 字段，用 `dns` + `remote-dns-resolve`。
- `pre-shared-key` 是带连字符的完整词，不是 `presharedkey`。
- `udp: true` 可以写但冗余：WireGuard 本身就是 UDP 承载。
- WireGuard 需要 UDP 出站能力。若网络阻断 UDP，WireGuard 不可用。
