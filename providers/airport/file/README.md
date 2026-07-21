# 📡 本地文件订阅配置

> 适用于 Mihomo / Clash Meta 的 `proxy-providers` 配置，`type: file`。

## 使用方式

1. 将订阅内容下载到本地文件（如 `my_provider.yaml`）
2. 将文件放在 `providers/` 目录下
3. 在 `config.yaml` 中使用 `type: file` 引用

## 示例

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
