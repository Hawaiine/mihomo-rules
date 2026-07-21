# 📡 机场订阅配置

> 适用于 Mihomo / Clash Meta 的 `proxy-providers` 配置。

## 📂 目录结构

| 目录 | 说明 |
|------|------|
| `http/` | 远程订阅配置模板 |
| `file/` | 本地文件订阅配置模板 |
| `filter/` | 地区过滤正则示例 |

## 🚀 使用方式

### 方式一：远程订阅（推荐）

在 `config.yaml` 中添加：

```yaml
proxy-providers:
  my_provider:
    type: http
    url: "https://your-subscription-link.com/v1/xxx"
    interval: 86400
    path: ./providers/my_provider.yaml
    health-check:
      enable: true
      url: https://cp.cloudflare.com/generate_204
      interval: 300
```

### 方式二：本地文件

将订阅内容下载到本地文件，然后：

```yaml
proxy-providers:
  my_provider:
    type: file
    path: ./providers/my_provider.yaml
    health-check:
      enable: true
      url: https://cp.cloudflare.com/generate_204
      interval: 300
```

## ⚠️ 注意事项

- 订阅链接包含敏感信息, **不要提交到公开仓库**
- 建议使用 `filter` 过滤出你需要的地区节点, 减少内存占用
- `exclude-filter` 可用于排除测试节点、过期节点等
- `override.skip-cert-verify` 仅在你信任机场且遇到证书问题时启用
