# 01 · 研究方法论

> 本文记录本次协议研究**实际采用的方法**，包括走过的弯路。
> 目的是让结论**可复现、可证伪**。

---

## 0. 核心原则

> **所有结论必须有实测证据。不接受任何"应该是这样"的推测。**

具体表现为：

1. 每一条协议细节，都必须能指出**证据来源**（NetLog 第几跳 / JS 第几行 / 哪次 API 返回）
2. 与参考项目结论不一致时，**以本次实测为准**，并在文档中标注差异
3. 拿不到证据的部分，**明确标注"未验证"**，而不是含糊带过

---

## 1. 三种取证手段

三种手段**互补且互相印证**，任何单一手段都不足以支撑结论。

### 1.1 真实流量捕获（Chrome NetLog）

**目标**：拿到完整请求头、响应头、Cookie、跳转链。

```powershell
chrome.exe `
  --user-data-dir="<干净的独立 profile>" `
  --no-first-run `
  --no-default-browser-check `
  --log-net-log="capture.json" `
  --net-log-capture-mode=IncludeCookiesAndCredentials `
  "https://<目标页面>"
```

**关键点 —— NetLog 有三种捕获模式，选错就白干：**

| 模式 | 拿到什么 | 适用性 |
|---|---|---|
| `Default` | 事件元数据 | ❌ 信息太少 |
| `IncludeCookiesAndCredentials` | **完整请求头 + Cookie + 响应头** | ✅ **推荐** |
| `IncludeSocketBytes` | **TLS 密文**（只有 SNI 可读） | ❌ 对 HTTPS **完全无用** |

> **坑点**：`IncludeSocketBytes` 名字听起来"更全"，
> 但它给的是**加密后的字节**。对于 HTTPS 流量，请求体在里面是密文，
> **拿不到任何有用信息**。这一点浪费了大量时间。

**NetLog 的固有局限：拿不到 HTTPS 请求体（POST body）。**

原因：NetLog 在网络栈的字节层记录，HTTPS 的 body 在离开进程前就已加密。
**任何"netlog 解密"的尝试都是徒劳的** —— 必须换手段。

### 1.2 前端 JS 逆向

**目标**：拿到加密算法、密钥、上报节奏、判定阈值 —— 这些**只在客户端代码里**。

播放器主包规模：

```
stuStudy.*.js   8.55 MB   （学习页主逻辑）
app.*.js        0.48 MB   （公共库）
```

经 [obfuscator.io](https://obfuscator.io/) 处理，特征：

- 字符串统一提到一个**字符串数组**，代码里只留索引
- **自防御：数组旋转**（self-defending）
- 十六进制标识符

**反混淆的关键难点是数组旋转。** 详见第 3 节。

### 1.3 API 独立复现

**目标**：验证"从 JS 读出来的结论"在真实服务端是否成立。

技术选型：**`curl_cffi` + `impersonate="chrome"`**

```python
from curl_cffi import requests as cr
s = cr.Session(impersonate="chrome")
```

**为什么不用 `requests` 或 Node `fetch`：**

| 客户端 | TLS 指纹 | HTTP 版本 | 结果 |
|---|---|---|---|
| Python `requests` | OpenSSL 默认 | HTTP/1.1 | 容易被风控 |
| Node `fetch` | 默认 | HTTP/1.1 | 容易被风控 |
| **`curl_cffi` (impersonate)** | **Chrome 真实 JA3** | **HTTP/2** | ✅ 与真实浏览器一致 |

> 这一选择是**实测驱动**的：在排查验证码触发原因时，
> 排除了"指纹不对"这一假设 —— 因为**换成 Chrome 指纹后行为一致**，
> 说明触发原因在别处（最终定位为服务端累加模型的语义问题）。

---

## 2. 为什么不用调试器（重要方法学决策）

### 2.1 现象

早期尝试用 **Chrome DevTools Protocol** 抓包：

```javascript
await client.send('Network.enable');
await client.send('Target.setAutoAttach', { autoAttach: true, ... });
```

**结果：页面直接白屏（`documentElement === null`），
`document.readyState` 永远停在 `loading`。**

### 2.2 对照实验

| 条件 | 页面状态 |
|---|---|
| 附着 CDP + `Network.enable` | ❌ 白屏，文档被销毁 |
| **不附着 CDP**（仅 NetLog） | ✅ 正常加载 |

**结论：平台检测到调试器附着后会主动破坏当前文档。**

### 2.3 反调试机制定位

反混淆后找到的浏览器层反调试逻辑：

```javascript
// 1) 定时校验原生 API 是否被 hook
setInterval(checkout, 60000);   // 每 60 秒
// 校验 XMLHttpRequest 等是否为原生实现

// 2) 事件可信度校验
// PointerEvent / MouseEvent 上的 isTrusted 检查

// 3) 命中后 notTrustScript 流程
//    → 上报 collector2c.zhihuishu.com/public/jsonp/collect
//    → 暂停播放器
//    → 页面跳转
```

### 2.4 方法学结论

> **该平台的反调试完全实现在浏览器层。**

推论（本次研究的基石）：

> **任何不经过浏览器的 API 客户端，天然绕过全部反调试机制。**

这不是"绕过技术"，而是**架构层面的必然结果** ——
反调试代码运行在浏览器里，而 API 客户端根本不运行浏览器。

---

## 3. 反混淆：数组旋转的破解

### 3.1 问题

obfuscator.io 的自防御会把字符串数组**整体旋转** N 位：

```javascript
// 原始
var arr = ["queryCourse", "videolist", "saveDatabase..."];

// 旋转 2 位后
var arr = ["saveDatabase...", "queryCourse", "videolist"];
// 但代码里的索引仍然是原始的！→ 全部错位
```

**症状隐蔽**：不报错，只是所有字符串都变成不相干的内容，
极易被误判为"混淆太深解不开"。

### 3.2 破解方法：锚点反推

**思路**：找到若干"代码索引 → 期望字符串"的**已知对应关系**，反推旋转量。

```javascript
// 例如已知：
arr[0x1e3] 应该是 "queryCourse"
arr[0x1ce] 应该是 "videolist"
```

对每个候选旋转量 R，检查是否满足所有锚点：

```javascript
// tools/anchor.mjs 的核心逻辑
for (let R = 0; R < arr.length; R++) {
    const ok = anchors.every(({ idx, expect }) =>
        arr[(idx + R) % arr.length] === expect
    );
    if (ok) console.log('ROT =', R);
}
```

### 3.3 实测结果

| 文件 | 旋转量 | 锚点一致度 |
|---|---|---|
| `stuStudy.*.js` | **286** | 6 / 6 ✅ |
| `app.*.js` | **140** | 全部一致 ✅ |

**6 个独立锚点全部指向同一个旋转量** —— 这是强证据，不是巧合。

### 3.4 工具

```bash
# 求旋转量
node tools/anchor.mjs

# 用指定旋转量反混淆
ROT=286 node tools/deobf.mjs stuStudy.obf.js stuStudy.deobf.js
```

---

## 4. 验证闭环

每一条结论都经过如下闭环：

```
① JS 里读到算法/常量
        ↓
② 用真实捕获的密文解密 → 能否还原出可读明文？
        ↓
③ 用还原的算法加密 → 服务端是否接受（code=0）？
        ↓
④ 两端都通过 → 结论成立
```

**实例：登录请求体的验证**

```
① 从 login.zhihuishu.com 的 JS 里找到加密函数 $a() 与密钥
        ↓
② 用该密钥解密 NetLog/CDP 捕获的真实登录请求体
   → ✅ 成功还原出 JSON：{account, password, validate}
        ↓
③ 用该算法构造新请求 → 服务端返回业务响应（而非解密错误）
        ↓
④ 结论成立：算法与密钥均正确
```

---

## 5. 走过的弯路（供后来者避坑）

| # | 弯路 | 教训 |
|---|---|---|
| 1 | 试图直接读 Edge 的 cookie 数据库 | 浏览器运行时数据库被**独占锁**；非管理员也无法用 `robocopy /B` 绕过（需 Backup 权限） |
| 2 | 用 `IncludeSocketBytes` 抓包 | 拿到的是 **TLS 密文**，对 HTTPS 毫无用处 |
| 3 | 用 CDP 抓包 | **破坏页面**，触发浏览器层反调试 |
| 4 | 反混淆时不处理数组旋转 | 字符串全错，且**症状隐蔽**，极易误判 |
| 5 | 假设"错误码一定在文档里" | 实测发现**未公开的错误码**（如 `-8`） |
| 6 | 相信参考项目的结论 | 多处已被实测证伪（域名、阈值、周期都有错） |

---

## 6. 工具链总览

| 阶段 | 工具 | 产出 |
|---|---|---|
| 流量捕获 | Chrome `--log-net-log` | 请求头、Cookie、跳转链 |
| 静态分析 | `tools/anchor.mjs` + `tools/deobf.mjs` | 可读源码 |
| 密钥提取 | 正则 + 人工审阅 | 加密算法与密钥 |
| 行为验证 | `curl_cffi` 客户端 | 服务端返回码 |
| 状态观测 | 服务端查询接口 | 真实验证结论 |

---

## 7. 方法学小结

1. **证据优先于推测** —— 拿不到证据就标"未验证"
2. **手段要互补** —— NetLog 给不了请求体，就必须靠 JS 逆向补
3. **反调试要正面理解** —— 搞清它实现在哪一层，就知道该从哪一层绕开
4. **交叉验证** —— 客户端推断 + 服务端返回，两者一致才下结论
5. **警惕参考项目** —— 它们可能过期，也可能一开始就错
