# 节点名过滤

用于 `filter` 和 `exclude-filter`。引擎是 regexp2（.NET 语法），不是 Go RE2，所以支持负向查找。多段正则用反引号分隔，不是用 `|` 把整段再包一层。

英文缩写必须加词边界。否则 `PLUS` 会命中美国，`TWICE` 命中台湾，`WINDOW` 命中印度，`TRUST` 命中土耳其，`Frankfurt` 会同时命中德国和法国。

## 地区

| 地区 | 正则 |
|------|------|
| 香港 | `(?i)(香港\|Hong\s?Kong\|\bHK\b\|HKG)` |
| 日本 | `(?i)(日本\|Japan\|\bJP\b\|JPN\|Tokyo\|东京\|東京)` |
| 美国 | `(?i)(美国\|United\s?States\|\bUS\b\|\bUSA\b)` |
| 新加坡 | `(?i)(新加坡\|Singapore\|\bSG\b\|SGP)` |
| 台湾 | `(?i)(台湾\|台灣\|Taiwan\|\bTW\b\|TWN)` |
| 韩国 | `(?i)(韩国\|Korea\|\bKR\b\|KOR\|Seoul\|首尔)` |
| 英国 | `(?i)(英国\|英國\|\bUK\b\|\bGB\b\|London\|伦敦)` |
| 德国 | `(?i)(德国\|Germany\|\bDE\b\|DEU\|Frankfurt)` |
| 法国 | `(?i)(法国\|France\|\bFR\b\|FRA)` |
| 加拿大 | `(?i)(加拿大\|Canada\|\bCA\b)` |
| 澳大利亚 | `(?i)(澳大利亚\|澳洲\|Australia\|\bAU\b\|AUS\|Sydney)` |
| 印度 | `(?i)(印度\|India\|\bIN\b)` |
| 土耳其 | `(?i)(土耳其\|Turkey\|\bTR\b\|Istanbul)` |
| 巴西 | `(?i)(巴西\|Brazil\|\bBR\b)` |
| 俄罗斯 | `(?i)(俄罗斯\|Russia\|\bRU\b\|Moscow\|莫斯科)` |
| 马来西亚 | `(?i)(马来西亚\|Malaysia\|\bMY\b)` |
| 泰国 | `(?i)(泰国\|Thailand\|\bTH\b\|Bangkok)` |
| 越南 | `(?i)(越南\|Vietnam\|\bVN\b)` |
| 菲律宾 | `(?i)(菲律宾\|Philippines\|\bPH\b\|Manila)` |
| 印尼 | `(?i)(印尼\|印度尼西亚\|Indonesia\|\bID\b\|Jakarta)` |

国旗 emoji 可选，加上不会造成误匹配，这里不写是为了让正则可读。

## 排除

默认只排除过期和流量耗尽：

```text
(?i)(剩余|过期|流量耗尽|到期)
```

不要把 `GIA`、`CN2`、`IPv6` 放进默认排除，那是正常线路。

若要用一条正则排除“名称里出现这些词”的节点，用：

```text
(?i)^(?!.*(剩余|过期|流量耗尽|到期)).+$
```

`^(?!(剩余|过期))` 只排除以这些词开头的名字，`香港 剩余` 会漏过去。

## 正例 / 反例

保留：`香港 01`、`US West`、`日本 Tokyo`、`DE-Frankfurt`、`IPv6 美国`。

排除：`剩余 30天`、`套餐到期`、`流量耗尽`。

不应命中地区缩写：`PLUS`、`TWICE`、`WINDOW`、`TRUST`、`PHONE`、`HTTPS`。

`DE-Frankfurt` 只应命中德国。`Indonesia` 同时含 India 与 Indonesia，会同时命中印度和印尼，这是名称本身重叠，不是词边界能消掉的。

缩写天然仍有混淆，测出来的样本：

- `SG-CN-relay`：`SG` 落 Singapore。
- `TRUST-Kr`：`\bKr\b` 落 Korea。
- `DECODE-Germany`：`\bDE\b` 落 Germany（这条是期望结果，说明词边界生效）。
- `MY-Error`：`\bMY\b` 落 Malaysia。
- `India-vip`：`\bIN\b` 落 India。

词边界只能保证「不以子串形式命中」：`PLUS` 不命中美国、`TWICE` 不命中台湾、`WINDOW` 不命中印度、`TRUST` 不命中土耳其，这些是实测通过的。但两个缩写出现在同一节点名时，会同时命中多个地区。

避开办法是把业务上成对出现的架构缩写（`SG`、`MY`、`ID`）替换成更长的形式，或者直接用完整地名：
- `SG` → `Singapore`
- `MY` → `Malaysia`
- `ID` → `Indonesia`
