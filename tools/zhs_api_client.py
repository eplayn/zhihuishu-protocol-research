#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树共享课 · 只读 API 客户端（研究演示用）
================================================================

本工具用于【验证】本项目文档中还原的协议是否成立：
加密算法、请求封装、接口路径、字段结构。

!! 它【只读】—— 只调用查询类接口，不包含任何进度上报或学习行为模拟 !!
   这是刻意的设计：协议研究 ≠ 提供刷课工具。见 DISCLAIMER.md。

【前置条件】
需要一个有效的会话 Cookie（自行从浏览器开发者工具导出）。
Cookie 获取方式：打开智慧树学习页 → F12 → Network → 任选一个
studyservice-api 的请求 → 复制 Request Headers 里的 Cookie 值。

注意：会话 (jt-cas) 有效期仅 24 小时，过期需重新登录。

【用法】
    # 课程概览
    python zhs_api_client.py --cookie "<COOKIE>" --course <RECRUIT_AND_COURSE_ID> course

    # 章节与视频列表
    python zhs_api_client.py --cookie "..." --course <ID> chapters

    # 每个视频的学习状态
    python zhs_api_client.py --cookie "..." --course <ID> state

    # 账号健康检查（异常行为记录）
    python zhs_api_client.py --cookie "..." --school <ID> --recruit <ID> diag

    # 成绩规则与课程时间线
    python zhs_api_client.py --cookie "..." --course-id <ID> --recruit <ID> rules

    # 演示加密：把一段 JSON 加密成 secretStr
    python zhs_api_client.py --cookie x crypto '{"hello":"world"}'

【依赖】
    pip install curl_cffi pycryptodome
"""
import argparse
import base64
import json
import re
import sys
import time
from urllib.parse import unquote

try:
    from curl_cffi import requests as cr
except ImportError:
    sys.exit("缺少依赖：pip install curl_cffi")
try:
    from Crypto.Cipher import AES
except ImportError:
    sys.exit("缺少依赖：pip install pycryptodome")

# ============================================================
#  协议常量（来自公开前端 JS，见 docs/03-crypto.md）
# ============================================================
API_HOST = "studyservice-api.zhihuishu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

IV = b"1g3qqdh4jvbskb9x"        # 全局固定 IV
VIDEO_KEY = b"azp53h0kft7qi78q"  # studyservice-api 密钥
EV_KEY = "zzpttjd"               # ev 编码器异或密钥


# ============================================================
#  加密
# ============================================================
def _pkcs7(raw: bytes) -> bytes:
    n = 16 - len(raw) % 16
    return raw + bytes([n]) * n


def encrypt(payload: dict, key: bytes = VIDEO_KEY) -> str:
    """AES-128-CBC + PKCS#7 -> Base64"""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(AES.new(key, AES.MODE_CBC, IV).encrypt(_pkcs7(raw))).decode()


def decrypt(b64: str, key: bytes = VIDEO_KEY) -> dict:
    """Base64 -> AES-128-CBC 解密 -> 去 PKCS#7 -> JSON"""
    raw = AES.new(key, AES.MODE_CBC, IV).decrypt(base64.b64decode(b64))
    return json.loads(raw[:-raw[-1]].decode("utf-8"))


def ev_encode(arr) -> str:
    """ev 编码器（前端 D26666.Z）：
    join(';') -> 逐字符 XOR("zzpttjd") -> hex 补零到 2 位 -> slice(-4) -> 拼接"""
    s = ";".join(str(x) for x in arr)
    out = []
    for i, ch in enumerate(s):
        n = ord(ch) ^ ord(EV_KEY[i % len(EV_KEY)])
        h = format(n, "x")
        if len(h) < 2:
            h = "0" + h
        out.append(h[-4:])
    return "".join(out)


def date_formate() -> int:
    """前端实现：Math.floor(Date.now()/1000) * 1000
    特征：恒为 1000 的整数倍（末三位是 000）"""
    return int(time.time()) * 1000


# ============================================================
#  请求
# ============================================================
class Client:
    def __init__(self, cookie: str, verbose: bool = False):
        self.verbose = verbose
        self.s = cr.Session(impersonate="chrome", timeout=25)
        self.s.headers.update({
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            # ⚠️ 这两个头必须与真实浏览器一致（注意是 studyvideoh5，不是 studyh5）
            "Origin": "https://studyvideoh5.zhihuishu.com",
            "Referer": "https://studyvideoh5.zhihuishu.com/",
            "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Cookie": cookie,
        })

    def post(self, path: str, payload: dict) -> dict:
        t = date_formate()
        p = dict(payload)
        p["dateFormate"] = t
        body = {"secretStr": encrypt(p), "dateFormate": str(t)}
        url = "https://%s/gateway/t/v1%s" % (API_HOST, path)
        if self.verbose:
            print("  → POST %s" % url)
            print("     payload: %s" % json.dumps(payload, ensure_ascii=False))
        try:
            r = self.s.post(url, data=body)
        except Exception as e:
            return {"code": -999, "message": "网络异常: %s" % e}
        try:
            return json.loads(r.text)
        except Exception:
            return {"code": -998, "raw": r.text[:300]}

    def get(self, path: str, params: dict) -> dict:
        t = date_formate()
        p = dict(params)
        p["dateFormate"] = t
        p["secretStr"] = encrypt(p)
        url = "https://%s/gateway/t/v1%s" % (API_HOST, path)
        if self.verbose:
            print("  → GET  %s" % url)
        try:
            r = self.s.get(url, params=p)
        except Exception as e:
            return {"code": -999, "message": "网络异常: %s" % e}
        try:
            return json.loads(r.text)
        except Exception:
            return {"code": -998, "raw": r.text[:300]}


def show(r: dict, full: bool = False):
    code = r.get("code")
    mark = "✅" if code == 0 else "❌"
    print("  %s code=%s  %s" % (mark, code, r.get("message", "")))
    if code == 0 and full:
        print(json.dumps(r.get("data"), ensure_ascii=False, indent=2)[:4000])


# ============================================================
#  只读命令
# ============================================================
def cmd_course(c: Client, a):
    print("\n【课程信息 queryCourse】")
    r = c.post("/learning/queryCourse", {"recruitAndCourseId": a.course})
    if r.get("code") != 0:
        show(r)
        return
    d = r["data"]
    ci = d.get("courseInfo", {})
    vd = d.get("videoData", {})
    print("  ✅ code=0")
    print("    课程名      : %s" % ci.get("name"))
    print("    课程 ID     : %s" % ci.get("courseId"))
    print("    recruitId   : %s" % d.get("recruitId"))
    print("    共享课      : %s" % d.get("isShareCourse"))
    print("    习惯分开关  : %s" % d.get("hasHabbitScore"))
    print("    学习状态    : %s" % d.get("studyStatus"))
    print("    可跳章      : %s" % vd.get("canJumpChapter"))


def _videolist(c: Client, rac: str):
    r = c.post("/learning/videolist", {"recruitAndCourseId": rac})
    if r.get("code") != 0:
        return None, r
    return r["data"], r


def cmd_chapters(c: Client, a):
    print("\n【章节视频树 videolist】")
    data, r = _videolist(c, a.course)
    if data is None:
        show(r)
        return
    total = 0
    for ch in data.get("videoChapterDtos", []):
        print("\n  ▶ %s" % ch.get("name"))
        for l in ch.get("videoLessons", []):
            smalls = l.get("videoSmallLessons") or ([l] if l.get("videoId") else [])
            for v in smalls:
                ts = v.get("videoSec") or 0
                total += ts
                print("      [%-9s]  %-42s %5ss" % (
                    v.get("videoId"), (v.get("name") or "")[:42], ts))
    print("\n  合计时长: %d 秒（%.2f 小时）" % (total, total / 3600))


def cmd_state(c: Client, a):
    print("\n【学习状态 queryStuyInfo】")
    data, r = _videolist(c, a.course)
    if data is None:
        show(r)
        return
    lesson_ids, video_ids, name_map = [], [], {}
    for ch in data.get("videoChapterDtos", []):
        for l in ch.get("videoLessons", []):
            lesson_ids.append(l["id"])
            smalls = l.get("videoSmallLessons") or ([l] if l.get("videoId") else [])
            for v in smalls:
                if v.get("id"):
                    video_ids.append(v["id"])
                name_map[str(v.get("videoId"))] = (v.get("name"), v.get("id"), v.get("videoSec"))

    rc = c.post("/learning/queryCourse", {"recruitAndCourseId": a.course})
    recruit_id = rc.get("data", {}).get("recruitId")
    if not recruit_id:
        print("  ❌ 拿不到 recruitId")
        return

    r = c.post("/learning/queryStuyInfo", {
        "lessonIds": lesson_ids, "lessonVideoIds": video_ids, "recruitId": recruit_id})
    if r.get("code") != 0:
        show(r)
        return

    st = r.get("data") or {}
    lv = st.get("lv") or {}
    lesson = st.get("lesson") or {}
    print("  ✅ code=0")
    print("     %-12s %-40s %-8s %s" % ("videoId", "名称", "状态", "已学/总长"))
    for vid, (nm, small_id, sec) in name_map.items():
        s = lv.get(str(small_id)) or lesson.get(str(small_id)) or {}
        ws = s.get("watchState", "-")
        ttt = s.get("studyTotalTime", 0)
        flag = {1: "✅ 完成", 0: "⬜ 未看", 2: "🔶 部分", "-": "❔"}.get(ws, ws)
        print("     %-12s %-40s %-8s %s/%s" % (vid, (nm or "")[:40], flag, ttt, sec))


def cmd_diag(c: Client, a):
    print("\n【账号健康检查 exceptionActionDetail】")
    r = c.post("/cheat/exceptionActionDetail", {
        "schoolId": a.school, "recruitId": a.recruit})
    show(r, full=True)
    if r.get("code") == 0:
        d = r.get("data") or {}
        print("\n  解读:")
        print("    异常次数 count       = %s   %s" % (
            d.get("count"), "✅ 干净" if d.get("count") == 0 else "⚠️ 有记录"))
        print("    锁定     lock        = %s   %s" % (
            d.get("lock"), "✅ 未锁" if not d.get("lock") else "❌ 已锁"))
        print("    处罚类型 punishType  = %s   %s" % (
            d.get("punishType"), "✅ 无" if d.get("punishType") == 0 else "❌ 有处罚"))

    print("\n【学习时长限制 queryStudiedTimeLimitInfo】")
    r2 = c.get("/course/queryStudiedTimeLimitInfo", {
        "schoolId": a.school, "recruitId": a.recruit})
    show(r2, full=True)


def cmd_rules(c: Client, a):
    print("\n【成绩规则与课程时间线 queryStudyReadBefore】")
    r = c.post("/learning/queryStudyReadBefore", {
        "courseId": a.course_id, "recruitId": a.recruit})
    show(r, full=True)


def cmd_crypto(c: Client, a):
    raw = a.extra[0] if a.extra else '{"demo":"hello"}'
    try:
        obj = json.loads(raw)
    except Exception:
        sys.exit("参数需是合法 JSON")
    print("\n【加密演示】")
    print("  明文      : %s" % json.dumps(obj, ensure_ascii=False))
    enc = encrypt(obj)
    print("  密钥      : %s" % VIDEO_KEY.decode())
    print("  IV        : %s   （全局固定！相同明文 → 相同密文）" % IV.decode())
    print("  secretStr : %s" % enc)
    print("  解密回验  : %s" % json.dumps(decrypt(enc), ensure_ascii=False))
    print("\n  dateFormate = %d   （注意末三位恒为 000）" % date_formate())
    print("\n【ev 编码演示】")
    demo = [1, 2, 3, 4, 5, "0", 10, 20, 20, "abcdzhs"]
    print("  数组      : %s" % json.dumps(demo))
    print("  sdsew     : %s" % ev_encode(demo))


def cmd_whoami(c: Client, a):
    print("\n【会话身份解析 CASLOGC】")
    m = re.search(r"CASLOGC=([^;]+)", a.cookie)
    if not m:
        print("  ❌ Cookie 里没有 CASLOGC")
        return
    info = json.loads(unquote(m.group(1)))
    print("  字段清单: %s" % list(info.keys()))
    # 脱敏显示
    for k, v in info.items():
        if k in ("realName", "username"):
            print("    %-10s = %s" % (k, (str(v)[:2] + "*" * 6) if v else ""))
        else:
            print("    %-10s = %s" % (k, v))

    print("\n【jt-cas 会话有效期】")
    m2 = re.search(r"jt-cas=([^;]+)", a.cookie)
    if m2:
        try:
            p = m2.group(1).split(".")[1]
            pay = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
            import datetime
            iat, exp = pay.get("iat"), pay.get("exp")
            print("  签发 iat : %s" % datetime.datetime.fromtimestamp(iat))
            print("  到期 exp : %s" % datetime.datetime.fromtimestamp(exp))
            print("  有效期   : %.1f 小时" % ((exp - iat) / 3600))
            left = (exp - time.time()) / 3600
            print("  剩余     : %s" % ("%.1f 小时" % left if left > 0 else "❌ 已过期"))
        except Exception as e:
            print("  解析失败: %s" % e)


# ============================================================
#  入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(
        description="智慧树共享课只读 API 客户端（协议研究演示）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="只读工具，不含任何学习行为模拟。详见 DISCLAIMER.md")
    ap.add_argument("--cookie", required=True, help="会话 Cookie（自行从浏览器导出）")
    ap.add_argument("--course", help="recruitAndCourseId（从课程页 URL 获取）")
    ap.add_argument("--course-id", dest="course_id", help="courseId（数字）")
    ap.add_argument("--recruit", help="recruitId")
    ap.add_argument("--school", help="schoolId")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印请求详情")
    ap.add_argument("command", choices=[
        "whoami", "course", "chapters", "state", "diag", "rules", "crypto"],
        help="whoami=解析身份 / course=课程 / chapters=章节 / state=学习状态 "
             "/ diag=健康检查 / rules=成绩规则 / crypto=加密演示")
    ap.add_argument("extra", nargs="*", help="crypto 命令的 JSON 参数")
    a = ap.parse_args()

    need = {"course": "course", "chapters": "course", "state": "course"}
    if a.command in need and not a.course:
        sys.exit("该命令需要 --course <RECRUIT_AND_COURSE_ID>")
    if a.command == "diag" and not (a.school and a.recruit):
        sys.exit("diag 需要 --school <ID> --recruit <ID>")
    if a.command == "rules" and not (a.course_id and a.recruit):
        sys.exit("rules 需要 --course-id <ID> --recruit <ID>")

    c = Client(a.cookie, a.verbose)
    {
        "whoami": cmd_whoami, "course": cmd_course, "chapters": cmd_chapters,
        "state": cmd_state, "diag": cmd_diag, "rules": cmd_rules, "crypto": cmd_crypto,
    }[a.command](c, a)


if __name__ == "__main__":
    print("=" * 60)
    print("  智慧树共享课 · 只读 API 客户端")
    print("  仅供协议研究与学习。详见 DISCLAIMER.md")
    print("=" * 60)
    main()
