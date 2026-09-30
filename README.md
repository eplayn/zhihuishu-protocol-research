# 智慧树（知到）共享课 · 协议逆向研究

> 对**智慧树 / 知到**共享学分课 Web 端的一次完整的、以**实测为唯一依据**的协议分析。
> 覆盖登录态、加密层、接口清单、进度上报机制、弹题机制与风控体系。
>
> **仅供学习与研究使用。详见 [DISCLAIMER.md](DISCLAIMER.md)。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Research](https://img.shields.io/badge/type-protocol%20research-blue.svg)](#)
[![No Warranty](https://img.shields.io/badge/warranty-none-red.svg)](DISCLAIMER.md)

---

## ⚠️ 免责声明（先读这个）

本项目为**网络安全与协议分析的学习研究**，目的是理解现代 Web 应用在
**会话管理、传输加密、行为风控**方面的工程实现。

- ❌ **不鼓励、不支持**任何违反平台服务条款、学术诚信规范或当地法律的行为
- ❌ 作者**不对**任何使用者的行为及后果负责
- ✅ 所有数据均来自**公开可访问的前端资源**与**研究者本人账号的授权测试**
- ✅ 仓库中**不含**任何真实个人信息、会话凭据或第三方数据

**完整条款见 [DISCLAIMER.md](DISCLAIMER.md)。若你不同意，请立即停止使用本仓库。**

---

## 目录

- [一、研究背景与目标](#一研究背景与目标)
- [二、研究方法](#二研究方法)
- [三、研究过程](#三研究过程)
- [四、核心成果](#四核心成果)
- [五、关键发现](#五关键发现)
- [六、仓库结构](#六仓库结构)
- [七、快速开始](#七快速开始)
- [八、错误码速查](#八错误码速查)
- [九、踩坑记录](#九踩坑记录)
- [十、参考项目](#十参考项目)

---

## 一、研究背景与目标

智慧树（知到）是国内主要的高校在线课程平台之一，其**共享学分课**采用
SPA + 微服务网关 + 统一登录中心（CAS）的架构。

**研究目标**（纯技术视角）：

| # | 目标 | 说明 |
|---|---|---|
| 1 | 搞清**登录态如何保存与续期** | CAS 票据链、cookie 分层、JWT 生命周期 |
| 2 | 还原**传输层加密方案** | 前端如何加密请求体、密钥如何管理 |
| 3 | 梳理**业务接口** | 课程、章节、进度、弹题的调用关系 |
| 4 | 理解**学习时长的计算与上报模型** | 客户端节奏、服务端累加规则 |
| 5 | 分析**风控体系** | 反调试、行为检测、验证码触发条件 |

---

## 二、研究方法

本次研究坚持一个原则：**所有结论必须有实测证据，不接受任何推测。**

### 2.1 三种取证手段（并用、互相印证）

| 手段 | 工具 | 拿到什么 | 局限 |
|---|---|---|---|
| **真实流量捕获** | Chrome NetLog<br>`--log-net-log` + `IncludeCookiesAndCredentials` | 完整请求头、响应头、Cookie、跳转链 | **拿不到 HTTPS 请求体**（TLS 密文） |
| **前端 JS 逆向** | 自研 obfuscator.io 反混淆器<br>[`tools/deobf.mjs`](tools/deobf.mjs) | 加密算法、密钥、上报节奏、判定阈值 | 需处理字符串数组旋转 |
| **API 独立复现** | `curl_cffi`（Chrome TLS 指纹 + HTTP/2） | 验证推断是否正确 | 会被风控；需谨慎 |

### 2.2 为什么不用调试器（DevTools / CDP）

> **这是本次研究最重要的方法学决策。**

研究早期尝试用 Chrome DevTools Protocol（`Network.enable` + `Target.setAutoAttach`）抓包，
结果**页面直接白屏、`readyState` 永远停在 `loading`**。

**对照实验**（不开 CDP，页面正常）证明：**平台检测到调试器附着后会主动破坏页面。**

```js
// 反混淆后定位到的反调试逻辑（浏览器层）
setInterval(checkout, 60000);     // 每 60 秒校验原生 XMLHttpRequest 是否被 hook
// PointerEvent 上的 isTrusted 检查
// notTrustScript 命中 → 上报 collector2c.zhihuishu.com → 暂停播放 + 跳转
```

**结论：** 该平台的反调试**完全实现在浏览器层**。
因此，**任何不经过浏览器的 API 客户端天然绕过全部反调试机制** ——
这既是研究结论，也是本次方法论的核心。

---

## 三、研究过程

### 阶段 0 · 首次接触（走弯路）

- 尝试从 Edge 的 cookie 数据库直接读取 → **失败**（浏览器运行时数据库被独占锁）
- 尝试 `robocopy /B` 备份绕过 → **失败**（需要 Backup 权限，非管理员不可用）
- **教训**：不要跟浏览器的数据库较劲，走网络层。

### 阶段 1 · 建立可信的流量捕获通道

最终方案：**Chrome NetLog**

```
chrome.exe --user-data-dir=<干净 profile> \
  --log-net-log=capture.json \
  --net-log-capture-mode=IncludeCookiesAndCredentials
```

`IncludeCookiesAndCredentials` 能拿到**完整请求头 + Cookie**，足以还原登录链路。

> **发现**：`IncludeSocketBytes` 模式拿到的是 **TLS 密文**，
> 只有 SNI 可读，对 HTTPS 请求体毫无帮助。**这是很多人会踩的坑。**

### 阶段 2 · 登录链路还原

从 NetLog 的跳转链里逐跳还原出完整 SSO 流程（见
[docs/02-login-and-session.md](docs/02-login-and-session.md)）。

### 阶段 3 · 前端 JS 反混淆（本次技术难点）

播放器主包 `stuStudy.*.js` 有 **8.5 MB**，经 [obfuscator.io](https://obfuscator.io/) 处理：

- 字符串数组 + 索引引用
- **自防御：数组旋转**（self-defending rotation）

**突破方法：** 用已知的"索引 → 字符串"锚点反推旋转量。

```bash
node tools/anchor.mjs          # 用已知锚点求 ROT
node tools/deobf.mjs in.js out.js   # ROT=286 node tools/deobf.mjs ...
```

实测结果：`stuStudy.js` 旋转量 **286**（6/6 锚点一致），`app.js` 旋转量 **140**。

### 阶段 4 · 加密层还原

反混淆后直接定位到加密函数与硬编码密钥，并用真实密文**双向验证**
（加密 → 与服务端通信成功；解密 → 还原出可读明文）。

**7/7 个密钥全部实测验证通过。**

### 阶段 5 · 上报机制还原

从 `startTotalTimer` 提取定时器周期，从 `saveDdsjkTimeInWhileFn` 提取上报条件与数组结构。

### 阶段 6 · 服务端行为验证

用独立 API 客户端（`curl_cffi`，Chrome TLS 指纹）实测调用，
通过**服务端返回码**反向确认客户端推断。

---

## 四、核心成果

### 4.1 完整协议文档

| 文档 | 内容 |
|---|---|
| [docs/02-login-and-session.md](docs/02-login-and-session.md) | CAS 票据链、cookie 分层、24 小时 JWT 生命周期 |
| [docs/03-crypto.md](docs/03-crypto.md) | AES-128-CBC 方案、全部密钥、ev 编码器 |
| [docs/04-api-reference.md](docs/04-api-reference.md) | 20+ 接口清单与参数 |
| [docs/05-progress-reporting.md](docs/05-progress-reporting.md) | 上报节奏、watchPoint 算法、服务端累加模型 |
| [docs/06-risk-control.md](docs/06-risk-control.md) | 反调试、行为风控、验证码触发条件 |
| [docs/07-findings.md](docs/07-findings.md) | **关键发现与踩坑合集** |

### 4.2 可复用工具

| 工具 | 用途 |
|---|---|
| [`tools/deobf.mjs`](tools/deobf.mjs) | **通用 obfuscator.io 反混淆器**（自动探测数组旋转量） |
| [`tools/anchor.mjs`](tools/anchor.mjs) | 从已知锚点反推字符串数组旋转量 |
| [`tools/zhs_api_client.py`](tools/zhs_api_client.py) | 只读 API 客户端（查课程 / 章节 / 学习状态） |

### 4.3 量化成果

```
反混淆代码量        : 9.0 MB（8.55 MB + 0.48 MB）
还原接口数          : 20+
实测验证的密钥      : 7 / 7
端到端跑通的视频    : 31 / 31
```

---

## 五、关键发现

以下每一条都有实测证据，其中**多条推翻了公开参考项目的结论**。

| # | 发现 | 说明 |
|---|---|---|
| 1 | **`CASLOGC` 由 `cloudapi.polymas.com` 下发** | 不是 `passport`；参考项目的模型有误 |
| 2 | **`Origin`/`Referer` 必须是 `studyvideoh5`** | 参考项目用的是已废弃的 `studyh5` |
| 3 | **完成阈值是 81%（`0x51`）** | 不是广泛流传的 91% |
| 4 | **主上报周期是 300 秒** | 不是 30 秒 |
| 5 | **服务端是 `studyTotalTime += playTimes` 累加** | 它**忽略**客户端上报的 `totalStudyTime` |
| 6 | **`totalStudyTime` 必须单调不减** | 否则返回 `-8`（新发现的错误码） |
| 7 | **`questionIds` 必须是字符串** | 传数组会返回 `-1` |
| 8 | **`Origin`/`Referer` 的值影响风控判定** | 用错域名会显著提高被拦概率 |
| 9 | **平台反调试全在浏览器层** | API 客户端天然绕过 |
| 10 | **`dateFormate` 恒以 `000` 结尾** | = `int(time.time()) * 1000` |

> 详见 [docs/07-findings.md](docs/07-findings.md)

---

## 六、仓库结构

```
.
├── README.md                       # 本文件
├── DISCLAIMER.md                   # 免责声明（务必阅读）
├── LICENSE                         # MIT
├── .gitignore
├── docs/
│   ├── 01-methodology.md           # 研究方法论与取证手段
│   ├── 02-login-and-session.md     # 登录态与会话管理
│   ├── 03-crypto.md                # 传输层加密
│   ├── 04-api-reference.md         # 接口清单
│   ├── 05-progress-reporting.md    # 进度上报机制
│   ├── 06-risk-control.md          # 风控与反调试
│   └── 07-findings.md              # 关键发现与踩坑
└── tools/
    ├── deobf.mjs                   # obfuscator.io 反混淆器
    ├── anchor.mjs                  # 旋转量反推
    └── zhs_api_client.py           # 只读 API 客户端
```

---

## 七、快速开始

### 环境

```bash
Python >= 3.10
Node.js >= 18

pip install curl_cffi pycryptodome
```

### 反混淆任意 obfuscator.io 产物

```bash
# 自动探测旋转量并还原
node tools/deobf.mjs obfuscated.js deobfuscated.js
```

### 只读查询课程信息

```bash
# 需要一个有效的会话 Cookie（自行从浏览器导出）
python tools/zhs_api_client.py --cookie "CASLOGC=...; jt-cas=..." course
python tools/zhs_api_client.py --cookie "..." chapters
python tools/zhs_api_client.py --cookie "..." state
```

---

## 八、错误码速查

| code | 含义 | 备注 |
|---|---|---|
| `0` | 成功 | 业务成功判定用 **code 而非 HTTP 状态码** |
| `-8` | **学习总时长下降了** | 本文首次记录；`totalStudyTime` 必须单调不减 |
| `-9` | 学习时间已结束 | 超出课程允许的学习时间窗 |
| `-10` | 同时播放多个视频 | 多开检测 |
| `-12` | 需要滑块验证 | 易盾；会话级状态 |
| `403` | 登录过期 | 需重新登录 |
| `422` | 参数或状态异常 | 播放器会暂停 |

---

## 九、踩坑记录

> 完整版见 [docs/07-findings.md](docs/07-findings.md)，这里是浓缩版。

1. **NetLog 的 `IncludeSocketBytes` 拿不到 HTTPS 请求体** —— 拿到的是 TLS 密文
2. **CDP 调试会破坏页面** —— 反调试会主动让文档失效，必须换 NetLog 方案
3. **反混淆必须处理数组旋转** —— 否则所有字符串索引全是错的，且症状隐蔽
4. **`questionIds` 类型陷阱** —— 文档里没写，传错类型只返回一个语焉不详的 `-1`
5. **`Origin` 写错会踩风控** —— 用参考项目的旧域名会显著提高被拦概率
6. **运行中的浏览器无法读取 cookie 数据库** —— 数据库被独占锁，非管理员也无法用备份 API 绕过
7. **PowerShell 的 `Set-Content -Encoding UTF8` 会写 BOM** —— 可能导致脚本解析失败

---

## 十、参考项目

研究过程中参考了以下公开项目的**思路**（但其具体结论均经过本次实测复核，
多处已被证伪或修正）：

- [ocsjs/ocsjs](https://github.com/ocsjs/ocsjs) —— 纯 DOM 自动化的思路
- [fuckZHS](https://github.com/search?q=fuckZHS) —— 早期 API 层实现

> **重要**：本项目**不是**它们的复刻。所有协议细节均来自本次独立实测，
> 与参考项目不一致之处，以本项目实测结果为准（并在文档中标注了差异）。

---

## 许可

代码：[MIT](LICENSE) ｜ 文档：CC BY-NC-SA 4.0

**使用即表示你已阅读并同意 [DISCLAIMER.md](DISCLAIMER.md)。**
