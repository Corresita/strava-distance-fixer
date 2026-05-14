# Strava API 参考文档（项目离线副本）

本目录是从 Strava 官方开发者文档、API 镜像和 stravalib SDK 文档中整理出来的，覆盖本项目实际用到的所有端点和模型。重点是回答一个核心问题：

> **为什么用 Strava API PUT `/activities/{id}` 修改 distance 后，对于带 GPS 的活动会被服务端"自动改回去"？**

## 文件列表

| 文件 | 内容 |
| --- | --- |
| [strava-api-update-activity.md](strava-api-update-activity.md) | `PUT /activities/{id}` 端点、`UpdatableActivity` 模型字段表 |
| [strava-api-auth.md](strava-api-auth.md) | OAuth2 授权流程、scope、token 刷新 |
| [strava-api-webhooks.md](strava-api-webhooks.md) | Webhook 订阅、事件 payload、握手协议 |
| [strava-api-rate-limits.md](strava-api-rate-limits.md) | 速率限制与响应头 |
| [strava-api-changelog-relevant.md](strava-api-changelog-relevant.md) | 与本项目相关的官方变更记录 |
| [distance-revert-issue.md](distance-revert-issue.md) | **GPS 活动 distance 被回滚问题的根因分析与解决方案** |

## 原始来源

- <https://developers.strava.com/docs/reference/>
- <https://developers.strava.com/docs/authentication/>
- <https://developers.strava.com/docs/webhooks/>
- <https://developers.strava.com/docs/rate-limits/>
- <https://developers.strava.com/docs/changelog/>
- <https://github.com/sshevlyagin/strava-api-v3.1/blob/master/docs/UpdatableActivity.md>
- <https://stravalib.readthedocs.io/en/latest/reference/api/stravalib.client.Client.update_activity.html>
- Strava 社区论坛（403，仅通过搜索摘要参考）

抓取时间：2026-05-14。
