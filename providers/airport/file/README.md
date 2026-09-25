# 本地文件订阅

`type: file` 读取一份订阅文件。文件内容是带顶层 `proxies:` 的节点列表，不是本目录这种 provider 片段。

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

`path` 必填，且必须在 HomeDir（启动参数 `-d`）或 `SAFE_PATHS` 内。文件不会自动更新。

`health-check`、`override`、`filter`、`exclude-filter`、`exclude-type` 与 http 相同，见 `../http/README.md`。
