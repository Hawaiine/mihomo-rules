# 📡 地区过滤正则

> 用于 `proxy-providers` 的 `filter` 和 `exclude-filter` 参数。

## 地区匹配正则

| 地区 | 正则 |
|------|------|
| 香港 | `(?i)(香港|Hong.Kong|HongKong|HK|HKG|🇭🇰)` |
| 日本 | `(?i)(日本|Japan|JP|JPN|Tokyo|🇯🇵)` |
| 美国 | `(?i)(美国|United.States|UnitedStates|US|USA|🇺🇸)` |
| 新加坡 | `(?i)(新加坡|Singapore|SG|SGP|🇸🇬)` |
| 台湾 | `(?i)(台湾|Taiwan|TW|TWN|🇹🇼)` |
| 韩国 | `(?i)(韩国|Korea|KR|KOR|Seoul|🇰🇷)` |
| 英国 | `(?i)(英国|UK|GB|GBR|London|🇬🇧)` |
| 德国 | `(?i)(德国|Germany|DE|DEU|Frankfurt|🇩🇪)` |
| 法国 | `(?i)(法国|France|FR|FRA|Paris|🇫🇷)` |
| 加拿大 | `(?i)(加拿大|Canada|CA|CAN|🇨🇦)` |
| 澳大利亚 | `(?i)(澳大利亚|Australia|AU|AUS|Sydney|🇦🇺)` |
| 印度 | `(?i)(印度|India|IN|IND|Mumbai|🇮🇳)` |
| 土耳其 | `(?i)(土耳其|Turkey|TR|TUR|Istanbul|🇹🇷)` |
| 巴西 | `(?i)(巴西|Brazil|BR|BRA|São Paulo|🇧🇷)` |
| 俄罗斯 | `(?i)(俄罗斯|Russia|RU|RUS|Moscow|🇷🇺)` |
| 马来西亚 | `(?i)(马来西亚|Malaysia|MY|MYS|Kuala Lumpur|🇲🇾)` |
| 泰国 | `(?i)(泰国|Thailand|TH|THA|Bangkok|🇹🇭)` |
| 越南 | `(?i)(越南|Vietnam|VN|VNM|Ho Chi Minh|🇻🇳)` |
| 菲律宾 | `(?i)(菲律宾|Philippines|PH|PHL|Manila|🇵🇭)` |
| 印尼 | `(?i)(印尼|Indonesia|ID|IDN|Jakarta|🇮🇩)` |
| 全部 | `(?i).*$` |

## 排除节点正则

| 排除内容 | 正则 |
|----------|------|
| 测试/过期节点 | `(?i)(剩余|过期|GIA|CN2|IPv6|测试)` |
| 仅保留可用节点 | `(?i)(^(?!(剩余|过期|GIA|测试)).*$)` |

## 使用示例

```yaml
proxy-providers:
  hk_provider:
    type: http
    url: "https://your-subscription-link"
    filter: "(?i)(香港|HK|HKG|🇭🇰)"
    exclude-filter: "(?i)(剩余|过期|GIA)"
    exclude-type: ""

  multi_region:
    type: http
    url: "https://your-subscription-link"
    filter: "(?i)(香港|HK|日本|JP|美国|US|新加坡|SG|台湾|TW)"
    exclude-filter: "(?i)(剩余|过期)"
```
