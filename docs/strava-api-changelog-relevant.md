# Strava API 变更记录（与本项目相关）

来源：<https://developers.strava.com/docs/changelog/>

只摘录与活动更新 / distance / sport_type / gear 相关的条目，按日期倒序：

## 2022-06-15
> Introduction of activity `sport_type`. This is the preferred field to use moving forward, as opposed to `type`, which is now considered deprecated.

→ 本项目当前没用到 sport_type（只改 distance），但如果未来要支持改运动类型，应该用 `sport_type` 而非 `type`。

## 2018-07-26
> `weight` is the only recognized parameter for athlete update. The endpoint will fail silently on other parameters until September 1st, 2018.

→ **关键先例：Strava 的 update 端点会"静默忽略"未受支持的字段**。同样的"接受请求但不持久化"模式很可能也作用于 `PUT /activities/{id}` 的 `distance` 字段。

## 2015-12-23
> Add `elev_high`, `elev_low`, `max_watts` to activity summary and activity detail.

→ 这些是只读字段，不可通过 UpdatableActivity 修改。

## 2014-02-03
> Allow the clearing of gear from an activity by passing `'none'` for `gear_id` on activity update.

→ 唯一一次官方提到 UpdatableActivity 字段语义。

---

## 未出现在 changelog 里的"事实"

- `distance` **从未** 在 changelog 中作为可更新字段被宣布加入 UpdatableActivity。
- 也从未官方说明 "GPS 活动 distance 不可改"——这是社区里通过实测发现的行为。
- 本项目 [CHANGELOG.md](../CHANGELOG.md) 里 1.4.0 之后的所有重试 / web 回退逻辑，都是为了**绕过**这个未文档化但实际存在的限制。
