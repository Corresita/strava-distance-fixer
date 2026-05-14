# Strava OAuth2 认证

来源：<https://developers.strava.com/docs/authentication/>

## 流程概览（三段式 OAuth2）

1. 引导用户访问授权页面（携带 `client_id` / `redirect_uri` / `scope` / `response_type=code`）。
2. 用户同意后，Strava 重定向回 `redirect_uri` 并带 `?code=...`。
3. 应用用此 code 调用 `POST https://www.strava.com/oauth/token` 换取 `access_token` + `refresh_token`。

## Scope 一览

| Scope | 含义 |
| --- | --- |
| `read` | 读取公开 profile 信息 |
| `read_all` | 读取私有 segment / route / profile |
| `profile:read_all` | 读取所有 profile 信息 |
| `profile:write` | 修改 athlete 的 weight / FTP，标记 segment |
| `activity:read` | 读取对该 app 可见的活动（不含隐私区） |
| `activity:read_all` | 读取所有活动（含隐私区、Only Me） |
| `activity:write` | **创建手动活动 / 上传 / 编辑该 app 可见的活动** |

> 本项目需要 `activity:read` + `activity:write`。如果想改 "Only Me" 活动，还要加 `activity:read_all`。

## Access Token 刷新

- Access token 有效期 **6 小时**。
- 刷新端点：`POST https://www.strava.com/oauth/token`

请求示例：

```http
POST https://www.strava.com/oauth/token
Content-Type: application/x-www-form-urlencoded

client_id=<CLIENT_ID>
&client_secret=<CLIENT_SECRET>
&grant_type=refresh_token
&refresh_token=<REFRESH_TOKEN>
```

响应：

```json
{
  "token_type": "Bearer",
  "access_token": "...",
  "expires_at": 1747250000,
  "expires_in": 21600,
  "refresh_token": "..."
}
```

**注意：** Strava 可能在刷新时返回**新的** `refresh_token`，必须用最新返回的，旧的会失效。本项目 [app.py:96](../app.py#L96) 已经在 `get_access_token()` 里正确处理了这一点。

## Token 撤销

`POST https://www.strava.com/oauth/deauthorize?access_token=<token>`，用户也可以在 Strava 设置里手动撤销。

## API Base URL

所有请求均发往 `https://www.strava.com/api/v3`，带 `Authorization: Bearer <access_token>` 头。
