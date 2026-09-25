# 单节点配置模板

`providers/nodes/` 是 Mihomo 的**节点列表片段库**。每个 YAML 文件是一个或多个「proxies 列表项」，内容形如：

```yaml
- name: "node-1"
  type: vless
  server: ...
```

这些文件**不是完整配置**，也没有顶层 `proxies:` 键。

## 目录

| 目录 | 协议 | YAML 数 |
|------|------|---------|
| `shadowsocks/` | Shadowsocks | 4 |
| `vmess/` | VMess | 5 |
| `vless/` | VLESS | 5 |
| `trojan/` | Trojan | 4 |
| `hysteria/` | Hysteria v1/v2 | 5 |
| `tuic/` | TUIC | 2 |
| `wireguard/` | WireGuard | 1 |
| `ssh-snell-anytls/` | SSH / Snell / AnyTLS | 4 |

## 正确的使用方式

方式一：**复制列表项**（适用于所有模板）

1. 打开需要的 `.yaml` 文件
2. 把以 `- name:` 开头的列表项粘贴到主配置的 `proxies:` 下
3. 替换所有占位符

```yaml
proxies:
  - name: "my-node"
    type: vless
    server: real.example.com
    # ...其余字段
```

方式二：**包装成 provider 再用**（适合批量管理）

新建一个带顶层 `proxies:` 的文件，把节点内容粘进去：

```yaml
proxies:
  - name: "my-node"
    type: vless
    server: real.example.com
```

然后用 `type: file` 的 proxy-provider 引用它：

```yaml
proxy-providers:
  my_nodes:
    type: file
    path: ./providers/my_nodes.yaml
```

## 错误用法

- 不要写 `type: yaml` + `path:` 引用节点文件。mihomo 没有这种节点引用方式，`proxies:` 下不能放这种项。
- 不要把节点片段直接当 provider 用。provider 文件必须有顶层 `proxies:`。
- 不要在主配置里同时保留原始片段和复制后的节点，会产生重复或双 `proxies`。

## 通用约定

- 所有地址、密钥、密码均为占位符，不含真实凭据。
- 默认保留证书校验。`skip-cert-verify` 一律注释并附条件说明。
- 每个模板的字段以 mihomo v1.19.31 源码为准，标题里标注来源。
- 双端匹配的字段（`servername`、`public-key`、`short-id`、`grpc-service-name`、`cipher`、`flow`、`password`）必须与服务端完全一致。
- 单位：带宽用 `"50 Mbps"`；端口跳跃间隔秒；TICU/Hysteria 的超时毫秒；`mtu` 字节。
- 不含未经互测的新特性（XHTTP、VLESS Encryption）。REALITY 兼容边界见各协议 README。

## 兼容性说明

REALITY 相关模板只保证 v1.19.31 能解析且按 mihomo 的 REALITY 实现与服务端握手。不保证兼容 Xray v26.7.11 及以后的版本。Xray 稳定版当前为 v26.3.27。

## 回归

本目录不由 `scripts/verify_configs.py` 覆盖。改动后请单独验证：YAML 解析、占位符清单、文档引用一致性。若本地装有目标版本 mihomo，请对每个模板跑 `mihomo -t` 并逐文件记录结果。
