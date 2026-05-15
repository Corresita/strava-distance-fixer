# Strava API 速率限制

来源：<https://developers.strava.com/docs/rate-limits/>

## 默认配额

| 类别 | 15 分钟 | 每日 |
| --- | --- | --- |
| 总体（所有请求） | **200** | **2 000** |
| 非上传（不含 POST activities / uploads / activity 媒体） | **100** | **1 000** |

## 重置规则

- 15 分钟窗口在自然分钟边界重置：每小时的 :00 / :15 / :30 / :45。
- 每日窗口在 UTC 午夜重置。

## 超限行为

- 返回 `429 Too Many Requests`，body 是 JSON 错误。
- **短期超限仍会计入长期配额**。

## 响应头

每个 API 响应都包含：

| Header | 值 |
| --- | --- |
| `X-RateLimit-Limit` | 两个逗号分隔的整数（15 分钟上限，每日上限） |
| `X-RateLimit-Usage` | 两个整数（15 分钟已用，每日已用） |
| `X-ReadRateLimit-Limit` | 读请求专属（15 分钟，每日） |
| `X-ReadRateLimit-Usage` | 读请求已用（15 分钟，每日） |

## 对本项目的影响

每个活动的处理通常涉及：

- 1 次 token 刷新（命中缓存大多数情况不会发生）
- 1 次 GET（确认 activity 已就绪）
- 0~N 次 GET（重试拿稳定 distance）
- 1 次 PUT（写入新 distance）
- 1 次 GET（verify 是否被回滚）
- 若被回滚，再次进入重试循环

最坏情况下一个活动消耗 ~15 个请求。按当前 5 次 retry + 30s/60s wait 估算：

- 单活动 ≤ 20 请求 → 每 15 分钟最多处理 ~10 个活动而不超非上传 100/15min 配额。
- 对个人用户而言这远远够用，**不需要为速率限制做额外优化**。
- 若后续接入多个用户，则需要考虑共享速率桶并加退避。
