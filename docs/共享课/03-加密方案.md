# 03 · 传输层加密

> 智慧树的"加密"是**客户端混淆式的对称加密**，非真正意义上的安全机制。
> 本文完整还原其方案与全部密钥。
>
> **这些密钥是公开前端 JS 中的硬编码常量，任何访问者都能获取。**

---

## 1. 业务接口加密

### 1.1 算法

| 项 | 值 |
|---|---|
| 算法 | **AES-128-CBC** |
| 填充 | **PKCS#7** |
| 编码 | **Base64** |
| IV | `1g3qqdh4jvbskb9x`（**全局固定**） |

> ⚠️ **IV 固定**意味着：**相同明文永远产生相同密文**。
> 这是一个明显的设计缺陷 —— 攻击者可通过密文比对推断请求内容，
> 也失去了 CBC 模式应有的语义安全性。

### 1.2 密钥总表

不同子系统使用不同密钥：

| 常量名 | 密钥 | 适用目标 |
|---|---|---|
| `VIDEO_KEY` | `azp53h0kft7qi78q` | `studyservice-api`（学习相关，最常用） |
| `HOME_KEY` | `7q9oko0vqb3la20r` | `onlineservice-api` |
| `AI_KEY` | `hw2fdlwcj4cs1mx7` | `kg-ai-run` |
| `EXAM_KEY` | `onbfhdyvz8x7otrp` | 考试相关 |
| `QA_KEY` | `kcGOlISPkYKRksSK` | 问答相关 |

**全部 5 个密钥均通过"解密真实密文 → 还原出可读 JSON"实测验证。**

### 1.3 参考实现

```python
import base64, json
from Crypto.Cipher import AES

IV = b"1g3qqdh4jvbskb9x"
VIDEO_KEY = b"azp53h0kft7qi78q"

def pkcs7_pad(raw: bytes) -> bytes:
    n = 16 - len(raw) % 16
    return raw + bytes([n]) * n

def encrypt(payload: dict, key: bytes = VIDEO_KEY) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ct = AES.new(key, AES.MODE_CBC, IV).encrypt(pkcs7_pad(raw))
    return base64.b64encode(ct).decode()

def decrypt(b64: str, key: bytes = VIDEO_KEY) -> dict:
    raw = AES.new(key, AES.MODE_CBC, IV).decrypt(base64.b64decode(b64))
    return json.loads(raw[:-raw[-1]].decode("utf-8"))   # 去掉 PKCS#7 填充
```

---

## 2. 请求封装格式

### 2.1 结构

```http
POST https://studyservice-api.zhihuishu.com/gateway/t/v1<path>
Content-Type: application/x-www-form-urlencoded
Origin: https://studyvideoh5.zhihuishu.com
Referer: https://studyvideoh5.zhihuishu.com/

secretStr=<Base64(AES(json))>&dateFormate=<毫秒时间戳>
```

### 2.2 `dateFormate` 出现两次

**这是一个容易漏掉的细节：**

- **加密体内**一份：`{"...": "...", "dateFormate": 1790667637000}`
- **表单外层**明文一份：`&dateFormate=1790667637000`

两者必须一致。

### 2.3 `dateFormate` 的生成

```javascript
// 前端实现
Math.floor(Date.now() / 1000) * 1000
```

**特征：永远是 1000 的整数倍，即以 `000` 结尾。**

```python
import time
date_formate = int(time.time()) * 1000    # 注意：先取整秒，再乘 1000
```

> 如果直接用 `int(time.time() * 1000)`，末三位是随机的 —— **与真实客户端不符**，
> 属于可被识别的特征差异。

### 2.4 成功判定

```javascript
// 业务成功看 code 字段，不是 HTTP 状态码
if (response.code === 0) { /* 成功 */ }
```

**HTTP 200 + `code != 0` 表示业务失败**，这是排查时最容易混淆的地方。

---

## 3. 登录页加密

登录中心的加密**与业务接口不同**（算法相同，密钥与 IV 不同）。

### 3.1 前端实现（反混淆后原文）

```javascript
async function $a(e) {
  const A = new TextEncoder,
        t = A.encode("bdas85990328dfpl"),     // ← 密钥
        n = A.encode("563216db1e1744c7"),     // ← IV
        r = await crypto.subtle.importKey("raw", t, { name: "AES-CBC" }, false, ["encrypt"]),
        o = await crypto.subtle.encrypt({ name: "AES-CBC", iv: n }, r,
                                          A.encode(JSON.stringify(e)));
  return xR(o);
}

function xR(e) {
  const A = new Uint8Array(e);
  let t = "";
  for (let n of A) t += String.fromCharCode(n);
  return btoa(t);                              // → Base64
}
```

### 3.2 参数

| 项 | 值 |
|---|---|
| 算法 | AES-128-CBC（WebCrypto 默认 **PKCS#7**） |
| **Key** | `bdas85990328dfpl` |
| **IV** | `563216db1e1744c7` |
| 输出 | Base64 字符串（**不是 JSON**） |

> **关于公开这两个常量**
>
> 它们是 `login.zhihuishu.com` 前端 JS 中**硬编码的公开常量**，
> 任何访问者打开开发者工具都能读到 —— 它们不是秘密。
>
> 更重要的是：**知道密钥并不能登录。**
> 实测证明，登录接口必须携带**有效的易盾滑块 token**
> （见 [02](02-login-and-session.md#221-账号密码登录含验证码门槛)），
> 而该 token 依赖浏览器环境与混淆的 SDK 生成。
>
> 因此，公开该密钥**不降低任何实际安全门槛**，
> 却能让读者验证本文的结论 —— 这正是研究文档应有的透明度。
> 本文**不提供**任何绕过易盾的方法。

### 3.3 哪些接口会被加密

前端用**白名单**决定是否加密：

```javascript
const UR = [
  "/user/register", "/login/account", "login/code", "login/teacherCode",
  "/reset/resetPassword", "/code/certifyByMobile", "/login/bySmsCode",
  "/sms/initLogin", "/login/resetPassword", "/accountV2/changePassword",
  "/zhsuser/login/changePwd", "/accountV2/removeUser"
];

Xg.interceptors.request.use(async e => (
  needsEncrypt(e.url) && (e.data = await $a(e.data)), e), ...);
```

### 3.4 验证结果

用该密钥解密**真实捕获的登录请求体**，成功还原：

```json
{
  "account":  "<手机号>",
  "password": "<密码>",
  "validate": "CN31_<易盾滑块 token>"
}
```

> **观察**：`password` 在加密体内是**明文**的。
> AES-CBC 在这里只是"把 JSON 变成看起来像密文的字符串"，
> 真正的传输保密性由 HTTPS 提供。
>
> **这是理解本方案性质的关键**：它是**混淆**，不是**安全机制**。

---

## 4. ev 编码器（`D26666.Z`）

业务上报中 `sdsew` 字段使用一种自定义编码。

### 4.1 前端实现

```javascript
KEY = "zzpttjd";

Z(arr) = X(arr.join(';'));

function X(str) {
  let out = "";
  for (let i = 0; i < str.length; i++) {
    let n = str.charCodeAt(i) ^ KEY.charCodeAt(i % KEY.length);
    let h = n.toString(16);
    if (h.length < 2) h = "0" + h;
    out += h.slice(-4);          // 取最后 4 个字符
  }
  return out;
}
```

### 4.2 步骤拆解

```
输入: 数组 [a, b, c, ...]
  ↓ join(';')
字符串 "a;b;c;..."
  ↓ 逐字符与 "zzpttjd" 循环异或
字节序列
  ↓ toString(16)，不足 2 位补 '0'
十六进制串
  ↓ slice(-4)  ← 注意：这一步在多数情况下等价于取原值
  ↓ 拼接
输出: 十六进制字符串
```

> `slice(-4)` 看起来多余（普通字符异或后 hex 最多 2 位），
> 但必须保留 —— **它是前端原文的一部分，去掉会改变输出**。

### 4.3 参考实现

```python
EV_KEY = "zzpttjd"

def ev_encode(arr) -> str:
    s = ";".join(str(x) for x in arr)
    out = []
    for i, ch in enumerate(s):
        n = ord(ch) ^ ord(EV_KEY[i % len(EV_KEY)])
        h = format(n, "x")
        if len(h) < 2:
            h = "0" + h
        out.append(h[-4:])
    return "".join(out)
```

---

## 5. 密钥是如何被找到的

**不是靠猜，是靠反混淆后的静态阅读。**

```bash
# 1) 从页面加载的 JS 中定位加密相关文件
# 2) 反混淆（需先求数组旋转量，见 01-methodology.md）
ROT=<n> node tools/deobf.mjs app.obf.js app.deobf.js

# 3) 搜索特征字符串
#    - "AES" / "CBC" / "Utf8.parse" / "CryptoJS"
#    - "secretStr"（业务加密的字段名）
#    - "importKey"（WebCrypto 登录加密）
```

**密钥通常以"看起来像随机字符串的 16 字符字面量"出现**，
紧跟 `AES-CBC` 或 `importKey` 调用。

---

## 6. 安全性评述

从工程安全角度，本方案的几个特征值得记录：

| 特征 | 评价 |
|---|---|
| IV 全局固定 | ❌ 丧失 CBC 的语义安全性，可做密文比对 |
| 密钥硬编码在前端 | ❌ 任何人可提取（本文即证明） |
| 多个子系统共用一套算法 | ⚠️ 单点失守影响面大 |
| 密码在加密体内明文 | ⚠️ 依赖 HTTPS 单层保护 |
| 仅用于登录接口 | ✅ 攻击面相对可控 |
| 真正的安全边界是 HTTPS + 风控 | ✅ 设计者的实际依赖 |

> **结论**：这是**防爬虫的混淆手段**，不是密码学意义上的保护。
> 它的价值在于提高逆向成本，而非提供机密性。

---

## 7. 相关文档

- 上报字段的完整语义 → [05-progress-reporting.md](05-progress-reporting.md)
- 加密为何挡不住 API 客户端 → [01-methodology.md](01-methodology.md)
