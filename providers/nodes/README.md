# 🌐 单节点配置模板

> 适用于 Mihomo / Clash Meta 的 `proxies:` 配置。
> 每个协议一个文件夹，每个配置变体一个独立的 `.yaml` 文件。

## 📂 协议目录

| 目录 | 协议 | 变体数 |
|------|------|--------|
| `shadowsocks/` | Shadowsocks | 4 |
| `vmess/` | VMess | 5 |
| `vless/` | VLESS | 5 |
| `trojan/` | Trojan | 4 |
| `hysteria/` | Hysteria v1/v2 | 5 |
| `tuic/` | TUIC | 3 |
| `wireguard/` | WireGuard / HTTP / SOCKS5 | 3 |
| `ssh-snell-anytls/` | SSH / Snell / AnyTLS | 4 |

## 🚀 使用方式

### 方式一：直接引用（推荐）

在 `config.yaml` 的 `proxies:` 部分引用：

```yaml
proxies:
  - name: "ss-base"
    type: yaml
    path: providers/nodes/shadowsocks/shadowsocks-base.yaml
```

### 方式二：复制内容

1. 进入对应协议的文件夹
2. 选择适合的配置变体（如 `shadowsocks/shadowsocks-obfs.yaml`）
3. 复制内容到你的 `config.yaml` 的 `proxies:` 部分
4. 替换占位符（server、port、password 等）

## ⚠️ 注意事项

- 所有配置均为**示例模板**，使用前请替换真实参数
- 订阅链接含敏感信息 → **不要提交到公开仓库**
- 推荐通过环境变量或 GitHub Secrets 管理密钥
- 参考文档: https://wiki.metacubex.one/en/config/proxies/

## 📋 协议特性对比

| 协议 | 加密 | 传输 | 抗封锁 | 性能 |
|------|------|------|--------|------|
| Shadowsocks | AEAD | TCP | 中 | 高 |
| VMess | AEAD | TCP/WS/H2/gRPC | 高 | 中 |
| VLESS | 无 | TCP/WS/GRPC | 高 | 高 |
| Trojan | TLS | TCP/WS | 高 | 高 |
| Hysteria2 | 自定义 | QUIC | 高 | 极高 |
| TUIC | 自定义 | QUIC | 高 | 极高 |
| WireGuard | 内置 | UDP | 中 | 极高 |
