# 04 · 接口清单

> 智慧树业务接口的完整梳理。
> **所有参数中的 `<...>` 为脱敏占位符。**

---

## 0. 通用约定

### 0.1 请求格式

```http
POST https://<host>/gateway/t/v1<path>
Content-Type: application/x-www-form-urlencoded
Origin:   https://studyvideoh5.zhihuishu.com
Referer:  https://studyvideoh5.zhihuishu.com/

secretStr=<Base64(AES-CBC(json))>&dateFormate=<int(time.time())*1000>
```

### 0.2 响应格式

```json
{ "code": 0, "message": "请求成功", "data": { } }
```

> **注意**：业务成功判定用 `code`，**不是 HTTP 状态码**。
> HTTP 200 完全可能是 `code: -12`。

### 0.3 主要主机

| 主机 | 用途 | 密钥 |
|---|---|---|
| `studyservice-api.zhihuishu.com` | 学习相关（本文主体） | `VIDEO_KEY` |
| `onlineservice-api.zhihuishu.com` | 课程列表 / 首页 | `HOME_KEY` |
| `cloudapi.polymas.com` | SSO / 身份 | — |
| `passport.zhihuishu.com` | CAS 票据 | — |
| `user.zhihuishu.com` | 账号服务 | 登录页密钥 |
| `newbase.zhihuishu.com` | 视频播放地址 | — |

---

## 1. 学习主流程

| 用途 | 方法 | 路径 | 请求体 |
|---|---|---|---|
| 课程信息 | POST | `/learning/queryCourse` | `{recruitAndCourseId}` |
| 章节视频树 | POST | `/learning/videolist` | `{recruitAndCourseId}` |
| 学习状态 | POST | `/learning/queryStuyInfo` | `{lessonIds, lessonVideoIds, recruitId}` |
| 上报令牌 | POST | `/learning/prelearningNote` | `{ccCourseId, chapterId, isApply:1, lessonId, lessonVideoId, recruitId, videoId}` |
| **主进度上报** | POST | `/learning/saveDatabaseIntervalTimeV2` | `{ewssw, sdsew, zwsds, courseId}` |
| 缓存上报 | POST | `/learning/saveCacheIntervalTimeV2` | `{ewssw, sdsew, zwsds}` |
| 上次观看 | POST | `/learning/queryUserRecruitIdLastVideoId` | `{recruitId}` |
| 服务器时间 | GET | `/learning/currentSystemTime` | — |
| 展示模式 | GET | `/learning/queryCourseDispMode` | `{recruitAndCourseId}` |

> ⚠️ 注意区分 **`queryStuyInfo`**（学习状态，拼写如此）与
> **`queryStudyInfo`**（实测 404，不存在）。

### 1.1 课程信息 `queryCourse`

**响应关键字段：**

```json
{
  "code": 0,
  "data": {
    "recruitId":        <数字>,
    "studyStatus":      "<字符串>",
    "hasHabbitScore":   true,
    "isShareCourse":    1,
    "courseInfo": {
      "courseId":       <数字>,
      "name":           "<课程名>",
      "schoolName":     "<学校名>"
    },
    "videoData": {
      "canJumpChapter": true
    }
  }
}
```

`recruitAndCourseId` 的获取方式见 [§5](#5-recruitandcourseid-的来龙去脉)。

### 1.2 学习状态 `queryStuyInfo`

**响应结构**（两层，`lv` 优先）：

```json
{
  "code": 0,
  "data": {
    "lv":     { "<smallLessonId>": { "watchState": 1, "studyTotalTime": 352 } },
    "lesson": { "<lessonId>":      { "watchState": 1, "studyTotalTime": 352 } }
  }
}
```

**`watchState` 语义**（实测确认）：

| 值 | 含义 |
|---|---|
| `0` | 未观看 |
| `1` | **已完成**（服务端认定） |
| `2` | 部分观看 |

> **`watchState == 1` 才是"完成"的唯一权威标志**，
> 不要用百分比自行判断。

### 1.3 上报令牌 `prelearningNote`

**这一步不能省。** 进度上报需要 `zwsds`（Base64 的令牌），
而令牌由本接口下发：

```json
// 响应
{ "code": 0, "data": { "studiedLessonDto": { "id": <令牌值> } } }
```

### 1.4 主进度上报 `saveDatabaseIntervalTimeV2`

**请求体四个字段：**

| 字段 | 内容 |
|---|---|
| `ewssw` | `watchPoint`，逗号分隔的数字串 |
| `sdsew` | ev 编码后的 10 元素数组（见 [05](05-progress-reporting.md)） |
| `zwsds` | Base64(令牌) |
| `courseId` | 课程 ID |

> **与旧版的差异**：`saveCacheIntervalTimeV2`（不带 V2 的旧版缓存上报）
> **不含 `courseId`**，且部分公开参考项目**完全没有 V2 版本**。

---

## 2. 弹题（课中测验）

| 用途 | 方法 | 路径 | 请求体 |
|---|---|---|---|
| 弹题列表 | POST | `/popupAnswer/loadVideoPointerInfo` | `{lessonId, lessonVideoId, recruitId, courseId}` |
| 弹题内容 | POST | `/popupAnswer/lessonPopupExam` | `{lessonId, lessonVideoId, questionIds}` |
| 弹题作答 | POST | `/popupAnswer/saveLessonPopupExamSaveAnswer` | 见下 |

### 2.1 作答请求体

```json
{
  "courseId":      <数字>,
  "recruitId":     <数字>,
  "testQuestionId": <题目 ID>,
  "isCurrent":     "1",
  "lessonId":      <数字>,
  "lessonVideoId": <数字>,
  "answer":        "<答案>",
  "testType":      0
}
```

### 2.2 ⚠️ 重要陷阱：`questionIds` 必须是字符串

`loadVideoPointerInfo` 返回的 `questionPoint[].questionIds`
**看起来像数组，实际是逗号分隔的字符串**。

```python
# ❌ 错误：传数组
{"questionIds": [295788232, 295788233]}      # → 返回 code: -1

# ✅ 正确：传字符串
{"questionIds": "295788232,295788233"}       # → code: 0
```

**错误提示极不友好**（只有一个 `-1`），排查成本很高。

### 2.3 弹题是单题作答

`saveLessonPopupExamSaveAnswer` 的 `testQuestionId` 是**单个题目 ID**，
不是题目数组 —— **每个小题需要单独上报一次**。

---

## 3. 视频播放地址

播放地址不是通过加密接口拿的，而是一个 **JSONP**：

```http
GET https://newbase.zhihuishu.com/video/initVideo
      ?jsonpCallBack=result
      &videoID=<videoId>
      &_=<毫秒时间戳>
```

**响应形如：**

```javascript
result({"lines":[{"lineUrl":"https://<cdn>/....mp4?...", ...}], ...})
```

> 需要剥离 JSONP 包裹（`result(` ... `)`）后再解析。

---

## 4. 风控 / 状态查询

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 异常行为记录 | GET | `/cheat/exceptionActionDetail` | `{schoolId, recruitId}` |
| 确认异常 | POST | `/cheat/agreeExceptionActionDetail` | — |
| 学习时长限制 | GET | `/course/queryStudiedTimeLimitInfo` | `{recruitId, schoolId}` |
| 滑块校验 | POST | `/learning/validateSlideToken` | `{token, ev, checkType:2}` |
| 成绩规则 | POST | `/learning/queryStudyReadBefore` | `{courseId, recruitId}` |
| 三维课件 | GET | `/course/threeDimensionalCourseWare` | `{videoId}` |

### 4.1 `/cheat/exceptionActionDetail` 响应

```json
{ "count": 0, "lock": false, "punishType": 0, "popWindow": false }
```

| 字段 | 含义 |
|---|---|
| `count` | 异常行为累计次数 |
| `lock` | 是否被锁定 |
| `punishType` | 处罚类型（0 = 无） |
| `popWindow` | 是否弹窗警告 |

> **这是判断账号是否"干净"的权威接口。**

### 4.2 `/learning/queryStudyReadBefore` —— 成绩规则

**这是本次新发现的接口**，返回课程时间线与成绩构成：

```json
{
  "code": 0,
  "data": {
    "courseStart": "2026-08-15",
    "courseEnd":   "2026-12-15",
    "examStart":   "2026-12-16 00:00",
    "examEnd":     "2026-12-25 23:59",
    "makeUpInfo":  "允许补考，补考周期...",
    "scoreAssessRule": {
      "totalScore": "100",
      "usualScoreShare":             "50",
      "progressScoreShare":          "20",
      "learnedBehaviorScoreShare":   "20",
      "learningHabitScore":          "20",
      "learningInteractScore":       "10",
      "finalExamScoreShare":         "40"
    }
  }
}
```

> **`learningHabitScore`（学习习惯分）是一个显式配置项**，
> 说明该维度确实是成绩的组成部分，且权重可观。
>
> 注意：**不同课程、不同学校的配置不同**，不能一概而论。

---

## 5. `recruitAndCourseId` 的来龙去脉

### 5.1 它是什么

智慧树的课程标识有**三个层级**：

| 标识 | 含义 | 稳定性 |
|---|---|---|
| `courseId` | 课程本身（如"马克思主义基本原理"） | 长期稳定 |
| `recruitId` | **招生批次**（某学期某次开班） | 每学期变 |
| `recruitAndCourseId` | 上述二者的**合并外部标识** | 每次开班变 |

**为什么需要 `recruitAndCourseId`：**
同一门课会反复开班，`courseId` 只标识"课程"，
只有 `recruitAndCourseId` 才能标识"**你实际在学的这一次**"。

### 5.2 形态特征

共享课的 `recruitAndCourseId` 是一串 **34 位十六进制字符**（17 字节）：

```
5b4a5d4c4f5e48595a4b5c4d5e4f485950     ← 合成示例，非任何真实课程
```

> 上面这串是**人工构造的**，仅用于说明格式，
> 不对应任何真实课程。请勿尝试使用。

**观察到的结构特征：**

- 长度固定 34 个 hex 字符
- **每个字节的高半字节恒为 `4` 或 `5`**
- 不是哈希（哈希不会如此规整）

> **未破解**：本文**没有**逆出它的生成算法。
> 试验了 256 种单字节 XOR、256 种常量减法，均无法还原为纯数字。
>
> **但这不影响使用** —— 它是一个**不透明句柄**，原样传递即可。

### 5.3 如何获取

**方法 1：从课程页 URL 读取**

```
https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=<34位hex>
```

**方法 2：从接口获取**

```http
POST onlineservice-api.zhihuishu.com/gateway/t/v1/student/course/share/queryShareCourseInfo
Body: {status: 0, pageNo: 1, pageSize: 5}      ← 使用 HOME_KEY
```

响应中的 `courseOpenDtos[].secret` 即 `recruitAndCourseId`。

### 5.4 如何区分共享课与校内课

```python
import re

if re.search(r"[a-zA-Z]", course_id):
    # 含字母 → 共享课 → 用 recruitAndCourseId
    pass
else:
    # 纯数字 → 校内课 → 用 courseId（走另一套签名机制）
    pass
```

与 `queryCourse` 返回的 `isShareCourse: 1` 互相印证。

> **本文仅覆盖共享课。** 校内课使用不同的签名方案（MD5 系），未在本次研究范围内。

---

## 6. 接口调用顺序（完整学习流程）

```
① queryCourse           拿 recruitId / courseId / canJumpChapter
② videolist             拿章节树与视频列表
③ queryStuyInfo         拿每个视频的 watchState / studyTotalTime
        ↓ 对每个待学视频
④ prelearningNote       拿上报令牌（token）
⑤ [循环] 播放 + 心跳
      每 300 秒 → saveDatabaseIntervalTimeV2   （主上报）
      每 180 秒 → saveCacheIntervalTimeV2      （缓存上报）
        ↓ 遇到弹题时间点
⑥ loadVideoPointerInfo  拿题目列表
⑦ lessonPopupExam       拿题目内容与选项
⑧ saveLessonPopupExamSaveAnswer   逐题作答
        ↓
⑨ queryStuyInfo         复查 watchState 是否变为 1
```

---

## 7. 相关文档

- 上报字段的精确语义 → [05-progress-reporting.md](05-progress-reporting.md)
- 请求如何加密 → [03-crypto.md](03-crypto.md)
- 接口调用的风控约束 → [06-risk-control.md](06-risk-control.md)
