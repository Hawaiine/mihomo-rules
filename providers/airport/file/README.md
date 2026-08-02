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
      lazy: true
```

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | `file` 本地文件 |
| `path` | 是 | 本地文件路径 |
| `health-check` | 否 | 健康检查配置 |
| `override` | 否 | 覆盖所有节点参数 |

## 注意事项

- `path` 是必填项，指向本地文件路径
- 文件必须是有效的 Mihomo/Clash 订阅格式
- 本地文件不会自动更新，需手动替换
