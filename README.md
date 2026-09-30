# 智慧树共享课协议分析

> 仅供技术研究，禁止商用。使用前请阅读 [DISCLAIMER.md](DISCLAIMER.md)。

## 摘要

本文分析智慧树（知到）共享学分课 Web 端的通信协议，覆盖身份认证、传输加密、
学习时长上报与风险控制四个层面。全部结论基于三类证据：真实流量捕获、
前端代码反混淆、服务端行为验证；未经验证的部分在文中明确标注。

分析的主要结果：服务端采用 `studyTotalTime += playTimes` 的累加模型，
客户端上报的累计值基本不被采信；据此定位了公开资料中未见记录的错误码 `-8`；
修正了既有实现在上报周期、完成阈值与请求来源域上的三处错误；
确认平台的反调试机制完全实现在浏览器层，因而纯 API 客户端不受其约束。

方法与工具一并开源。

---

## 1 背景

智慧树共享课采用 SPA + 微服务网关 + 统一登录中心（CAS）的架构。
本文关注四个具体问题：

1. 登录凭据的组织形式与生命周期；
2. 请求体加密方案及其密钥管理方式；
3. 学习时长的计算模型与上报机制；
4. 风控系统的检测维度与触发条件。

已有公开实现（ocsjs、fuckZHS 等）对上述问题给出过答案，但相互矛盾，
且部分结论已随平台更新失效。本文重新独立测量，并对差异之处逐项标注。

---

## 2 方法

### 2.1 证据来源

采用三种互补手段，任一单独手段均不足以支撑结论：

- **流量捕获**：Chrome NetLog（`--log-net-log`，`IncludeCookiesAndCredentials` 模式），
  可获取完整请求头、Cookie 与跳转链。
- **代码分析**：对播放器前端包反混淆（8.55 MB + 0.48 MB），提取加密算法、
  硬编码密钥、定时器周期与判定阈值。
- **行为验证**：以 `curl_cffi`（Chrome TLS 指纹 + HTTP/2）构造请求，
  通过服务端返回码反证客户端推断。

NetLog 存在固有局限：`IncludeSocketBytes` 模式记录的是网络栈字节层数据，
对 HTTPS 流量只能得到 TLS 密文，仅 SNI 可读。**HTTPS 请求体无法通过流量捕获获得**，
必须依赖代码分析。

### 2.2 反调试的应对

初期尝试以 Chrome DevTools Protocol（`Network.enable` + `Target.setAutoAttach`）
捕获流量，页面随即失效：`document.documentElement` 为 `null`，
`readyState` 持续停留于 `loading`。在不附着调试器的对照条件下页面正常加载，
可确认该现象由调试器附着触发。

反混淆后定位到相应实现：

```javascript
setInterval(checkout, 60000);          // 周期性校验原生 XMLHttpRequest 是否被替换
// 事件对象的 isTrusted 校验
// 命中后执行 notTrustScript：上报 collector2c.zhihuishu.com → 暂停播放 → 跳转
```

**该机制全部位于浏览器运行时内。** 因此不运行浏览器的客户端不受其约束——
这是架构决定的结果，而非规避手段。

### 2.3 反混淆

前端包经 obfuscator.io 处理，其自防御特性将字符串数组整体旋转若干位，
但代码中的索引仍按旋转前的位置书写。

该特性具有强隐蔽性：未经处理的还原结果语法正确、可正常执行，
但所有字符串均为错位内容，且不产生任何错误。仅从可读性判断，
极易被误判为"混淆强度过高而无法还原"。

本文以锚点法求解旋转量：选取若干"索引—期望字符串"的已知对应关系，
对候选旋转量逐一检验。`stuStudy.js` 解得 286（6 个独立锚点一致），
`app.js` 解得 140。

### 2.4 验证闭环

每条结论须通过如下闭环方可采信：

```
代码分析给出算法与常量
    → 以真实密文解密，能否还原可读明文
    → 以还原的算法构造请求，服务端是否返回 code=0
    → 两向均通过，结论成立
```

本文涉及的 7 个密钥全部通过该流程验证。

---

## 3 结果

### 3.1 服务端的累加模型

进度上报中，客户端提交 `totalStudyTime`（累计学习时长）与 `playTimes`（本次增量）。
实验表明服务端仅采信后者：

```
新 studyTotalTime = 旧 studyTotalTime + playTimes
```

验证方式为重复提交同一批次：

```
第一次  totalStudyTime=415, playTimes=540  → 服务端存储值 892 (= 352 + 540)
第二次  totalStudyTime=415, playTimes=540  → code: -8, "学习总时长下降了"
```

由此得到两条约束：**累计值必须单调不减**；服务端**不限制**该值超过视频总长度
（892 > 415 被正常接受）。

错误码 `-8` 未见于任何公开资料。

需要说明的是，本文早期曾观察到"上报值超过视频时长后触发验证码"并据此推断存在时长上限。
后续对照实验证伪了这一推断。**单点观察不足以建立因果关系。**

### 3.2 完成阈值

源码中的判定为 `percentage >= 0x51`，即 **81%**。
流传较广的 91% 说法与实测不符。实际使用中应以服务端返回的 `watchState == 1` 为准。

### 3.3 加密方案

业务接口采用 AES-128-CBC + PKCS#7 + Base64，IV 固定为 `1g3qqdh4jvbskb9x`。

固定 IV 意味着相同明文恒产生相同密文，CBC 模式应有的语义安全性因此丧失。
结合密钥硬编码于前端的事实，该方案不构成密码学意义上的机密性保护，
其实际作用是提高逆向成本。真正的安全边界在 HTTPS 与风控层。

登录接口使用同一算法但不同的密钥与 IV，请求体中密码以明文形式置于加密体内。

### 3.4 登录与会话

`jt-cas` 为 ES256 签名的 JWT，`exp - iat = 86400`，即**有效期 24 小时**。
过期后所有业务接口返回 401。

以 `CASTGC`（CAS 的 TGT）静默续期的尝试失败：服务端返回登录页 HTML 并清空该 Cookie，
表明 TGT 与 `jt-cas` 生命周期绑定，过期后须重新认证。

账号密码登录受验证码保护，实测响应：

```
不带 validate    → {"status": -1}
带失效 validate  → {"status": -4, "msg": "滑动验证码校验失败"}
```

即**必须携带有效的滑块令牌，无法脱离浏览器完成**。
扫码登录不经过 Web 端验证码，是当前唯一可行的自动化认证路径。

### 3.5 字段级陷阱

弹题列表接口返回的 `questionIds` 在 JSON 中呈现为数组，实际语义是逗号分隔字符串：

```python
{"questionIds": [295788232, 295788233]}    # → code: -1
{"questionIds": "295788232,295788233"}     # → code: 0
```

类型错误仅返回 `-1`，无进一步提示。

---

## 4 与既有实现的差异

参考实现的部分结论与实测不符。以下差异以本文实测为准：

| 项目 | 既有说法 | 实测结果 | 证据类型 |
|---|---|---|---|
| `Origin` / `Referer` | `studyh5.zhihuishu.com` | `studyvideoh5.zhihuishu.com` | 流量 |
| 主上报周期 | 30 秒 | 300 秒（`0x493e0`） | 源码 |
| 完成阈值 | 91% | 81%（`0x51`） | 源码 |
| `CASLOGC` 下发方 | `passport` | `cloudapi.polymas.com` | 流量 |
| 账号密码登录 | 可用 | 受验证码约束 | 实测 |

其中来源域一项影响显著：取值错误会导致请求进入风控判定，且失败现象不明确，定位成本较高。

---

## 5 局限与未决问题

**验证码的触发条件未完全确定。** 已排除的假设包括：TLS 指纹不符
（换用 Chrome 真实 JA3 后行为一致）、账号被标记（`exceptionActionDetail`
显示 `count: 0`、`lock: false`）、时长超限（服务端不施加限制）、请求间隔过短
（60 秒内连续两次上报均成功）。

经验性观察：削减非必要请求可显著降低触发率。在一次对照中移除"视频已达标后
补发上报"的逻辑后，连续 14 个视频的 27 次上报未触发验证码。
推测存在累积风险评分机制，但**缺乏直接证据，不作为结论**。

**`recruitAndCourseId` 的生成算法未破解。** 该标识为 34 位十六进制（17 字节），
每个字节高半字节恒为 4 或 5，形态不符合哈希特征。尝试 256 种单字节异或与
256 种常量减法均无法还原为数值。该标识可作不透明句柄原样传递，不影响使用。

**`CASTGC` 的独立有效期未确定。** 它与 `jt-cas` 同时失效，无法区分是自身到期
还是被服务端连带吊销。

**验证码令牌的生成算法未研究。** 本项工作在范围之外。

---

## 6 仓库结构

```
docs/
  01-methodology.md          取证手段、反调试分析、反混淆方法与验证闭环
  02-login-and-session.md    CAS 票据链、Cookie 分层、会话生命周期
  03-crypto.md               加密方案、密钥、ev 编码器、登录页加密
  04-api-reference.md        接口清单与参数
  05-progress-reporting.md   上报节奏、watchPoint 算法、服务端累加模型
  06-risk-control.md         反调试机制、行为风控、错误码表
  07-findings.md             按主题汇总的发现与实现陷阱

tools/
  deobf.mjs                  通用 obfuscator.io 反混淆器（自动探测数组旋转量）
  anchor.mjs                 以锚点反推字符串数组旋转量
  zhs_api_client.py          只读客户端，用于验证本文所述协议
  zhs_daily.py               将本文全部结论组装为可运行实现
```

`zhs_daily.py` 的作用是作为结论的工程证据：若文档中的描述有误，该实现无法运行。

---

## 7 使用

```bash
pip install curl_cffi pycryptodome
```

会话 Cookie 需自行从浏览器开发者工具获取。`recruitAndCourseId`
取自课程页 URL 的查询参数。

```bash
# 解析会话身份与有效期
python tools/zhs_api_client.py --cookie "<COOKIE>" whoami

# 课程信息 / 章节 / 学习状态
python tools/zhs_api_client.py --cookie "..." --course <ID> course
python tools/zhs_api_client.py --cookie "..." --course <ID> state

# 账号健康检查
python tools/zhs_api_client.py --cookie "..." --school <ID> --recruit <ID> diag

# 加密演示：加密一段 JSON 并回验解密
python tools/zhs_api_client.py --cookie x crypto '{"hello":"world"}'
```

反混淆可用于任意 obfuscator.io 产物，不限于本项目目标：

```bash
node tools/deobf.mjs input.js output.js
node tools/anchor.mjs input.js <偏移hex> <索引hex>:<期望字符串>
```

---

## 许可

代码 MIT，文档 CC BY-NC-SA 4.0。
