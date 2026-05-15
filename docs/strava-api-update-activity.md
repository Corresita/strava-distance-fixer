# PUT /activities/{id} — Update Activity

来源：<https://developers.strava.com/docs/reference/#api-Activities-updateActivityById>
镜像：<https://github.com/sshevlyagin/strava-api-v3.1/blob/master/docs/ActivitiesApi.md>

> Updates the given activity that is owned by the authenticated athlete.

## 端点

| 项 | 值 |
| --- | --- |
| 方法 | `PUT` |
| 路径 | `/activities/{id}` |
| Base URL | `https://www.strava.com/api/v3` |
| 认证 | `Authorization: Bearer <access_token>` |
| 必需 scope | `activity:write`（更新 "Only Me" 活动还额外需要 `activity:read_all`） |

## 路径参数

| 名称 | 类型 | 必需 | 描述 |
| --- | --- | --- | --- |
| `id` | int64 | 是 | 活动 ID |

## 请求体：`UpdatableActivity` 模型

**这是本项目问题的核心：`distance` 不在这个模型里。** Strava 官方只允许通过 API 修改下列字段：

| 字段 | 类型 | 可选 | 描述 |
| --- | --- | --- | --- |
| `commute` | bool | 是 | 是否为通勤 |
| `trainer` | bool | 是 | 是否在健身器材上记录 |
| `description` | string | 是 | 活动描述 |
| `name` | string | 是 | 活动名 |
| `type` | ActivityType | 是 | **已废弃**，请改用 `sport_type`（2022-06-15） |
| `sport_type` | SportType | 是 | 运动类型 |
| `gear_id` | string | 是 | 装备 ID（传 `"none"` 可清空，2014-02-03） |
| `private` | bool | 是 | 是否私有（stravalib 标注"未被 Strava API 实际支持，可能移除"） |
| `hide_from_home` | bool | 是 | 是否对 home feed 隐藏（mute 活动） |

`UpdatableActivity` 里**没有以下字段**：

- `distance`
- `moving_time` / `elapsed_time`
- `start_date` / `start_date_local`
- `total_elevation_gain`
- `average_speed` / `max_speed`
- `start_latlng` / `end_latlng`
- 任何 GPS 轨迹相关字段

## 响应

| 状态码 | 内容 |
| --- | --- |
| 200 | `DetailedActivity` —— 完整活动对象 |
| 4xx / 5xx | `Fault` —— 错误信息 |

## 实际行为：服务端如何处理"未文档化"字段

通过实测（参考 [distance-revert-issue.md](distance-revert-issue.md) 和 stravalib 文档）：

- **对手动创建（无 GPS 轨迹）的活动**：Strava 后端会保存通过 PUT 传入的 `distance`，因为这类活动的 distance 是用户自填的，没有 GPS 来源可作权威。
- **对带 GPS 轨迹的活动**：
  - PUT 接受请求并返回 200，响应体里的 `distance` 字段**可能**短暂显示为你传入的值；
  - 但服务端后台会用 GPS 轨迹重新计算 distance，**短则数秒、长则数分钟内把值改回 GPS 计算结果**；
  - 之后通过 GET 查询就是被回滚后的值。
- 这种"接收但不持久化"的行为类似 2018-07 athlete update 的 silent-ignore 模式（见 [strava-api-changelog-relevant.md](strava-api-changelog-relevant.md)）。

## 多语言代码示例（官方）

```python
from __future__ import print_function
import strava_api_v3
from strava_api_v3.rest import ApiException

configuration = strava_api_v3.Configuration()
configuration.access_token = 'YOUR_ACCESS_TOKEN'

api_instance = strava_api_v3.ActivitiesApi(strava_api_v3.ApiClient(configuration))
id = 789
body = strava_api_v3.UpdatableActivity()

try:
    api_response = api_instance.update_activity_by_id(id, body=body)
    print(api_response)
except ApiException as e:
    print("Exception: %s\n" % e)
```
