# 05 · 进度上报机制

> 本文是本次研究的**核心**：学习时长如何被计算、包装与上报，
> 以及服务端如何处理这些数据。

---

## 1. 客户端的四个定时器

反混淆后从 `startTotalTimer` 提取（数值为十六进制原文）：

| 定时器 | 周期（原文） | 周期（十进制） | 职责 |
|---|---|---|---|
| `learningTimeRecord` | `0x7c6` | **1990 ms** | 累加 `watchPoint` |
| `totalStudyTimeFun` | `0x137e` | **4990 ms** | 学习时长心跳 |
| `cacheInterval` | `0x2bf20` | **180000 ms**（3 分钟） | 缓存上报 |
| `databaseInterval` | `0x493e0` | **300000 ms**（5 分钟） | **主上报** |

> **注意主上报周期是 300 秒，不是 30 秒。**
> 部分公开参考项目写的是 30 秒 —— 实测证伪。

---

## 2. 心跳：学习时长如何增长

每 **4990 ms** 执行一次：

```javascript
totalStudyTime += 5 * playRate;
totalTimeFinish += 5 * playRate;
playTimes      += 5 * playRate;
computeProgree();
```

**关键点：**

| 项 | 说明 |
|---|---|
| 每次固定加 `5 * playRate` | 与真实经过的 4.99 秒对应（取整为 5） |
| `playRate` | **前端硬钳在 1.8**（最大倍速） |
| `playTimes` | 本次**未上报的增量** |
| `totalStudyTime` | 该视频的**累计**学习时长 |

### 2.1 倍速上限

```javascript
// 前端把倍速钳死在 1.8
playRate = Math.min(playRate, 1.8);
```

**这是前端的限制，不是服务端的限制** —— 服务端只接收结果，不校验倍速。

---

## 3. `watchPoint`（`ewssw` 字段）

每 **1990 ms** 执行一次：

```javascript
const val = parseInt(totalStudyTime / 5) + 2;
watchPointPost = (watchPointPost === '' ? "0,1," : watchPointPost + ',') + val;
```

### 3.1 特征

| 项 | 值 |
|---|---|
| 初始值 | `"0,1"` |
| 每样本追加 | `floor(totalStudyTime / 5) + 2` |
| 采样周期 | 1990 ms |
| 最终形态 | 逗号分隔的十进制数字串 |

### 3.2 语义

`watchPoint` 是**播放轨迹的采样点** ——
服务端可用它验证"学习时长的增长是否与真实的播放过程相符"。

**因为 `totalStudyTime` 在增长，采样值通常呈阶梯上升。**

### 3.3 参考实现

```python
class WatchPoint:
    def __init__(self):
        self.wp = ["0", "1"]

    def add(self, total_study_time):
        self.wp.append(str(int(max(0, total_study_time) // 5) + 2))

    def get(self):
        return ",".join(self.wp)
```

### 3.4 特殊情形：重看已完成的视频

重看时 `totalStudyTime` 已被钳在 `videoSec` 不再增长，
**采样值会是一个恒定值**重复出现。这是**真实客户端也会产生的形态**，
不是异常。

---

## 4. `sdsew`：10 元素数组

`sdsew` 是 ev 编码后的数组（编码算法见 [03-crypto.md](03-crypto.md#4-ev-编码器-d26666z)）。

**元素的顺序就是语义**（源码中的字面量顺序）：

| 索引 | 表达式 | 含义 |
|---|---|---|
| 0 | `this.recruitId` | 招生批次 ID |
| 1 | `this.lessonId` | 课节 ID |
| 2 | `this.smallLessonId` | 小节 ID |
| 3 | `this.lastViewVideoId` | 当前视频 ID |
| 4 | `this.videoDetail.chapterId` | 章节 ID |
| 5 | `this.data.studyStatus` | 学习状态（字符串） |
| 6 | `parseInt(this.playTimes)` | **本次增量** |
| 7 | `parseInt(this.totalStudyTime)` | **累计时长** |
| 8 | `round(player.getPosition())` | 播放器实时位置 |
| 9 | `this.getMainCode()` | **`uuid + "zhs"`** |

> **第 9 元素容易漏**：它是 `uuid + "zhs"` 的字符串拼接，
> 不是单独的 `uuid`。漏掉 `"zhs"` 后缀会导致上报失败。

---

## 5. 上报条件

源码 `saveDdsjkTimeInWhileFn`：

```javascript
if (isSaving) return;                      // 防重入

if (playTimes <= 5 && !force) {
    totalStudyTime -= playTimes;           // ★ 回退，不报
    playTimes = 0;
} else {
    // 构造并提交上报
}
```

### 5.1 语义解读

| 条件 | 行为 |
|---|---|
| `playTimes <= 5` 且非强制 | **回退** `totalStudyTime`，**不上报** |
| 否则 | 正常上报 |

**含义**：增量太小时不值得发请求，直接把累加值退回去，等下一轮再攒。

### 5.2 `force` 的触发时机

**切换视频时**以 `type=4, force=true` **强制冲刷**，确保进度不丢。

---

## 6. ★ 服务端的累加模型（本次核心发现）

### 6.1 实测过程

同一视频连续上报三次，观察服务端返回的累计值：

| 次序 | 客户端发送 `totalStudyTime` | 客户端发送 `playTimes` | 服务端**实际存储**的 `studyTotalTime` |
|---|---|---|---|
| 基线 | — | — | `352` |
| ① | `415` | `540` | **`892`** = 352 + 540 |
| ② | `415` | `540` | ❌ `code: -8` **学习总时长下降了** |

### 6.2 结论

```
服务端算法：新 studyTotalTime = 旧 studyTotalTime + playTimes
```

**三个推论（均为实测）：**

| # | 结论 |
|---|---|
| 1 | **服务端基本忽略客户端上传的 `totalStudyTime`**，只取 `playTimes` 作为增量 |
| 2 | **`totalStudyTime` 必须单调不减**，否则返回 **`-8`** |
| 3 | **服务端不限制 `studyTotalTime` 超过 `videoSec`**（892 > 415 被正常接受） |

### 6.3 正确构造方式

```python
# ❌ 错误：固定发送 videoSec
report(total_study=video_sec, play_times=chunk)
# → 第二次就会因"时长下降"被拒

# ✅ 正确：先读当前值，再递增
current = query_current_study_total_time()
report(total_study=current + chunk, play_times=chunk)
```

### 6.4 `-8` 是一个未公开的错误码

**公开资料中没有 `-8` 的记录。** 本次通过实测发现并定位了原因。

### 6.5 修正了一个早期误判

研究早期曾观察到"上报值超过 `videoSec` 后触发易盾 `-12`"，
并据此推断存在"时长上限"。

**后续实测推翻了这一推断**：服务端接受任意大的 `studyTotalTime`。
`-12` 的真实触发条件**尚未完全定位**（见 [06](06-risk-control.md#5-未定位的部分)）。

> 这是一个**方法学教训**：单点观察容易得出错误因果，必须做对照实验。

---

## 7. 完成阈值

```javascript
function computeProgree() {
    percentage = totalStudyTime / videoSec * 100;
    if (percentage >= 0x51) {          // 0x51 = 81
        isStudiedLesson = 1;
    }
}
```

**完成阈值是 81%（`0x51`），不是常见的 91%。**

> 部分公开参考项目使用 91%，实测证伪。

**权威判定仍应以服务端的 `watchState == 1` 为准。**

---

## 8. 上报的完整时序

```
视频开始
   │
   ├─ 每 1990 ms  → watchPoint 追加一个采样值
   ├─ 每 4990 ms  → totalStudyTime += 5 × 1.8
   │
   ├─ 每 180000 ms (3min) → saveCacheIntervalTimeV2   缓存上报
   ├─ 每 300000 ms (5min) → saveDatabaseIntervalTimeV2 主上报
   │
   ├─ 遇到弹题时间点 → 暂停 → 作答 → 继续
   │
   └─ 视频结束 / 切换
          → force=true 强制冲刷一次
```

---

## 9. 相关文档

- 加密与 ev 编码器 → [03-crypto.md](03-crypto.md)
- 接口参数细节 → [04-api-reference.md](04-api-reference.md)
- 上报被拒的各种原因 → [06-risk-control.md](06-risk-control.md)
