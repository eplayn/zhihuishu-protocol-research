## 文档索引

### 共享课（马克思主义基本原理这类）

| 文档 | 内容 |
|---|---|
| [01-methodology.md](docs/01-methodology.md) | 逆向方法论：抓包、反混淆、锚点法 |
| [02-login-and-session.md](docs/02-login-and-session.md) | 登录与会话维持 |
| [03-crypto.md](docs/03-crypto.md) | AES 加密体系 |
| [04-api-reference.md](docs/04-api-reference.md) | 接口清单 |
| [05-progress-reporting.md](docs/05-progress-reporting.md) | 进度上报机制 |
| [06-risk-control.md](docs/06-risk-control.md) | 风控与反调试 |
| [07-findings.md](docs/07-findings.md) | 结论汇总 |
| [11-共享课-作业.md](docs/11-共享课-作业.md) | **作业/单元测试：提交机制、重做、滑块、★答案神谕** |
| [12-共享课-视频与加密.md](docs/12-共享课-视频与加密.md) | 视频进度上报与加密基础 |

### 新形态课程（AI 智慧课）

| 文档 | 内容 |
|---|---|
| [08-新形态课程-体系与学习进度.md](docs/08-新形态课程-体系与学习进度.md) | 体系概览、加密、学习进度完成机制 |
| [09-新形态课程-考试掌握度.md](docs/09-新形态课程-考试掌握度.md) | 考试流程、掌握度计算、答案收割 |
| [10-课程总览接口.md](docs/10-课程总览接口.md) | 一次拿到名下所有课程与进度 |

### 核心发现速览

- **两套系统共用密码学基础**：IV 均为 `1g3qqdh4jvbskb9x`，密钥按模块分配
- **请求加密、响应明文**
- **新形态课程**：进度按「知识点+资源」单独记，视频需 `lastWatchTime ≥ refPartTime`
- **考试**：交卷后 `isCorrect` 回填，答案可直接收割；系统记历史最高分
- **共享课作业**：★ `getAnswerImgInfo` 返回的图片**只编码字母**（A/B/C/D），
  与题目无关 → 自标定字母表后，单选 1 次、多选 2 次调用即可还原完整答案
- **风控**：滑块验证是**会话级**的，过一次整条会话通畅；`eid` 为不可逆令牌

---

# 智慧树共享课协议分析

> 仅供技术研究，禁止商用。使用前请阅读 [DISCLAIMER.md](DISCLAIMER.md)。

## 摘要

本文分析智慧树（知到）共享学分课 Web 端的通信协议，覆盖身份认证、传输加密、
学习时长上报与风险控制四个层面。全部结论基于三类证据：真实流量捕获、
前端代码反混淆、服务端行为验证；未经验证的部分在第 7 节集中标注。

主要结果：服务端采用 `studyTotalTime += playTimes` 的累加模型，客户端上报的
累计值基本不被采信；据此定位了公开资料中未见记录的错误码 `-8`；复原了
`watchPoint` 采样算法与 10 元上报数组的字段语义；修正了既有实现在上报周期、
完成阈值与请求来源域上的三处错误；确认平台的反调试机制完全实现在浏览器层，
因而纯 API 客户端不受其约束。此外发现成绩构成中含独立的"学习习惯"维度。

方法与工具一并开源。

---

## 1 背景

智慧树共享课采用 SPA + 微服务网关 + 统一登录中心（CAS）的架构，
业务接口经统一网关封装，请求体在前端做对称加密。

本文关注四个问题：

1. 登录凭据的组织形式与生命周期；
2. 请求体加密方案及其密钥管理方式；
3. 学习时长的计算模型与上报机制；
4. 风控系统的检测维度与触发条件。

已有公开实现（ocsjs、fuckZHS 等）对上述问题给出过答案，但相互矛盾，
且部分结论已随平台更新失效。本文重新独立测量，差异之处在第 6 节逐项标注。

---

## 2 方法

### 2.1 证据来源与局限

采用三种互补手段，任一单独手段均不足以支撑结论。

**流量捕获。** 使用 Chrome NetLog：

```powershell
chrome.exe --user-data-dir=<独立 profile> `
  --log-net-log=capture.json `
  --net-log-capture-mode=IncludeCookiesAndCredentials `
  "https://<目标页面>"
```

捕获模式的选择是关键。NetLog 提供三档模式，其中 `IncludeSocketBytes` 名称上
最为完整，但记录的是网络栈**字节层**数据：对 HTTPS 流量而言，请求体在离开进程前
已完成加密，因此得到的是 TLS 密文，仅 SNI 可读。

**结论：HTTPS 请求体无法通过流量捕获获得。** 该限制由协议栈决定，非配置问题。

三档模式的实际产出如下：

| 模式 | 可获取内容 | 适用性 |
|---|---|---|
| `Default` | 事件元数据 | 信息量不足 |
| `IncludeCookiesAndCredentials` | 完整请求头、Cookie、响应头 | 本文所采用 |
| `IncludeSocketBytes` | TLS 密文（仅 SNI 可读） | 对 HTTPS 无实用价值 |

**代码分析。** 播放器前端包规模为 8.55 MB（`stuStudy.*.js`）与 0.48 MB（`app.*.js`），
经 obfuscator.io 处理。分析目标是提取加密算法、硬编码密钥、定时器周期与判定阈值——
这些内容仅存在于客户端代码中。

**行为验证。** 以 `curl_cffi` 的 `impersonate="chrome"` 构造请求，
该选项提供 Chrome 的 TLS 指纹（JA3）与 HTTP/2 支持。

客户端选型经过实测比对：Python `requests` 与 Node `fetch` 均使用默认 TLS 指纹
与 HTTP/1.1，在与真实浏览器行为的对照中存在差异；`curl_cffi` 消除了这一变量，
使后续排查得以聚焦于请求内容本身。

### 2.2 反调试的分析与应对

初期以 Chrome DevTools Protocol 捕获流量：

```javascript
await client.send('Network.enable');
await client.send('Target.setAutoAttach', { autoAttach: true, ... });
```

页面随即失效：`document.documentElement` 返回 `null`，`document.readyState`
持续停留于 `loading`。在不附着调试器的对照条件下页面正常加载，
可确认该现象由调试器附着触发，而非网络或服务端问题。

反混淆后定位到相应实现，共三部分：

```javascript
// ① 周期性校验原生 API 是否被替换
setInterval(checkout, 60000);   // 每 60 秒

// ② 事件对象可信度校验
//    PointerEvent / MouseEvent 的 isTrusted 属性

// ③ 命中后的 notTrustScript 流程
//    上报 collector2c.zhihuishu.com/public/jsonp/collect
//    → 暂停播放器 → 页面跳转
```

**该机制全部位于浏览器运行时内。** 不运行浏览器的客户端不会执行这段代码，
因而在架构层面不受其约束。这不是规避手段，而是观察到的必然结果；
其对方案选型的意义在第 8 节讨论。

### 2.3 反混淆：数组旋转的求解

obfuscator.io 的自防御特性将字符串数组整体旋转若干位，
但代码中的索引仍按**旋转前**的位置书写。

该特性具有强隐蔽性：未经处理的还原结果语法正确、可正常执行，
但所有字符串均为错位内容，且**不产生任何错误**。仅从可读性判断，
极易被误判为"混淆强度过高而无法还原"。

求解采用锚点法：选取若干"索引 → 期望字符串"的已知对应关系，
对候选旋转量逐一检验其是否同时满足全部锚点。

```javascript
for (let R = 0; R < arr.length; R++) {
    if (anchors.every(({idx, expect}) => arr[(idx + R) % arr.length] === expect)) {
        console.log('ROT =', R);
    }
}
```

实测结果：`stuStudy.js` 旋转量 286（6 个独立锚点一致），`app.js` 旋转量 140。
多个独立锚点收敛于同一值，可排除巧合。

### 2.4 验证闭环

每条结论须通过如下闭环方可采信：

```
代码分析给出算法与常量
    → 以真实密文解密，能否还原可读明文
    → 以还原的算法构造请求，服务端是否返回 code=0
    → 两向均通过，结论成立
```

以登录请求体为例：从 `login.zhihuishu.com` 的前端包中提取加密函数与密钥，
以该密钥解密真实捕获的登录请求体，成功还原出结构化明文；
再以同一算法构造新请求，服务端返回业务响应而非解密错误。两向验证通过。

本文涉及的 7 个密钥全部通过该流程验证。

---

## 3 协议复原

### 3.1 系统架构与主机划分

认证与业务由不同主机承担，职责划分如下：

| 主机 | 职责 | 加密密钥 |
|---|---|---|
| `login.zhihuishu.com` | 登录中心（Vue SPA） | 登录专用密钥 |
| `user.zhihuishu.com` | 账号服务 | 同上 |
| `passport.zhihuishu.com` | CAS 服务端，签发 TGT / ST | — |
| `cloudapi.polymas.com` | SSO 落地，**下发业务凭据** | — |
| `studyservice-api.zhihuishu.com` | 学习相关业务 | `VIDEO_KEY` |
| `onlineservice-api.zhihuishu.com` | 课程列表、首页 | `HOME_KEY` |
| `newbase.zhihuishu.com` | 视频播放地址 | — |

需要特别指出：业务身份凭据 `CASLOGC` 由 `cloudapi.polymas.com` 下发，
而非 `passport`。此项与部分公开实现的描述不符，影响凭据刷新逻辑的正确性。

### 3.2 认证链路

完整跳转链如下（实测）：

```
① GET  login.zhihuishu.com/?origin=zhs
       登录中心页面，用户在此输入凭据或扫码

② POST user.zhihuishu.com/zhsuser/login/account
       Content-Type: application/json;charset=UTF-8
       Body: <AES 加密后的 Base64 字符串>
       响应: {status, rt: {oncePassword | ...}}

③ GET  user.zhihuishu.com/zhsuser/certify/queryCertify/<uuid>
       查询认证状态，可能触发二次校验

④ GET  passport.zhihuishu.com/login
           ?pwd=<一次性密码>
           &service=https://cloudapi.polymas.com/oauth/login/ssoLogin
                    ?redirect=https://www.zhihuishu.com

⑤ 302  Location: .../ssoLogin?ticket=ST-...      CAS 签发 Service Ticket

⑥ GET  cloudapi.polymas.com/oauth/login/ssoLogin?ticket=ST-...
       ★ 在此 Set-Cookie：CASLOGC / CASTGC / jt-cas / polymas-token

⑦ 302  → https://www.zhihuishu.com/

⑧ 各子系统再各自换票
```

平台提供两种凭据输入方式，最终均汇聚至第 ④ 步的 `pwd=` 一次性密码机制。

**账号密码方式。** 请求体解密后结构为：

```json
{
  "account":  "<账号>",
  "password": "<密码>",
  "validate": "<易盾滑块令牌>"
}
```

密码在加密体内以明文形式存在，传输保密性实际由 HTTPS 提供。
该方式受验证码约束，详见 §4.2。

**扫码方式。** 涉及两个端点：

```
GET  passport.zhihuishu.com/qrCodeLogin/getLoginQrImg
     → { "qrToken": "...", "img": "<base64 PNG>" }

GET  passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo?qrToken=...
     轮询获取状态
```

状态码语义如下：

| status | 含义 |
|---|---|
| `-1` | 未扫码 |
| `0` | 已扫码，等待手机确认 |
| `1` | 确认成功，`oncePassword` 已下发 |
| `2` | 二维码过期 |
| `3` | 用户取消 |

`status == 1` 后取 `oncePassword` 进入第 ④ 步，后续链路与账号密码方式一致。

### 3.3 凭据与会话

**`CASLOGC`** 为 URL 编码的 JSON，含 8 个字段：

```json
{
  "realName":   "<姓名>",
  "myuniRole":  0,
  "myinstRole": 0,
  "userId":     <数字用户 ID>,
  "headPic":    "<头像 URL>",
  "uuid":       "<短标识>",
  "mycuRole":   0,
  "username":   "<哈希用户名>"
}
```

其中 `uuid` 用于构造上报字段（见 §3.8）。

**Cookie 分层。** 以 `studyservice-api` 为例，实测收到 10 个 Cookie，
按职责分为四层：

| 层 | Cookie | 作用 |
|---|---|---|
| CAS 票据 | `CASTGC` | TGT，用于免密换新票据 |
| 业务身份 | `CASLOGC`, `jt-cas` | 身份声明与会话 JWT |
| 会话 | `JSESSIONID`, `SESSION` | 各子域私有 |
| 风控路由 | `SERVERID`, `acw_tc`, `INGRESSCOOKIE` | 负载均衡、WAF |

构造请求时只需携带顶级域 `.zhihuishu.com` 的 Cookie；
各子域私有的 `JSESSIONID` 不应混入，否则冲突。

另有 `exitRecod_<uuid>` 由浏览器端 JS 设置，纯 API 客户端需手动补上。

**会话生命周期。** `jt-cas` 为 ES256 签名的 JWT，载荷如下：

```json
{
  "iss": "com.zhihuishu", "aud": "CAS", "sub": "<姓名>",
  "iat": 1790581237, "exp": 1790667637,
  "jti": "<UUID v4>", "uid": <用户 ID>
}
```

`exp - iat = 86400`，即**有效期 24 小时**。过期后所有业务接口返回 401。

以 `CASTGC` 静默续期的尝试失败：

```
GET passport.zhihuishu.com/login?service=<ssoLogin URL>   （携带旧 CASTGC）
→ 200 OK，返回登录页 HTML（约 84 KB），无跳转链
→ 服务端清空 CASTGC
```

表明 TGT 与 `jt-cas` 生命周期绑定，过期后须重新认证。

此外平台实施单点登录语义：在别处登录会吊销现有服务端会话，
浏览器与 API 客户端共享同一套会话状态。

### 3.4 请求封装

业务接口统一封装格式：

```http
POST https://studyservice-api.zhihuishu.com/gateway/t/v1<path>
Content-Type: application/x-www-form-urlencoded
Origin:   https://studyvideoh5.zhihuishu.com
Referer:  https://studyvideoh5.zhihuishu.com/

secretStr=<Base64(AES(json))>&dateFormate=<毫秒时间戳>
```

三个细节：

`dateFormate` **出现两次**——加密体内一份、表单外层明文一份，两者须一致。

其生成方式为 `Math.floor(Date.now() / 1000) * 1000`，即**先取整秒再乘 1000**，
故末三位恒为 `000`。直接使用 `int(time.time() * 1000)` 会产生随机末三位，
构成可识别的特征差异。

成功判定依据响应体的 `code` 字段而非 HTTP 状态码：
HTTP 200 完全可能携带 `code: -12`。

### 3.5 加密方案

业务接口采用 AES-128-CBC + PKCS#7 + Base64，IV 全局固定为 `1g3qqdh4jvbskb9x`。

不同子系统使用不同密钥：

| 密钥 | 适用目标 |
|---|---|
| `azp53h0kft7qi78q` | `studyservice-api`（学习相关） |
| `7q9oko0vqb3la20r` | `onlineservice-api` |
| `hw2fdlwcj4cs1mx7` | `kg-ai-run` |
| `onbfhdyvz8x7otrp` | 考试相关 |
| `kcGOlISPkYKRksSK` | 问答相关 |

**固定 IV** 意味着相同明文恒产生相同密文，CBC 模式应有的语义安全性因此丧失。
结合密钥硬编码于前端的事实，该方案不构成密码学意义上的机密性保护，
其作用是提高逆向成本。真正的安全边界在 HTTPS 与风控层。

登录接口使用同一算法但不同的密钥与 IV，输出为 Base64 字符串而非 JSON。
前端以白名单决定哪些接口需要加密，白名单包含 `/login/account`、
`/user/register`、`/reset/resetPassword`、`/login/bySmsCode` 等条目。

### 3.6 ev 编码器

上报字段 `sdsew` 使用自定义编码，前端实现如下：

```javascript
KEY = "zzpttjd";
Z(arr) = X(arr.join(';'));

function X(str) {
  let out = "";
  for (let i = 0; i < str.length; i++) {
    let n = str.charCodeAt(i) ^ KEY.charCodeAt(i % KEY.length);
    let h = n.toString(16);
    if (h.length < 2) h = "0" + h;
    out += h.slice(-4);
  }
  return out;
}
```

即：数组以 `;` 连接为字符串，逐字符与密钥循环异或，转为十六进制并补零至两位，
取末四位后拼接。

`slice(-4)` 在多数情况下不改变结果，但属前端原文的一部分，须予保留。

### 3.7 接口清单

学习主流程相关接口：

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 课程信息 | POST | `/learning/queryCourse` | `{recruitAndCourseId}` |
| 章节视频树 | POST | `/learning/videolist` | `{recruitAndCourseId}` |
| 学习状态 | POST | `/learning/queryStuyInfo` | `{lessonIds, lessonVideoIds, recruitId}` |
| 上报令牌 | POST | `/learning/prelearningNote` | `{ccCourseId, chapterId, isApply:1, lessonId, lessonVideoId, recruitId, videoId}` |
| 主进度上报 | POST | `/learning/saveDatabaseIntervalTimeV2` | `{ewssw, sdsew, zwsds, courseId}` |
| 缓存上报 | POST | `/learning/saveCacheIntervalTimeV2` | `{ewssw, sdsew, zwsds}` |
| 服务器时间 | GET | `/learning/currentSystemTime` | — |
| 上次观看 | POST | `/learning/queryUserRecruitIdLastVideoId` | `{recruitId}` |

需要注意 `queryStuyInfo`（学习状态）与 `queryStudyInfo` 是两个不同路径，
后者实测返回 404。

弹题相关接口：

| 用途 | 方法 | 路径 |
|---|---|---|
| 弹题列表 | POST | `/popupAnswer/loadVideoPointerInfo` |
| 弹题内容 | POST | `/popupAnswer/lessonPopupExam` |
| 提交作答 | POST | `/popupAnswer/saveLessonPopupExamSaveAnswer` |

风控与状态查询接口：

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 异常行为记录 | GET | `/cheat/exceptionActionDetail` | `{schoolId, recruitId}` |
| 学习时长限制 | GET | `/course/queryStudiedTimeLimitInfo` | `{recruitId, schoolId}` |
| 滑块校验 | POST | `/learning/validateSlideToken` | `{token, ev, checkType:2}` |
| 成绩规则 | POST | `/learning/queryStudyReadBefore` | `{courseId, recruitId}` |

播放地址不经加密接口，而是 JSONP：

```http
GET https://newbase.zhihuishu.com/video/initVideo
      ?jsonpCallBack=result&videoID=<id>&_=<时间戳>
```

**课程标识。** 平台使用三级标识：

| 标识 | 含义 | 变化频率 |
|---|---|---|
| `courseId` | 课程本身 | 长期稳定 |
| `recruitId` | 招生批次（某次开班） | 每学期 |
| `recruitAndCourseId` | 合并外部标识 | 每次开班 |

`recruitAndCourseId` 为 34 位十六进制（17 字节），每个字节高半字节恒为 4 或 5。
该形态不符合哈希特征，其生成算法未破解（见 §7）。
获取途径有二：课程页 URL 的查询参数，或
`onlineservice-api` 的 `queryShareCourseInfo` 响应中 `courseOpenDtos[].secret` 字段。

共享课与校内课以标识中是否含字母区分，后者使用另一套签名机制，本文未覆盖。

### 3.8 进度上报机制

**定时器。** 从反混淆源码提取，共四个（数值为十六进制原文）：

| 定时器 | 原文 | 十进制 | 职责 |
|---|---|---|---|
| `learningTimeRecord` | `0x7c6` | 1990 ms | 追加 `watchPoint` 采样 |
| `totalStudyTimeFun` | `0x137e` | 4990 ms | 学习时长心跳 |
| `cacheInterval` | `0x2bf20` | 180000 ms | 缓存上报 |
| `databaseInterval` | `0x493e0` | 300000 ms | **主上报** |

主上报周期为 **300 秒**，非部分资料所述的 30 秒。

**心跳。** 每 4990 ms 执行 `totalStudyTime += 5 * playRate`，
其中 `playRate` 在前端被钳制于 1.8。同一次心跳同时递增 `playTimes`
（本次未上报的增量）与 `totalTimeFinish`。

**`watchPoint`（字段 `ewssw`）。** 每 1990 ms 追加一个采样值：

```javascript
const val = parseInt(totalStudyTime / 5) + 2;
watchPointPost = (watchPointPost === '' ? "0,1," : watchPointPost + ',') + val;
```

初始值为 `"0,1"`，最终形态为逗号分隔的十进制数字串。
由于 `totalStudyTime` 持续增长，采样值通常呈阶梯上升。
重看已完成视频时 `totalStudyTime` 被钳制于视频总长度不再增长，
采样值将退化为恒定值重复，此为真实客户端同样会产生的形态。

**上报数组 `sdsew`。** 共 10 个元素，顺序即语义：

| 索引 | 含义 |
|---|---|
| 0 | `recruitId` |
| 1 | `lessonId` |
| 2 | `smallLessonId` |
| 3 | `videoId` |
| 4 | `chapterId` |
| 5 | `studyStatus` |
| 6 | `parseInt(playTimes)` — 本次增量 |
| 7 | `parseInt(totalStudyTime)` — 累计时长 |
| 8 | `round(player.getPosition())` — 播放器实时位置 |
| 9 | `uuid + "zhs"` |

第 9 个元素是字符串拼接结果，后缀 `"zhs"` 不可省略。

**上报条件。** 源码中的判定为：

```javascript
if (playTimes <= 5 && !force) {
    totalStudyTime -= playTimes;   // 回退，不提交
    playTimes = 0;
} else {
    // 构造并提交
}
```

即增量过小时不作提交，而是回退累加值等待下一轮。
切换视频时以 `force=true` 强制冲刷。

**完成阈值。** 判定为 `percentage >= 0x51`，即 **81%**。
流传较广的 91% 说法与实测不符。实际使用中应以服务端返回的
`watchState == 1` 为权威依据，取值语义为 `0`（未观看）、`1`（已完成）、`2`（部分）。

### 3.9 服务端累加模型

客户端提交两个时长字段：`totalStudyTime`（累计值）与 `playTimes`（增量）。
实验表明服务端**仅采信后者**：

```
新 studyTotalTime = 旧 studyTotalTime + playTimes
```

验证方式为重复提交同一批次：

```
第一次  totalStudyTime=415, playTimes=540  → 服务端存储值 892 (= 352 + 540)
第二次  totalStudyTime=415, playTimes=540  → code: -8, "学习总时长下降了"
```

由此得到两条约束：

1. **累计值必须单调不减**，否则返回 `-8`；
2. 服务端**不限制**该值超过视频总长度（892 > 415 被正常接受）。

错误码 `-8` 未见于任何公开资料。

需要说明的是，本文早期曾观察到"上报值超过视频时长后触发验证码"
并据此推断存在时长上限。后续对照实验证伪了这一推断：
服务端接受任意大的累计值。**单点观察不足以建立因果关系**，
本文将此列为方法论层面的教训。

正确的构造方式为先读取服务端当前值，再以增量递增提交。

### 3.10 弹题机制

弹题由服务器下发答案。`loadVideoPointerInfo` 返回题目列表及时间点，
`lessonPopupExam` 返回题目内容与选项，其中正确选项以 `result == '1'` 标记。

作答接口 `saveLessonPopupExamSaveAnswer` 以**单题**为粒度，
`testQuestionId` 为单个题目 ID，多小题需分别提交。

存在一处类型陷阱：`loadVideoPointerInfo` 返回的 `questionIds`
在 JSON 中呈现为数组，实际语义是**逗号分隔字符串**：

```python
{"questionIds": [295788232, 295788233]}    # → code: -1
{"questionIds": "295788232,295788233"}     # → code: 0
```

类型错误仅返回 `-1`，无进一步提示，定位成本较高。

---

## 4 风控分析

### 4.1 检测机制

**反调试。** 实现细节与应对见 §2.2。要点是：该机制位于浏览器运行时内，
对不运行浏览器的客户端不产生约束。

**异常行为记录。** 通过 `/cheat/exceptionActionDetail` 查询：

```json
{ "count": 0, "lock": false, "punishType": 0, "popWindow": false }
```

| 字段 | 含义 |
|---|---|
| `count` | 异常行为累计次数 |
| `lock` | 是否被锁定 |
| `punishType` | 处罚类型（0 为无） |
| `popWindow` | 是否弹窗警告 |

该接口是判断账号状态的权威依据。风控排查应首先查询此接口：
若 `count: 0` 且 `lock: false`，则问题不在账号层面，应转向单次请求内容的分析。

**多会话检测。** 服务端返回 `-10`，前端提示同时播放多个视频。

**学习时长限制。** 通过 `/course/queryStudiedTimeLimitInfo` 查询，
超限时返回 `-9`。

### 4.2 验证码

业务接口返回 `-12` 时前端弹出滑块。登录接口同样受其保护，
实测响应如下：

```
不带 validate    → {"status": -1}
带失效 validate  → {"status": -4, "msg": "滑动验证码校验失败", "rt": {"pwdErrorCount": 1}}
```

即**账号密码登录必须携带有效的滑块令牌，无法脱离浏览器完成**。
扫码登录不经过 Web 端验证码，是当前唯一可行的自动化认证路径。

失败尝试会递增响应中的 `pwdErrorCount`，研究过程中应避免反复试探。

业务侧 `-12` 呈现**会话级**特征：一次触发后后续所有写请求均返回 `-12`
（即使内容正常），直至在浏览器中正常观看视频若干分钟后方恢复。
该现象被独立复现两次。推测浏览器加载滑块 SDK 并完成一次校验后会刷新会话的风控状态。

### 4.3 错误码

以下为实测汇总，`-8` 为本文补充：

| code | 含义 | 触发场景 |
|---|---|---|
| `0` | 成功 | — |
| `-8` | 学习总时长下降了 | 提交的累计值小于服务端已存值 |
| `-9` | 学习时间已结束 | 超出允许的学习时间窗 |
| `-10` | 同时播放多个视频 | 多会话检测 |
| `-12` | 需要滑块验证 | 风控触发 |
| `403` | 登录过期 | 会话失效 |
| `422` | 参数或状态异常 | 请求内容不合法 |

以上均为**业务层**错误码，出现在 HTTP 200 的响应体内。

---

## 5 成绩构成

`/learning/queryStudyReadBefore` 返回课程时间线与成绩规则：

```json
{
  "courseStart": "YYYY-MM-DD", "courseEnd": "YYYY-MM-DD",
  "examStart":   "YYYY-MM-DD HH:mm", "examEnd": "YYYY-MM-DD HH:mm",
  "makeUpInfo":  "...",
  "scoreAssessRule": {
    "totalScore": "100",
    "usualScoreShare":           "50",
    "progressScoreShare":        "20",
    "learnedBehaviorScoreShare": "20",
    "learningHabitScore":        "20",
    "learningInteractScore":     "10",
    "finalExamScoreShare":       "40"
  }
}
```

其中 `learningHabitScore`（学习习惯）为独立配置项，说明该维度确为成绩组成部分
且权重可观。具体数值因课程与学校而异。

客户端侧对应实现为 `habitWarn`：本地累计计时，满 1500 秒（25 分钟）后调用
`/learning/currentSystemTime` 取**服务器时间**打标，写入 localStorage。
计时仅在前端进行，实际计分由服务端依据学习记录计算。

---

## 6 与既有实现的差异

参考实现的部分结论与实测不符。以下差异以本文实测为准：

| 项目 | 既有说法 | 实测结果 | 证据类型 |
|---|---|---|---|
| `Origin` / `Referer` | `studyh5.zhihuishu.com` | `studyvideoh5.zhihuishu.com` | 流量 |
| 主上报周期 | 30 秒 | 300 秒（`0x493e0`） | 源码 |
| 完成阈值 | 91% | 81%（`0x51`） | 源码 |
| `CASLOGC` 下发方 | `passport` | `cloudapi.polymas.com` | 流量 |
| 账号密码登录 | 可用 | 受验证码约束 | 实测 |
| 缓存上报 | `saveCacheIntervalTime` | 存在 `V2` 版本且不含 `courseId` | 源码 |
| 数组旋转 | 未提及 | 须处理，否则字符串全错 | 源码 |

来源域一项影响显著：取值错误会使请求进入风控判定，
且失败现象不明确，定位成本较高。

---

## 7 局限与未决问题

**验证码的触发条件未完全确定。** 已排除的假设包括：

| 假设 | 排除依据 |
|---|---|
| TLS 指纹不符 | 换用 Chrome 真实 JA3 + HTTP/2 后行为一致 |
| 账号被标记 | `exceptionActionDetail` 显示 `count: 0`、`lock: false` |
| 累计值超限 | 服务端接受 892 > 415 |
| 请求间隔过短 | 60 秒内连续两次上报均成功 |

经验性观察：削减非必要请求可显著降低触发率。在一次对照中移除
"视频已达标后补发上报"的逻辑后，连续 14 个视频的 27 次上报未触发验证码。
推测存在累积风险评分机制，但**缺乏直接证据，不作为结论**。

**`recruitAndCourseId` 的生成算法未破解。** 该标识为 34 位十六进制，
每个字节高半字节恒为 4 或 5。尝试 256 种单字节异或与 256 种常量减法，
均无法还原为数值。该标识可作不透明句柄原样传递，不影响使用。

**`CASTGC` 的独立有效期未确定。** 它与 `jt-cas` 同时失效，
无法区分是自身到期还是被服务端连带吊销。

**验证码令牌的生成算法未研究。** 本项工作在研究范围之外。

**风控评分的维度未知。**

---

## 8 仓库结构

```
docs/
  01-methodology.md          取证手段、反调试分析、反混淆方法与验证闭环
  02-login-and-session.md    CAS 票据链、Cookie 分层、会话生命周期
  03-crypto.md               加密方案、密钥、ev 编码器、登录页加密
  04-api-reference.md        接口清单与参数细节
  05-progress-reporting.md   上报节奏、watchPoint 算法、服务端累加模型
  06-risk-control.md         反调试机制、行为风控、错误码表
  07-findings.md             按主题汇总的发现与实现陷阱

  08-新形态课程-体系与学习进度.md   另一套课程系统（AI 智慧课）的体系与进度机制
  09-新形态课程-考试掌握度.md       考试流程、掌握度计算、答案收割
  10-课程总览接口.md              一次拿到名下所有课程与进度
  11-共享课-作业.md               作业/单元测试：提交、重做、滑块、★答案神谕
  12-共享课-视频与加密.md           视频进度上报与加密基础

tools/
  deobf.mjs                  通用 obfuscator.io 反混淆器（自动探测数组旋转量）
  anchor.mjs                 以锚点反推字符串数组旋转量
  zhs_api_client.py          只读客户端，用于验证本文所述协议
  zhs_daily.py               将本文全部结论组装为可运行实现
```

各文档与本文的分工：本文（1~7 章）聚焦**共享课**的视频与学习进度；
`docs/08~10` 覆盖**另一套独立系统**（新形态课程 / AI 智慧课），
`docs/11~12` 是共享课的作业与视频细目。

`zhs_daily.py` 的作用是作为结论的工程证据：若文档中的描述有误，该实现无法运行。

---

## 9 使用

```bash
pip install curl_cffi pycryptodome
```

会话 Cookie 需自行从浏览器开发者工具获取：
打开学习页，在 Network 面板中选取任一 `studyservice-api` 请求，
复制 Request Headers 中的 Cookie 字段。

`recruitAndCourseId` 取自课程页 URL 的查询参数。

```bash
# 解析会话身份与有效期
python tools/zhs_api_client.py --cookie "<COOKIE>" whoami

# 课程信息 / 章节 / 学习状态
python tools/zhs_api_client.py --cookie "..." --course <ID> course
python tools/zhs_api_client.py --cookie "..." --course <ID> chapters
python tools/zhs_api_client.py --cookie "..." --course <ID> state

# 账号健康检查
python tools/zhs_api_client.py --cookie "..." --school <ID> --recruit <ID> diag

# 成绩规则与课程时间线
python tools/zhs_api_client.py --cookie "..." --course-id <ID> --recruit <ID> rules

# 加密演示：加密一段 JSON 并回验解密
python tools/zhs_api_client.py --cookie x crypto '{"hello":"world"}'
```

反混淆工具可用于任意 obfuscator.io 产物，不限于本项目目标：

```bash
node tools/deobf.mjs input.js output.js

# 解不出旋转量时，先用锚点反推
node tools/anchor.mjs input.js <偏移hex> <索引hex>:<期望字符串>
ROT=<n> node tools/deobf.mjs input.js output.js
```

---

## 许可

代码 MIT，文档 CC BY-NC-SA 4.0。
