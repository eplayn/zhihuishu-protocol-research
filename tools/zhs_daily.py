#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树共享课 · 每日一键签到（含扫码登录）
================================================================

本脚本是本项目【协议研究成果的工程化验证】：
它把 docs/ 中还原的登录链路、加密方案、上报节奏与字段结构，
组装成一个可运行的完整实现，用以证明研究结论成立。

! 仅供技术学习与研究。禁止商用。详见 DISCLAIMER.md !

----------------------------------------------------------------
【它做什么】

每天运行一次：

    python zhs_daily.py --course <RECRUIT_AND_COURSE_ID>

    1) 检查会话 —— 过期则弹出二维码，用「知到」App 扫一下
    2) 选一个视频，分批上报 25 分钟学习时长（对齐前端 300 秒节奏）
    3) 记录到 capture/daily_log.json，并打印结果

【为什么每天都要扫码】

登录态里的 jt-cas 是 JWT，有效期仅 24 小时（见 docs/02）。
而账号密码登录实测必须携带易盾滑块 token，无法纯 API 完成，
因此扫码是当前唯一可行的登录方式。

【参数】

    --course <ID>       必填。从课程页 URL 获取：
                        https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=<这里>
    --status            只看签到历史，不做任何事
    --minutes=30        自定义每日目标分钟数（默认 25）
    --relogin           强制重新扫码登录
    --force             今天已签到过也再跑一次
    --speed=1.8         假定倍速（影响 watchPoint 采样密度）

【依赖】

    pip install curl_cffi pycryptodome
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

try:
    from curl_cffi import requests as cr
except ImportError:
    sys.exit("缺少依赖：pip install curl_cffi")
try:
    from Crypto.Cipher import AES
except ImportError:
    sys.exit("缺少依赖：pip install pycryptodome")

# ============================================================
#  路径
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CAPTURE_DIR = BASE_DIR.parent / "capture"
SESSION_FILE = CAPTURE_DIR / "session.json"
DAILY_LOG = CAPTURE_DIR / "daily_log.json"
QR_PNG = CAPTURE_DIR / "qr_login.png"

API = "studyservice-api.zhihuishu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

# 加密常量（见 docs/03-crypto.md）
IV = b"1g3qqdh4jvbskb9x"
VIDEO_KEY = b"azp53h0kft7qi78q"
EV_KEY = "zzpttjd"

# 节奏常量（见 docs/05-progress-reporting.md）
REPORT_EVERY = 300.0   # 主上报间隔，前端 0x493e0 = 300000ms
SAMPLE_MS = 1.99       # watchPoint 采样周期，前端 0x7c6 = 1990ms

# 登录
QR_IMG_URL = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg"
QR_INFO_URL = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo"
SSO_SERVICE = ("https://cloudapi.polymas.com/oauth/login/ssoLogin"
               "?redirect=https://www.zhihuishu.com")
QR_WAIT_SEC = 300

_uuid = ""
_course = ""


# ============================================================
#  工具
# ============================================================
def log(*a):
    print(*a, flush=True)


def hhmmss(s):
    s = int(max(0, s))
    return "%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def pkcs7(raw):
    n = 16 - len(raw) % 16
    return raw + bytes([n]) * n


def encrypt(obj, key=VIDEO_KEY):
    raw = pkcs7(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return base64.b64encode(AES.new(key, AES.MODE_CBC, IV).encrypt(raw)).decode()


def ev_encode(arr):
    """前端 D26666.Z：join(';') -> 逐字符 XOR(zzpttjd) -> hex 补零到2位 -> slice(-4)"""
    s = ";".join(str(x) for x in arr)
    out = []
    for i, ch in enumerate(s):
        n = ord(ch) ^ ord(EV_KEY[i % len(EV_KEY)])
        h = format(n, "x")
        if len(h) < 2:
            h = "0" + h
        out.append(h[-4:])
    return "".join(out)


def jwt_exp(tok):
    try:
        p = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("exp")
    except Exception:
        return None


# ============================================================
#  会话
# ============================================================
def load_cookie():
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8")).get(API, {}).get("cookie", "")
    except Exception:
        return ""


def save_cookie(ck, extra=None):
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    rec = {"cookie": ck, "ua": UA}
    if extra:
        rec.update(extra)
    data[API] = rec
    SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def api_session(cookie):
    s = cr.Session(impersonate="chrome", timeout=25)
    s.headers.update({
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        # 注意：必须是 studyvideoh5，不是 studyh5（见 docs/07）
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
    return s


def call(s, path, payload):
    t = int(time.time()) * 1000           # 末三位恒为 000
    p = dict(payload)
    p["dateFormate"] = t
    form = {"secretStr": encrypt(p), "dateFormate": str(t)}
    try:
        r = s.post("https://%s%s" % (API, path), data=form)
    except Exception as e:
        return {"code": -999, "message": "网络异常 %s" % e}
    try:
        return json.loads(r.text)
    except Exception:
        return {"code": -998, "raw": r.text[:300]}


def verify(cookie):
    s = api_session(cookie)
    r = call(s, "/gateway/t/v1/learning/queryCourse", {"recruitAndCourseId": _course})
    return r.get("code") == 0, r


# ============================================================
#  二维码登录（见 docs/02-login-and-session.md）
# ============================================================
def qr_login():
    s = cr.Session(impersonate="chrome", timeout=25, allow_redirects=True)
    s.headers.update({"Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", "User-Agent": UA})

    log("正在获取登录二维码 ...")
    try:
        j = s.get(QR_IMG_URL).json()
    except Exception as e:
        log("二维码请求失败: %s" % e)
        return ""
    qr_token, img_b64 = j.get("qrToken"), j.get("img")
    if not qr_token or not img_b64:
        log("二维码返回异常: %s" % str(j)[:200])
        return ""

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    QR_PNG.write_bytes(base64.b64decode(img_b64))
    try:
        os.startfile(str(QR_PNG))
    except Exception:
        pass

    log("")
    log("  +----------------------------------------------+")
    log("  |  二维码已弹出，请用「知到」App 扫码并确认     |")
    log("  |  （若没自动打开，手动打开下面这个文件）        |")
    log("  +----------------------------------------------+")
    log("  %s" % QR_PNG)
    log("")
    log("  等待扫码中（最长 %d 秒）..." % QR_WAIT_SEC)

    scanned = False
    deadline = time.time() + QR_WAIT_SEC
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            msg = s.get(QR_INFO_URL, params={"qrToken": qr_token}).json()
        except Exception:
            continue
        st = msg.get("status")
        if st == -1:
            continue
        if st == 0:
            if not scanned:
                scanned = True
                log("  已扫码，请在手机上点确认")
            continue
        if st == 1:
            log("  确认成功，正在换取登录态 ...")
            try:
                s.get("https://passport.zhihuishu.com/login",
                      params={"pwd": msg.get("oncePassword"), "service": SSO_SERVICE})
            except Exception as e:
                log("  换取登录态失败: %s" % e)
                return ""
            pairs, seen = [], set()
            for c in s.cookies.jar:
                dom = (c.domain or "").lstrip(".")
                if dom not in ("zhihuishu.com", "polymas.com") or c.name in seen:
                    continue
                seen.add(c.name)
                pairs.append("%s=%s" % (c.name, c.value))
            ck = "; ".join(pairs)
            if "CASLOGC" not in seen:
                log("  没拿到 CASLOGC，登录未完成")
                return ""
            try:
                from urllib.parse import unquote
                uuid = json.loads(unquote(re.search(r"CASLOGC=([^;]+)", ck).group(1)))["uuid"]
                # 浏览器会自己设这个 cookie，纯 API 需手动补（见 docs/02）
                if ("exitRecod_" + uuid) not in ck:
                    ck += "; exitRecod_%s=2" % uuid
                log("  账号标识: %s" % uuid)
            except Exception:
                pass
            ok, _ = verify(ck)
            if not ok:
                log("  登录后接口验证失败，请重试")
                return ""
            save_cookie(ck, {"loginAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            log("  登录成功，会话已保存")
            return ck
        if st == 2:
            log("  二维码已过期")
            return ""
        if st == 3:
            log("  你在手机上取消了")
            return ""
    log("  等待超时")
    return ""


# ============================================================
#  签到日志
# ============================================================
def load_log():
    try:
        return json.loads(DAILY_LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_log(d):
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_LOG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def print_status(d):
    today = date.today().isoformat()
    log("=" * 52)
    log("  今天（%s）" % today)
    rec = d.get(today)
    if rec:
        log("    已签到   %.1f 分钟   视频「%s」   %s"
            % (rec.get("minutes", 0), rec.get("videoName", "?"), rec.get("at", "")))
    else:
        log("    还没签到")
    if d:
        log("")
        log("  历史记录：")
        for k in sorted(d.keys(), reverse=True):
            r = d[k]
            log("    %s   %5.1f 分钟   %s" % (k, r.get("minutes", 0), r.get("videoName", "")))
    log("")
    log("  累计签到 %d 天" % len(d))
    log("=" * 52)


# ============================================================
#  业务（见 docs/04-api-reference.md）
# ============================================================
def build_ctx(s):
    c = call(s, "/gateway/t/v1/learning/queryCourse", {"recruitAndCourseId": _course})
    if c.get("code") != 0:
        raise SystemExit("queryCourse 失败: %s" % json.dumps(c, ensure_ascii=False)[:200])
    vids = call(s, "/gateway/t/v1/learning/videolist", {"recruitAndCourseId": _course})
    if vids.get("code") != 0:
        raise SystemExit("videolist 失败: %s" % json.dumps(vids, ensure_ascii=False)[:200])
    videos, order, lesson_ids, video_ids = {}, [], [], []
    for ch in vids["data"]["videoChapterDtos"]:
        for l in ch.get("videoLessons", []):
            lesson_ids.append(l["id"])
            smalls = l.get("videoSmallLessons") or ([dict(l, id=0)] if l.get("videoId") else [])
            for v in smalls:
                o = {"videoId": v["videoId"], "lessonId": l["id"], "smallLessonId": v.get("id", 0),
                     "chapterId": ch["id"], "name": v.get("name"), "videoSec": v.get("videoSec")}
                videos[str(v["videoId"])] = o
                order.append(str(v["videoId"]))
                if v.get("id"):
                    video_ids.append(v["id"])
    return {"recruitId": c["data"]["recruitId"],
            "courseId": c["data"]["courseInfo"]["courseId"],
            "courseName": c["data"]["courseInfo"].get("name"),
            "hasHabbitScore": c["data"].get("hasHabbitScore"),
            "studyStatus": c["data"].get("studyStatus", "0"),
            "videos": videos, "order": order,
            "lessonIds": lesson_ids, "videoIds": video_ids}


def get_states(s, ctx):
    r = call(s, "/gateway/t/v1/learning/queryStuyInfo",
             {"lessonIds": ctx["lessonIds"], "lessonVideoIds": ctx["videoIds"],
              "recruitId": ctx["recruitId"]})
    return r.get("data") or {} if r.get("code") == 0 else {}


def state_of(st, v):
    return (st.get("lv") or {}).get(str(v["smallLessonId"])) or \
           (st.get("lesson") or {}).get(str(v["lessonId"])) or {}


def token_for(s, ctx, v):
    r = call(s, "/gateway/t/v1/learning/prelearningNote", {
        "ccCourseId": ctx["courseId"], "chapterId": v["chapterId"], "isApply": 1,
        "lessonId": v["lessonId"], "lessonVideoId": v["smallLessonId"],
        "recruitId": ctx["recruitId"], "videoId": v["videoId"]})
    return ((r.get("data") or {}).get("studiedLessonDto") or {}).get("id")


def report(s, ctx, v, total_study, play_times, wp):
    token = token_for(s, ctx, v)
    if not token:
        return None
    # sdsew 10 元素，顺序即语义（见 docs/05）
    ev = [ctx["recruitId"], v["lessonId"], v["smallLessonId"], v["videoId"], v["chapterId"],
          ctx["studyStatus"], int(round(play_times)), int(round(total_study)),
          int(round(total_study)), _uuid + "zhs"]
    payload = {"ewssw": wp.get(), "sdsew": ev_encode(ev),
               "zwsds": base64.b64encode(str(token).encode()).decode(),
               "courseId": ctx["courseId"]}
    r = call(s, "/gateway/t/v1/learning/saveDatabaseIntervalTimeV2", payload)
    code = r.get("code")
    if code == 0:
        log("    上报成功  totalStudyTime=%d  playTimes=%d" % (total_study, play_times))
    else:
        log("    上报失败  code=%s  %s" % (code, r.get("message")))
    return r


class WatchPoint:
    """前端 watchPoint：初始 "0,1"，每 1990ms 追加 floor(totalStudyTime/5)+2"""

    def __init__(self):
        self.wp = ["0", "1"]

    def add(self, t):
        self.wp.append(str(int(max(0, t) // 5) + 2))

    def get(self):
        return ",".join(self.wp)


# ============================================================
#  主流程
# ============================================================
def main():
    global _uuid, _course
    ap = argparse.ArgumentParser(
        description="智慧树共享课每日签到（协议研究工程验证）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="仅供技术学习与研究，禁止商用。详见 DISCLAIMER.md")
    ap.add_argument("--course", required=True,
                    help="recruitAndCourseId，从课程页 URL 获取")
    ap.add_argument("--status", action="store_true", help="只看签到历史")
    ap.add_argument("--minutes", type=float, default=25.0, help="每日目标分钟数（默认 25）")
    ap.add_argument("--chunk", type=float, default=540.0, help="单批上报秒数（默认 540）")
    ap.add_argument("--speed", type=float, default=1.8, help="假定倍速（影响采样密度）")
    ap.add_argument("--relogin", action="store_true", help="强制重新扫码登录")
    ap.add_argument("--force", action="store_true", help="今天已签到也再跑一次")
    a = ap.parse_args()

    _course = a.course
    dlog = load_log()

    if a.status:
        print_status(dlog)
        return

    today = date.today().isoformat()
    if today in dlog and not a.force:
        log("今天（%s）已经签到过了。" % today)
        log("   %.1f 分钟，视频「%s」，完成于 %s"
            % (dlog[today].get("minutes", 0), dlog[today].get("videoName", "?"),
               dlog[today].get("at", "")))
        log("   如需再跑一次，加 --force")
        return

    log("=" * 52)
    log("  智慧树 · 每日签到    %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 52)

    # ---- 1. 会话 ----
    ck = "" if a.relogin else load_cookie()
    ok, exp = False, None
    if ck and "jt-cas=" in ck:
        exp = jwt_exp(re.search(r"jt-cas=([^;]+)", ck).group(1))
        if exp and exp > time.time():
            ok, _ = verify(ck)
    if ok:
        log("1) 会话有效（jt-cas 剩余 %.1f 小时），直接复用" % ((exp - time.time()) / 3600))
    else:
        log("1) 需要登录（%s）" % ("旧会话已过期" if ck else "本地无会话"))
        ck = qr_login()
        if not ck:
            log("")
            log("登录未完成，本次签到中止。")
            return

    s = api_session(ck)
    try:
        from urllib.parse import unquote
        _uuid = json.loads(unquote(re.search(r"CASLOGC=([^;]+)", ck).group(1)))["uuid"]
    except Exception:
        _uuid = ""

    # ---- 2. 课程信息 ----
    log("")
    log("2) 读取课程 ...")
    ctx = build_ctx(s)
    st = get_states(s, ctx)
    log("   %s   hasHabbitScore=%s" % (ctx["courseName"], ctx["hasHabbitScore"]))

    # 轮换选视频：挑最久没用过的，避免总刷同一个
    used = {}
    for day, rec in dlog.items():
        vid = str(rec.get("videoId", ""))
        if vid:
            used[vid] = max(used.get(vid, ""), day)
    order = sorted(ctx["order"], key=lambda x: used.get(str(x), ""))
    v = ctx["videos"][order[0]]
    sec = v["videoSec"]
    base0 = int(state_of(st, v).get("studyTotalTime") or 0)
    log("   选中视频: %s  [%s]  videoSec=%s  当前 studyTotalTime=%d"
        % (v["name"], v["videoId"], sec, base0))

    srv = call(s, "/gateway/t/v1/learning/currentSystemTime", {})
    if srv.get("code") == 0:
        log("   服务器时间: %s"
            % datetime.fromtimestamp(srv["data"] / 1000).strftime("%Y-%m-%d %H:%M:%S"))

    # ---- 3. 签到 ----
    need = a.minutes * 60
    nbatch = int(need // a.chunk) + (1 if need % a.chunk else 0)
    log("")
    log("3) 开始签到：目标 %.0f 分钟（%d 秒），分 %d 批" % (a.minutes, need, nbatch))

    sent = 0.0
    base = base0
    t0 = time.time()
    batch = 0

    while sent < need:
        chunk = min(a.chunk, need - sent)
        batch += 1

        # ★ 服务端是 studyTotalTime += playTimes 累加，必须单调不减，否则 -8（见 docs/05）
        live = int(state_of(get_states(s, ctx), v).get("studyTotalTime") or 0)
        if live > base:
            base = live
        target = base + chunk

        samples = max(2, int((chunk / a.speed) / SAMPLE_MS))
        wp = WatchPoint()
        for i in range(samples):
            wp.add(base + (i + 1) * chunk / samples)

        log("")
        log("   第 %d 批：playTimes=%.0fs  totalStudyTime=%d -> %d" % (batch, chunk, base, target))
        r = report(s, ctx, v, target, chunk, wp)
        if r is None:
            log("   拿不到 learningTokenId，中止")
            return
        code = r.get("code")
        if code == -8:
            log("   -8 学习总时长下降，重跑一次会自动对齐")
            return
        if code == -12:
            log("   命中易盾验证码。请先在浏览器打开任意视频播几分钟，再重跑本脚本。")
            return
        if code in (-9, -10, 403):
            log("   账号级问题 code=%s，停止。" % code)
            return
        if code != 0:
            log("   上报返回 code=%s，停止。" % code)
            return

        sent += chunk
        base = target
        log("   已累计 %.1f / %.0f 分钟" % (sent / 60, a.minutes))

        if sent < need:
            wait = REPORT_EVERY - (time.time() - t0 - (batch - 1) * REPORT_EVERY)
            wait = max(0, min(wait, REPORT_EVERY))
            log("   等待 %.0f 秒后发下一批（对齐前端 300 秒节奏）..." % wait)
            time.sleep(wait)

    # ---- 4. 记录 ----
    dlog[today] = {
        "credited": int(sent),
        "minutes": round(sent / 60, 1),
        "videoId": str(v["videoId"]),
        "videoName": v["name"],
        "fromTotal": base0,
        "toTotal": int(base),
        "at": datetime.now().strftime("%H:%M:%S"),
        "costSec": int(time.time() - t0),
    }
    save_log(dlog)

    log("")
    log("=" * 52)
    log("  今日签到完成")
    log("     计入时长 : %.1f 分钟（目标 %.0f 分钟）" % (sent / 60, a.minutes))
    log("     耗时     : %s" % hhmmss(time.time() - t0))
    log("     视频     : %s" % v["name"])
    log("     累计签到 : %d 天" % len(dlog))
    log("     日志文件 : %s" % DAILY_LOG)
    log("=" * 52)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("")
        log("已手动中断。")
    except Exception as e:
        log("")
        log("出错: %s" % e)
        import traceback
        traceback.print_exc()
