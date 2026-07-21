# 📱 Clash for Android 配置

> 适用于 Clash for Android / Surfboard (mihomo 内核)

## 📋 文件

- `config.yaml` — 主配置文件（自动生成，带详细注释）
- `config.min.yaml` — 精简版（无注释，体积更小）

## 🚀 使用步骤

1. **替换订阅链接**：编辑 `config.yaml`，找到 `proxy-providers` 下的 `provider1` 和 `provider2`
2. 将 `url: "此处填入你的订阅链接"` 改为你的真实机场订阅地址
3. **可选**：若只有一个订阅，保留 `provider1`，删除 `provider2` 及相关引用（仅「香港节点」用到）
4. 将文件导入 Clash for Android（点右上角配置 → 导入）
5. 开启代理

## ⚙️ 配置要点

| 参数 | 值 | 说明 |
|------|-----|------|
| `mixed-port` | 7890 | HTTP/SOCKS5 混合代理端口 |
| `port` / `socks-port` | 7891 / 7892 | 纯 HTTP / SOCKS5 端口（兼容旧客户端） |
| `find-process-mode` | off | Android 端不匹配进程（VPN 模式） |
| `tun.enable` | false | Android 端关闭 TUN，仅在 VPN 模式下运行 |
| `tcp-concurrent` | true | 并发连接所有 IP 取最快握手 |
| `unified-delay` | true | 统一延迟测试 |
| `allow-lan` | true | 允许局域网设备使用此代理 |
| `dns.enhanced-mode` | fake-ip | 减少 DNS 泄漏 |
| `dns.cache-algorithm` | arc | 自适应替换缓存 |
| `dns.listen` | 127.0.0.1:53 | DNS 监听（Android VPN 本地，避免端口冲突） |
| `sniffer.enable` | true | TLS/HTTP 嗅探（fake-ip 必需） |

## 🔗 规则说明

- 所有 RULE-SET 引用本项目的 `ruleset/*/` 目录下的规则集
- 规则匹配顺序：拦截 → 内网 → CN 直连 → 品牌分流 → 漏网之鱼
- `GEOSITE` 和 `GEOIP` 数据库通过 `geox-url` 从 jsDelivr CDN 下载（国内加速）

## 📦 策略组说明

| 策略组 | 类型 | 说明 |
|--------|------|------|
| 🔧 手动切换 | select | 手动选择节点 |
| 🛑 全球拦截 | select | 广告/恶意网站拦截 |
| 🔯 故障转移 | fallback | 自动切换可用节点 |
| 🔀 负载均衡 | load-balance | 多节点并发复用 |
| 🐟 漏网之鱼 | select | 未匹配规则的兜底策略 |
| 🇭🇰🇯🇵🇺🇸🇸🇬🇹🇼 | select | 地区节点分组 |

### 品牌策略组

每个品牌对应一个策略组（如 Netflix → Netflix 组），支持从 `provider1` 拉取节点。