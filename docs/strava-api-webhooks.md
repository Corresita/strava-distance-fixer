# Strava Webhooks

来源：<https://developers.strava.com/docs/webhooks/>

## 订阅模型

- 一个 app **只能有一个** subscription，所有授权用户的事件都走同一个 callback。
- 订阅管理：`POST/GET/DELETE https://www.strava.com/api/v3/push_subscriptions`

## 验证握手

1. 应用 POST 订阅请求，附带 `callback_url` 和 `verify_token`。
2. Strava 立即向 callback URL 发 GET，附带 `hub.mode=subscribe`、`hub.verify_token`、`hub.challenge`。
3. 应用必须在 **2 秒内** 返回 `{ "hub.challenge": "<value>" }` 且 HTTP 200。

本项目 [app.py:335](../app.py#L335) 的 `webhook_verify` 已实现此逻辑。

## 事件触发条件

会触发 webhook 的操作：

- 活动 **创建** (`aspect_type=create`)
- 活动 **删除** (`aspect_type=delete`)
- 活动 **更新**（仅限以下三种字段变化）：
  - `title`
  - `type` / `sport_type`
  - `private`
- Athlete **取消授权**

> **重要：distance / moving_time 等字段的变化不会触发 webhook**。也就是说，如果 Strava 后台静默把你 PUT 的 distance 改回去，你不会收到任何通知。本项目目前是通过"PUT 后 sleep 再 GET 验证"来发现回滚的。

## Event Payload

```json
{
  "object_type": "activity",        // 或 "athlete"
  "object_id": 1234567890,
  "aspect_type": "create",          // create / update / delete
  "updates": { "title": "..." },    // 仅 update 时有
  "owner_id": 12345,
  "subscription_id": 1,
  "event_time": 1748100000
}
```

- 每条 event POST 可能带 `X-Strava-Signature` 头（timestamp + HMAC-SHA256），用于校验合法性。
- 必须 **2 秒内** ACK 200，否则重试最多 3 次。
- "某些活动属性是异步更新的"——一次用户操作可能触发多条事件。

## 项目实现注意点

- 当前 [app.py:351](../app.py#L351) 在 `aspect_type=create` 时启动后台线程并 sleep 120s 再 GET，是为了等 Strava 后台异步计算 distance / GPS 完成。
- 如果 sleep 太短，会拿到 `distance=0` 或不稳定的中间值；
- 如果 sleep 太长，又会让用户等很久才看到结果。这是一个权衡。
