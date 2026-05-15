# GPS 活动 distance 被 Strava 自动改回的根因与处理方案

## 1. 现象

通过 `PUT /activities/{id}` 修改一个**带 GPS 轨迹**的活动的 `distance`：

- HTTP 200 OK，响应体里 `distance` 显示为你传入的值；
- 等数秒~数分钟后，再次 GET 同一活动，`distance` 已被改回 GPS 计算出的原始值。

对**手动创建**（没有 GPS）的活动，同样的 PUT 请求可以持久化生效。

## 2. 根因

参考 [strava-api-update-activity.md](strava-api-update-activity.md)：

> Strava API 官方的 `UpdatableActivity` 模型**根本没有 `distance` 字段**。可更新字段只有：
> `name`, `type`/`sport_type`, `description`, `commute`, `trainer`, `gear_id`, `private`, `hide_from_home`

因此：

1. 我们 PUT 进去的 `distance` 字段，对 API spec 来说是"未文档化的额外字段"；
2. Strava 后端的行为是 **"静默接受 + 不持久化"**（与 2018 年 athlete update 对未声明字段的处理一致，见 [strava-api-changelog-relevant.md](strava-api-changelog-relevant.md)）；
3. 对 GPS 活动，后端会用 GPS 轨迹**异步重算 distance**，把短暂保存的值覆盖掉；
4. 对无 GPS 的手动活动，没有 GPS 来源可用于覆盖，所以 PUT 的 distance 能"侥幸"保住。

**这是一个 Strava 的设计决定，不是 bug，也不会被"修复"——API 从来就没承诺过支持修改 GPS 活动的 distance。**

## 3. 可行的解决路径

按"稳定性 / 工作量"两个维度排序：

### 方案 A：网页表单模拟（项目已部分实现）✅ 推荐

Strava 网页端 `https://www.strava.com/activities/{id}/edit` 的表单提交流程，**对 GPS 活动是有效的**——用户在网页里手动改 distance 然后保存，是会持久化的。本项目 [app.py:164](../app.py#L164) 的 `fix_distance_web` 已经走了这条路，但还有几个**实际会失败**的点：

1. **CSRF 头缺失**：现代 Strava 的 Rails/Turbo 表单要求把 CSRF token 放在 HTTP header `X-CSRF-Token` 里，而不只是表单 hidden 字段。当前代码只把它塞到了表单 `data` 里，可能被服务端拒绝。
2. **单位敏感**：表单里的 distance 字段是按用户在 Strava 设置中的"度量单位"显示的（公里 / 英里）。当前代码直接 `str(rounded_km)`，**如果账号设置为英里，就会把 13.13 km 当作 13.13 mi 提交，导致最终 distance 变成 21.13 km**。需要先读账号 measurement preference，或显式调用一个返回 km 的字段。
3. **没有 verify**：表单 POST 200/302 不代表 distance 真改成功。应该和 API 流程一样，等几秒后 GET 一次 API 拉取 `distance`，确认匹配。
4. **登录 fragile**：用 email/password 模拟登录可能踩到 2FA、人机验证、IP 风控，导致整个 fallback 不可用。

→ 见后文 §5 的修复清单。

### 方案 B：重新上传 GPX/FIT（最稳定，但侵入大）

1. 通过 API `GET /activities/{id}` 拿到原始 streams（GPS 轨迹）；
2. 在本地修改 GPX：按比例缩放每个点之间的位移，或裁剪 / 添加点，使总长度等于目标 distance；
3. **删除原活动**，再用 `POST /uploads` 上传修改后的 GPX。

优势：完全走官方文档，不会被改回。
劣势：

- 修改 GPS 轨迹会让"地图轨迹"也变形，肉眼可见；
- 删除 + 重传会丢失原活动的评论、kudos、segment matches；
- 实现成本高很多。

不推荐除非方案 A 完全走不通。

### 方案 C：放弃修改 GPS 活动，仅对手动活动生效

通过 `activity.start_latlng` 是否为空、或 `activity.manual=True` 字段判定，只对**手动活动**走 API PUT，对 GPS 活动直接跳过或转方案 A。

→ 这其实就是当前代码的隐式行为（API 失败后转 web），可以做得更明确：webhook 拿到 activity 后先 GET 一次，看到是 GPS 就直接走 web，不浪费两次 PUT。

## 4. 推荐落地方案

**短期（这次 PR）**：把方案 A 修扎实。具体见 §5。

**中期**：在 webhook 路径里先 GET 判 `manual=True`，直接分流到 API / web，少做两次失败的 PUT，省时间 + 省速率配额。

**长期**：监控 web 表单 fallback 的成功率，如果 Strava 哪天加了人机验证就退到方案 B 或人工兜底。

## 5. 方案 A 的修复清单

修改 [app.py:164](../app.py#L164) 的 `fix_distance_web`：

| 问题 | 现状 | 修复 |
| --- | --- | --- |
| CSRF 头 | 只放在 `data` 里 | 同时放到 `session.headers["X-CSRF-Token"]` |
| 单位 | 直接 `str(rounded_km)` | 先 GET `/api/v3/athlete` 拿 `measurement_preference`，如果是 `feet` 则换算成 miles |
| verify | 只看 HTTP 状态 | POST 后 sleep 5s → API GET → 比较 distance |
| 登录稳定性 | 每次都 fresh login | 已经有 `web_session` 全局缓存，但 cookie 没持久化，重启就失效。可以把 session.cookies 序列化到 `/tmp` |
| 表单字段抓取 | `find_all("input")` | 现在的 Strava 表单大量是 React 渲染，HTML 静态抓取可能为空。需要先打开一次活动 edit 页确认实际表单字段名 |
| 凭证 | `STRAVA_EMAIL` / `STRAVA_PASSWORD` 明文环境变量 | 暂时接受，但要警告：登录失败 3 次会被 Strava 限流，建议本地用 cookie 注入而非密码登录 |

修改 [app.py:216](app.py#L216) 的 `fix_distance`：

| 问题 | 现状 | 修复 |
| --- | --- | --- |
| GPS 活动也先打 API | 浪费 2 次 PUT 才进 fallback | GET 后判 `activity.get("manual")` 或 `activity.get("start_latlng")`，如果是 GPS 直接走 web |
| Fallback 失败被掩盖 | `fix_distance_web` 抛异常被外层 except 接住，下一轮 attempt 4 因 distance 已改成目标值（实测中是 mock 巧合）显示 "already correct" | fallback 失败要 break 出整个 retry 循环并明确 log 失败原因 |

## 6. 参考

- [strava-api-update-activity.md](strava-api-update-activity.md) — UpdatableActivity 字段表
- [strava-api-changelog-relevant.md](strava-api-changelog-relevant.md) — silent-ignore 先例
- [strava-api-webhooks.md](strava-api-webhooks.md) — distance 变更不触发 webhook
- 社区帖（被 403，仅搜索摘要）：
  - <https://communityhub.strava.com/strava-features-chat-5/known-issue-correct-distance-function-no-longer-working-10394>
  - <https://communityhub.strava.com/strava-features-chat-5/edit-a-run-distance-932>
- 第三方修复工具：<https://gotoes.org/gotoes/strava/> （供参考，本项目不依赖）
