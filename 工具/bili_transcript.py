#!/usr/bin/env python3
"""B站视频正文提取器：把视频链接变成可阅读的逐字稿 / AI 文稿。

关键前提（踩过坑，别删）：
  - 只带 SESSDATA 调 conclusion/get 会返回 -403，必须做 wbi 签名
  - 只带 SESSDATA 调 player/v2 经常拿不到字幕，必须同时带 buvid3
  两者都由本脚本自动处理。

三个阶段，可全跑：
  1. CC 字幕 / AI 字幕  —— player/v2 → 字幕 JSON，秒级，最可靠
     （只落地中文轨道，en/ar/es/ja/pt 等翻译轨自动跳过）
  2. 官方 AI 文稿      —— conclusion/get（wbi 签名），含总结+大纲+逐字稿
  3. 都没拿到           —— 提示改用 yt-dlp + whisper 本地转写

仅依赖 Python 标准库。
用法:
  python3 bili_transcript.py BV1oPFDzQEG7
  python3 bili_transcript.py "https://www.bilibili.com/video/BV1oPFDzQEG7" --out ~/Desktop/bili
  python3 bili_transcript.py "https://b23.tv/xxxx" --sessdata "你的SESSDATA"
SESSDATA 也可通过环境变量 BILI_SESSDATA 或文件 ~/.bili_sessdata 提供。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
TEXT_KEYS = {"content", "summary", "conclusion", "text", "result", "desc", "title"}
# wbi 混合密钥重排表（B站公开算法）
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5,
             49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24,
             55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
             57, 62, 11, 36, 20, 34, 44, 52]


class Bili:
    def __init__(self, sessdata):
        self.sessdata = sessdata
        self.cookie = "SESSDATA=%s" % sessdata  # 必须先赋值，_buvid3 里的 _req 会用到
        self._wbi_key = None
        self.buvid3 = self._buvid3()
        if self.buvid3:
            self.cookie += "; buvid3=%s" % self.buvid3

    def _req(self, url):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Cookie": self.cookie,
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _buvid3(self):
        try:
            return self._req("https://api.bilibili.com/x/frontend/finger/spi").get("data", {}).get("b_3", "")
        except Exception:
            return ""

    def get(self, path, params=None, signed=False):
        url = "https://api.bilibili.com" + path
        if params:
            url += "?" + (self._sign(params) if signed else urllib.parse.urlencode(params))
        return self._req(url)

    def get_raw(self, url):
        return self._req(url)

    def wbi_key(self):
        if self._wbi_key:
            return self._wbi_key
        nav = self.get("/x/web-interface/nav")
        img = nav["data"]["wbi_img"]["img_url"].rsplit("/", 1)[-1].split(".")[0]
        sub = nav["data"]["wbi_img"]["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        raw = img + sub
        self._wbi_key = "".join(raw[i] for i in MIXIN_TAB)[:32]
        return self._wbi_key

    def _sign(self, params):
        params = dict(params)
        params["wts"] = int(time.time())
        qs = urllib.parse.urlencode(sorted(params.items()))
        return qs + "&w_rid=" + hashlib.md5((qs + self.wbi_key()).encode()).hexdigest()


def http_anon(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def resolve_input(raw):
    """支持完整链接、b23.tv 短链、纯 BV 号。"""
    m = BV_RE.search(raw)
    if m:
        return m.group(0)
    if raw.startswith("http") or "b23.tv" in raw:
        url = raw if raw.startswith("http") else "https://" + raw
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            final = resp.geturl()
        m = BV_RE.search(final)
        if m:
            return m.group(0)
        raise SystemExit("短链解析后未找到 BV 号，最终跳转: %s" % final)
    raise SystemExit("无法识别输入，请给出 BV 号或 B站视频链接: %s" % raw)


def ts(sec):
    try:
        sec = int(float(sec or 0))
    except (TypeError, ValueError):
        return "00:00"
    if sec >= 3600:
        return "%02d:%02d:%02d" % (sec // 3600, sec // 60 % 60, sec % 60)
    return "%02d:%02d" % (sec // 60, sec % 60)


def get_meta(cli, bvid):
    d = cli.get("/x/web-interface/view", {"bvid": bvid}) if cli else \
        http_anon("https://api.bilibili.com/x/web-interface/view?bvid=%s" % bvid)
    if d.get("code") != 0:
        raise SystemExit("获取视频信息失败: %s (code=%s)" % (d.get("message"), d.get("code")))
    data = d["data"]
    return {
        "bvid": bvid,
        "aid": data.get("aid"),
        "cid": data["cid"],
        "title": data.get("title", ""),
        "up": data.get("owner", {}).get("name", ""),
        "up_mid": data.get("owner", {}).get("mid"),
        "duration": data.get("duration"),
        "desc": data.get("desc", ""),
    }


def fetch_subtitle_tracks(cli, meta):
    """抓全部字幕轨道。

    必须用签名的 /x/player/wbi/v2：未签名的 /x/player/v2 会轮换返回别的视频的字幕，
    已实测拿到过完全无关的内容。返回每条轨道的元信息 + 正文，由调用方挑选。
    """
    d = cli.get("/x/player/wbi/v2",
                {"aid": meta.get("aid") or 0, "cid": meta["cid"], "bvid": meta["bvid"]},
                signed=True)
    subs = ((d.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    tracks = []
    for s in subs:
        url = s.get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            continue
        try:
            body = cli.get_raw(url).get("body") or []
        except Exception:
            continue
        if not body:
            continue
        last = 0.0
        for it in body:
            try:
                last = max(last, float(it.get("to") or it.get("from") or 0))
            except (TypeError, ValueError):
                pass
        text = "\n".join("[%s] %s" % (ts(i.get("from")), i.get("content", "")) for i in body)
        tracks.append({
            "lan": s.get("lan", ""),
            "lan_doc": s.get("lan_doc") or s.get("lan", ""),
            "lines": len(body),
            "chars": sum(len(i.get("content", "")) for i in body),
            "coverage": last,
            "preview": (body[0].get("content", "") or "")[:60],
            "text": text,
        })
    return tracks


def is_zh(t):
    """判断轨道是否为中文。

    注意：B站轨道名是 ai-zh / zh-CN / zh-Hans，不能只判断 startswith("zh")
    （ai-zh 不以 zh 开头），也不能漏掉 lan_doc 里写"中文"的情况。
    """
    lan = str(t.get("lan", "")).lower()
    return lan == "ai-zh" or lan.startswith("zh") or "中文" in str(t.get("lan_doc", ""))


def pick_track(tracks, duration):
    """按时长覆盖率 + 字数挑一条最可信的轨道。"""
    if not tracks:
        return None
    dur = float(duration or 0)
    for t in tracks:
        t["ok"] = bool(dur and t["coverage"] >= dur * 0.7)
    ok = [t for t in tracks if t["ok"]]
    pool = ok or tracks

    zh = [t for t in pool if is_zh(t)]
    return (zh or sorted(pool, key=lambda x: x["chars"], reverse=True))[0]


def flatten(obj, keys=TEXT_KEYS):
    """递归抽取任意结构里的文本字段，兼容 B站接口改版。"""
    out = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, str) and v.strip():
                    out.append(v.strip())
                else:
                    walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(obj)
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def parse_conclusion(cj):
    """返回 (文本, status)。兼容 model_result 的 summary/outline/subtitle 结构。"""
    d = cj.get("data") or {}
    mr = d.get("model_result") or {}
    parts = []

    if mr.get("summary"):
        parts.append("【视频总结】\n" + mr["summary"])

    outline = mr.get("outline") or []
    if outline:
        buf = ["【内容大纲】"]
        for it in outline:
            if isinstance(it, dict):
                title = it.get("title") or it.get("point") or ""
                body = it.get("content") or it.get("summary") or ""
                t = it.get("time") if it.get("time") is not None else it.get("from")
                line = ("[%s] %s" % (ts(t), title)) if t is not None else str(title)
                if body:
                    line += "\n" + body
                buf.append(line)
            else:
                buf.append(str(it))
        parts.append("\n".join(buf))

    msub = mr.get("subtitle")
    if isinstance(msub, list) and msub:
        lines = ["[%s] %s" % (ts(i.get("from")), i.get("content", ""))
                 for i in msub if isinstance(i, dict)]
        if lines:
            parts.append("【AI 逐字稿】\n" + "\n".join(lines))

    if not parts:
        texts = flatten(d)
        if texts:
            parts.append("\n\n".join(texts))

    return "\n\n".join(parts), d.get("status")


def load_sessdata(cli_value):
    if cli_value:
        return cli_value.strip().strip('"').strip("'")
    env = os.environ.get("BILI_SESSDATA")
    if env:
        return env.strip()
    for p in (Path.home() / ".bili_sessdata", Path.home() / ".workbuddy" / ".bili_sessdata"):
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    return None


def main():
    ap = argparse.ArgumentParser(description="提取 B站视频正文（字幕 / 官方 AI 文稿）")
    ap.add_argument("video", help="BV 号或 B站视频链接（含 b23.tv 短链）")
    ap.add_argument("--sessdata", default=None, help="B站登录 cookie 中的 SESSDATA 值")
    ap.add_argument("--out", default="./bili", help="输出目录，默认 ./bili")
    ap.add_argument("--skip-subtitle", action="store_true", help="跳过字幕")
    ap.add_argument("--skip-conclusion", action="store_true", help="跳过官方 AI 文稿")
    args = ap.parse_args()

    sessdata = load_sessdata(args.sessdata)
    bvid = resolve_input(args.video)
    cli = Bili(sessdata) if sessdata else None
    meta = get_meta(cli, bvid)

    print("标题  : %s" % meta["title"])
    print("UP主  : %s" % meta["up"])
    print("时长  : %s 秒" % meta["duration"])
    print("登录态: %s" % ("已提供 SESSDATA" if sessdata else "未提供（AI 文稿不可用）"))

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[^\w\u4e00-\u9fff-]+', "_", meta["title"])[:60] or bvid

    results = {}

    if not args.skip_subtitle:
        try:
            tracks = fetch_subtitle_tracks(cli, meta)
            if not tracks:
                print("字幕  : 该视频没有可用字幕")
            else:
                # 只保留中文轨道：en/ar/es/ja/pt 等翻译轨不落地，避免输出目录被刷屏
                zh_tracks = [t for t in tracks if is_zh(t)]
                skipped = [t for t in tracks if not is_zh(t)]
                print("字幕  : 共 %d 条轨道（中文 %d 条，已跳过非中文 %d 条：%s）"
                      % (len(tracks), len(zh_tracks), len(skipped),
                         ",".join(t["lan"] for t in skipped) or "无"))
                for t in zh_tracks:
                    flag = "匹配" if (meta.get("duration") and t["coverage"] >= meta["duration"] * 0.7) else "时长不足"
                    print("        - %-6s %-8s %3d行 %5d字 覆盖%ds/%ss [%s] 首句：%s"
                          % (t["lan"], t["lan_doc"], t["lines"], t["chars"],
                             int(t["coverage"]), meta.get("duration"), flag, t["preview"]))
                    (out_dir / ("%s.%s.txt" % (stem, t["lan"]))).write_text(
                        "视频：%s\n轨道：%s\n\n%s" % (meta["title"], t["lan_doc"], t["text"]),
                        encoding="utf-8")
                if not zh_tracks:
                    print("字幕  : ⚠ 没有中文轨道，该视频可能只有外语字幕")
                best = pick_track(zh_tracks or tracks, meta.get("duration"))
                results["subtitle"] = best["text"]
                if not best.get("ok"):
                    results["subtitle_warn"] = True
                print("字幕  : 采用 %s（%d 行，%d 字）%s"
                      % (best["lan_doc"], best["lines"], best["chars"],
                         "" if best.get("ok") else "⚠ 时长覆盖不足，请人工核对"))
        except Exception as e:
            print("字幕  : 抓取失败 %s" % e)

    if not args.skip_conclusion:
        if not cli:
            print("AI文稿: 跳过（未提供 SESSDATA）")
        else:
            try:
                cj = cli.get("/x/web-interface/view/conclusion/get",
                             {"bvid": bvid, "cid": meta["cid"],
                              "up_mid": meta["up_mid"] or 0, "web_location": 0},
                             signed=True)
                (out_dir / ("%s.conclusion.json" % stem)).write_text(
                    json.dumps(cj, ensure_ascii=False, indent=2), encoding="utf-8")
                code = cj.get("code")
                if code == 0:
                    text, status = parse_conclusion(cj)
                    if text:
                        results["conclusion"] = text
                        print("AI文稿: 成功（%d 字，status=%s）" % (len(text), status))
                    else:
                        print("AI文稿: 接口返回空（status=%s），该视频未生成 AI 总结" % status)
                elif code == -403:
                    print("AI文稿: 失败 -403，SESSDATA 无效/过期，或签名被拒")
                else:
                    print("AI文稿: 失败 %s (code=%s)" % (cj.get("message"), code))
            except Exception as e:
                print("AI文稿: 请求异常 %s" % e)

    if not results:
        print("\n没有拿到任何正文。可选：")
        print("  1) 换一个视频试试（该视频可能确实没字幕、没 AI 总结）")
        print("  2) 改用本地转写：brew install yt-dlp ffmpeg 后下音频跑 faster-whisper")
        sys.exit(2)

    header = ("视频：%s\nUP主：%s\n链接：https://www.bilibili.com/video/%s\n时长：%s 秒\n%s\n\n"
              % (meta["title"], meta["up"], bvid, meta["duration"], "=" * 40))
    parts = []
    if "subtitle" in results:
        parts.append("===== 逐字字幕 =====\n" + results["subtitle"])
    if "conclusion" in results:
        parts.append("===== 官方 AI 文稿 =====\n" + results["conclusion"])
    body = header + "\n\n".join(parts)

    txt_path = out_dir / ("%s.txt" % stem)
    txt_path.write_text(body, encoding="utf-8")
    print("\n输出：%s（共 %d 字）" % (txt_path, len(body)))
    print(str(txt_path))


if __name__ == "__main__":
    main()
