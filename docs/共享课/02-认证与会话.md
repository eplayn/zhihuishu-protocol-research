# 02 · 登录态与会话管理

> 本文还原智慧树的统一登录（CAS / SSO）链路与会话生命周期。
> **所有占位符（`<...>`）均为脱敏替换，非真实值。**

---

## 1. 架构总览

智慧树的登录不是单点，而是**四个子系统协作**：

```
┌─────────────────────────┐
│  login.zhihuishu.com    │  登录中心（Vue SPA，负责收集凭据）
└───────────┬─────────────┘
            │ 凭据（加密后）
            ▼
┌─────────────────────────┐
│  user.zhihuishu.com     │  用户服务（校验账密 / 下发一次性密码）
└───────────┬─────────────┘
            │ 一次性密码
            ▼
┌─────────────────────────┐
│ passport.zhihuishu.com  │  CAS 服务端（发票据 TGT / ST）
└───────────┬─────────────┘
            │ ticket=ST-...
            ▼
┌─────────────────────────┐
│ cloudapi.polymas.com    │  ★ 真正下发业务身份 Cookie 的地方
│   /oauth/login/ssoLogin │
└─────────────────────────┘
```

> **关键认知**：业务身份 Cookie（`CASLOGC`）由 **`cloudapi.polymas.com`** 下发，
> **不是** `passport`。这一点与某些公开参考项目不同。

---

## 2. 完整登录链路

### 2.1 实测跳转链

```
① GET  login.zhihuishu.com/?origin=zhs
        （登录中心页面，用户在此输入凭据或扫码）

② POST user.zhihuishu.com/zhsuser/login/account
        Content-Type: application/json;charset=UTF-8
        Body: <AES 加密后的 base64 字符串>          ← 见 03-crypto.md
        响应: {status, rt: {oncePassword | ...}}

③ GET  user.zhihuishu.com/zhsuser/certify/queryCertify/<uuid>
        （查询认证状态，可能触发二次校验）

④ GET  passport.zhihuishu.com/login
            ?pwd=<一次性密码>
            &service=https://cloudapi.polymas.com/oauth/login/ssoLogin
                     ?redirect=https://www.zhihuishu.com

⑤ 302  Location: https://cloudapi.polymas.com/oauth/login/ssoLogin?ticket=ST-...
        ↑ CAS 签发 Service Ticket

⑥ GET  cloudapi.polymas.com/oauth/login/ssoLogin?ticket=ST-...
        ★★ 在此 Set-Cookie：
            CASLOGC          业务身份（URL 编码的 JSON）
            CASTGC           CAS 票据授权票据（TGT）
            jt-cas           会话 JWT（★ 24 小时有效期）
            polymas-token    跨域令牌

⑦ 302  →  https://www.zhihuishu.com/

⑧ 各子系统再各自换票：
        onlineservice-api.zhihuishu.com/gateway/f/v1/login/gologin?fromurl=...
        cloudapi.polymas.com/oauth/login/ssoLogin?redirect=<子系统地址>
```

### 2.2 两种登录方式

平台支持两种凭据输入方式，**最终都汇聚到第 ④ 步的 `pwd=` 一次性密码机制**：

| 方式 | 触发接口 | 额外要求 |
|---|---|---|
| **账号密码** | `POST /zhsuser/login/account` | ⚠️ **必须携带易盾滑块 token** |
| **二维码扫码** | `GET /qrCodeLogin/getLoginQrImg` + 轮询 | 无 |

#### 2.2.1 账号密码登录（含验证码门槛）

请求体解密后（见 [03-crypto.md](03-crypto.md#3-登录页加密)）：

```json
{
  "account":  "<手机号或账号>",
  "password": "<密码>",
  "validate": "<易盾滑块 token，格式 CN31_...>"
}
```

**实测的验证码门槛：**

| 请求 | 服务端响应 |
|---|---|
| 不带 `validate` | `{"status": -1, "msg": null}` |
| 带**已失效**的 `validate` | `{"status": -4, "msg": "滑动验证码校验失败", "rt": {"pwdErrorCount": 1}}` |

> **结论：账号密码登录必须携带有效的易盾滑块 token，无法纯 API 完成。**
>
> 注意响应里的 `pwdErrorCount` —— 失败尝试会被计数。
> **研究时应避免反复试探，以免触发账号保护。**

#### 2.2.2 二维码登录（推荐，无验证码）

```
① GET  passport.zhihuishu.com/qrCodeLogin/getLoginQrImg
        → { "qrToken": "...", "img": "<base64 PNG>" }

② 轮询 GET passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo?qrToken=...
        → { "status": <状态码>, "oncePassword": "<达成时给出>" }

③ status == 1 时：
   GET passport.zhihuishu.com/login?pwd=<oncePassword>&service=<第④步的 service>
        → 之后的链路与 2.1 的第 ⑤~⑦ 步完全一致
```

**轮询状态码：**

| status | 含义 |
|---|---|
| `-1` | 未扫码 |
| `0` | 已扫码，等待手机确认 |
| `1` | **确认成功，`oncePassword` 已下发** |
| `2` | 二维码过期 |
| `3` | 用户取消 |

---

## 3. CASLOGC —— 业务身份凭据

`CASLOGC` 是一个 **URL 编码的 JSON**，共 **8 个字段**：

```json
{
  "realName":   "<姓名>",
  "myuniRole":  0,
  "myinstRole": 0,
  "userId":     <数字用户 ID>,
  "headPic":    "<头像 URL>",
  "uuid":       "<8 位短标识>",
  "mycuRole":   0,
  "username":   "<哈希后的用户名>"
}
```

### 3.1 解析方式

```python
from urllib.parse import unquote
import json, re

m = re.search(r"CASLOGC=([^;]+)", cookie_string)
info = json.loads(unquote(m.group(1)))
uuid = info["uuid"]          # 后续大量接口的 ?uuid= 参数
```

### 3.2 `uuid` 的用途

- 作为大量接口的 `uuid` 参数
- **前端 `getMainCode()` = `uuid + "zhs"`**，用于进度上报（见 [05](05-progress-reporting.md)）
- 浏览器还会设置一个 `exitRecod_<uuid>` cookie

> ⚠️ **注意**：`exitRecod_<uuid>` 是浏览器端 JS 设置的，
> 纯 API 客户端需要**手动补上**，否则可能被判定为会话不完整。

---

## 4. Cookie 分层

以 `studyservice-api.zhihuishu.com` 为例，实测收到 **10 个 Cookie**：

```
locale, CASTGC, CASLOGC, jt-cas,
Hm_lvt_<siteId>, HMACCOUNT, exitRecod_<uuid>,
Hm_lpvt_<siteId>, acw_tc, SERVERID
```

**按职责分四层：**

| 层 | Cookie | 作用 |
|---|---|---|
| **CAS 票据层** | `CASTGC` | CAS 的 TGT，用于免密换新票据 |
| **业务身份层** | `CASLOGC`, `jt-cas` | 身份声明与会话 JWT |
| **会话层** | `JSESSIONID`, `SESSION`（各子域） | 服务端会话 |
| **风控路由层** | `SERVERID`, `acw_tc`, `INGRESSCOOKIE` | 负载均衡 + 阿里云 WAF |

> 构造请求时，**只需携带顶级域 `.zhihuishu.com` 的 Cookie**，
> 各子域私有的 `JSESSIONID` 不要混入（会冲突）。

---

## 5. 会话生命周期（研究重点）

### 5.1 `jt-cas` 是 24 小时有效期的 JWT

解码 `jt-cas`（一个 ES256 签名的 JWT）：

```json
{
  "iss": "com.zhihuishu",
  "aud": "CAS",
  "sub": "<姓名>",
  "iat": 1790581237,
  "exp": 1790667637,
  "jti": "<UUID v4>",
  "uid": <用户 ID>
}
```

```
exp - iat = 86400 秒 = 24 小时
```

**这是本次研究最重要的运维结论之一：**

> **会话有效期为 24 小时。过期后所有业务接口返回 `401 Forbidden`。**

### 5.2 过期后能否静默续期？—— 实测：不能

理论上，只要 `CASTGC`（CAS TGT）仍有效，就可以免密换一张新票据：

```
GET passport.zhihuishu.com/login?service=<ssoLogin URL>
    （携带旧的 CASTGC）
→ 期望 302 携带 ticket=ST-...
```

**实测结果（TGT 已过期的情况）：**

```
响应: 200 OK，返回登录页 HTML（84 KB）
跳转链: 无
CASTGC: 被服务端清空
```

> **结论：`TGT` 与 `jt-cas` 的生命周期是绑定的，一起失效。
> 过期后必须重新走一次登录。**

### 5.3 单点登录（SSO）语义

- **在别处登录 → 现有会话立即失效**（服务端吊销）
- 浏览器与 API 客户端**共享同一套服务端会话** ——
  浏览器登出，API 的 Cookie 也一起失效

> 这一点在排查中很关键：当浏览器显示"未登录"时，
> 说明**服务端会话已死**，不是本地 Cookie 的问题。

### 5.4 会话状态判定

```python
import base64, json, time

def jwt_exp(tok: str):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["exp"]

# 剩余有效期
remain_hours = (jwt_exp(jt_cas) - time.time()) / 3600
```

**但 JWT 未过期 ≠ 会话可用**（可能被 SSO 吊销），
**最终应以一次真实业务请求的返回码为准。**

---

## 6. 工程建议

| 场景 | 建议 |
|---|---|
| 长期使用 | **每天重新登录一次**（扫码，无需密码与验证码） |
| 会话判定 | JWT `exp` 预检 + 业务接口实测，两者都要 |
| Cookie 构造 | 只取顶级域 `.zhihuishu.com` 的 Cookie |
| 缺失 `exitRecod_<uuid>` | 手动补 `exitRecod_<uuid>=2` |
| 并发 | **禁止多账号共享会话**（平台有 `-10` 多开检测） |

---

## 7. 未验证 / 存疑部分

如实标注研究中**没有拿到确凿证据**的部分：

- `CASTGC`（TGT）的**独立**有效期未知（实测中它与 `jt-cas` 同时失效，
  无法区分是 TGT 本身过期，还是被服务端连带吊销）
- `polymas-token` 的具体作用域与生命周期未深入测试
- 是否存在"记住我"之类的长效凭据 —— 未发现

---

## 8. 相关文档

- 加密细节 → [03-crypto.md](03-crypto.md)
- 接口清单 → [04-api-reference.md](04-api-reference.md)
- 风控体系 → [06-risk-control.md](06-risk-control.md)
