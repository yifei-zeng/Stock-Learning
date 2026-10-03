#!/usr/bin/env python3
"""B站 UP主投稿目录拉取器：把某个 mid 的全部投稿/合集变成 markdown 清单。

只拉「目录元数据」（BV号/标题/时长/发布日期），不下载任何逐字稿/文稿。
复用 bili_transcript.py 的 Bili 类（wbi 签名 + buvid3 + SESSDATA）。

实测结论（2026-10-03，别删）：
  - seasons_series_list 匿名 -400、带签名+SESSDATA 仍 -400（疑似要 w_webid）→ 弃用
  - /x/space/wbi/arc/search 无 w_webid 直接 412 → 弃用
  - ✅ /x/series/recArchivesByKeywords 可用（keywords 留空 = 全部投稿翻页），
    但有速率风控：连跑两次就 412，必须慢速翻页 + 遇 412 长退避重试
  - ✅ /x/web-interface/view 对合集内视频返回 ugc_season，一次拿到整个合集的剧集清单
  - 空间页 HTML 是 10KB JS 空壳，服务端拿不到 w_webid，别再试

用法:
  python3 bili_space_list.py --mid 433280310
  python3 bili_space_list.py --mid 433280310 --season-bv BV1rRt36GEGr   # 顺带展开该合集
  python3 bili_space_list.py --mid 433280310 --out 清单.md
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bili_transcript as bt


def get_all_submissions(cli, mid, ps=50, max_pages=30, pause=3.0,
                        retry_wait=60, max_retries=3, keywords=""):
    """全部投稿翻页（recArchivesByKeywords）。keywords 留空=全部投稿，给值=空间内搜索。
    返回 (dict{bvid: arc}, total, err)。"""
    allv, pn, total = {}, 1, None
    while True:
        d = None
        for attempt in range(max_retries + 1):
            try:
                d = cli.get("/x/series/recArchivesByKeywords",
                            {"mid": mid, "keywords": keywords, "pn": pn, "ps": ps})
            except Exception as e:
                d = {"code": "HTTP", "message": str(e)[:90]}
            if d.get("code") == 0:
                break
            if attempt < max_retries:
                print("  ⚠ pn=%s 失败（%s），%ds 后重试 %d/%d"
                      % (pn, d.get("message"), retry_wait, attempt + 1, max_retries),
                      file=sys.stderr)
                time.sleep(retry_wait)
        if d is None or d.get("code") != 0:
            return allv, total, "pn=%s: %s" % (pn, (d or {}).get("message"))
        data = d.get("data") or {}
        arcs = data.get("archives") or []
        total = (data.get("page") or {}).get("total") or total
        for a in arcs:
            allv[a["bvid"]] = a
        print("  pn=%d：+%d（累计 %d / total=%s）" % (pn, len(arcs), len(allv), total),
              file=sys.stderr)
        if not arcs or (total and len(allv) >= total) or pn >= max_pages:
            return allv, total, None
        pn += 1
        time.sleep(pause)


def get_season(cli, bvid):
    """用合集内任意一集的 BV，通过 view 接口的 ugc_season 拿整个合集。返回 (season, err)。"""
    d = cli.get("/x/web-interface/view", {"bvid": bvid})
    if d.get("code") != 0:
        return None, "view code=%s %s" % (d.get("code"), d.get("message"))
    season = (d.get("data") or {}).get("ugc_season") or {}
    if not season:
        return None, "该视频不在任何合集内"
    eps = []
    for sec in season.get("sections") or []:
        for ep in sec.get("episodes") or []:
            arc = ep.get("arc") or {}
            eps.append({
                "bvid": arc.get("bvid") or ep.get("bvid"),
                "title": arc.get("title") or ep.get("title"),
                "duration": arc.get("duration") or ep.get("duration"),
                "pubdate": arc.get("pubdate"),
            })
    eps.sort(key=lambda x: x.get("pubdate") or 0)
    return {"id": season.get("id"), "title": season.get("title"), "eps": eps}, None


def ts_len(sec):
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        return "?"
    return "%d:%02d" % (sec // 60, sec % 60) if sec < 3600 else \
        "%d:%02d:%02d" % (sec // 3600, sec // 60 % 60, sec % 60)


def day(ts_):
    return time.strftime("%Y-%m-%d", time.localtime(ts_ or 0))


def get_up_name(cli, mid):
    """取 UP主昵称（/x/web-interface/card）。失败返回 None。"""
    try:
        d = cli.get("/x/web-interface/card", {"mid": mid})
    except Exception as e:
        return None, str(e)[:80]
    if d.get("code") != 0:
        return None, "code=%s %s" % (d.get("code"), d.get("message"))
    card = (d.get("data") or {}).get("card") or {}
    return card.get("name"), None


def to_md(mid, allv, total, err, seasons, up_name=None, keyword=""):
    head = "# UP主 %s（mid=%s）" % (up_name or "?", mid)
    if keyword:
        head += " · 空间内搜索「%s」" % keyword
    lines = [head + "（拉取时间 %s）" % time.strftime("%Y-%m-%d"), ""]
    seen = set()
    for s in seasons:
        lines.append("## 合集：%s（season_id=%s，%d 集）" % (s["title"], s["id"], len(s["eps"])))
        lines.append("")
        lines.append("| # | 标题 | 时长 | 发布日期 | BV号 | 链接 |")
        lines.append("|---|---|---|---|---|---|")
        for i, e in enumerate(s["eps"], 1):
            seen.add(e["bvid"])
            lines.append("| %d | %s | %s | %s | %s | https://www.bilibili.com/video/%s |" % (
                i, (e["title"] or "").replace("|", "\\|"), ts_len(e["duration"]),
                day(e["pubdate"]), e["bvid"], e["bvid"]))
        lines.append("")

    extra = sorted((a for b, a in allv.items() if b not in seen),
                   key=lambda x: x.get("pubdate") or 0)
    lines.append("## %s（%d 篇）" % ("搜索结果「%s」" % keyword if keyword else "合集外的散投稿", len(extra)))
    lines.append("")
    if extra:
        lines.append("| # | 标题 | 时长 | 发布日期 | 播放 | BV号 | 链接 |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, a in enumerate(extra, 1):
            lines.append("| %d | %s | %s | %s | %s | %s | https://www.bilibili.com/video/%s |" % (
                i, (a.get("title") or "").replace("|", "\\|"), ts_len(a.get("duration")),
                day(a.get("pubdate")), (a.get("stat") or {}).get("view", "-"),
                a.get("bvid"), a.get("bvid")))
    else:
        lines.append("> 无（%s）" % ("关键词无命中" if keyword else "全部投稿都在上方合集内"))
    lines.append("")
    if err:
        lines.append("> ⚠ 全部投稿未拉全（total=%s，已拉 %s 篇）：`%s`。等 10 分钟后重跑本脚本即可续传。"
                     % (total, len(allv), err))
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="拉取 B站 UP主投稿目录（只拉元数据，不下载正文）")
    ap.add_argument("--mid", type=int, required=True, help="UP主的 mid（space.bilibili.com/<mid>）")
    ap.add_argument("--season-bv", action="append", default=[],
                    help="合集内任意一集的 BV 号，可重复传入；用于展开完整合集")
    ap.add_argument("--keyword", default="", help="空间内搜索关键词（给值=搜索，留空=全部投稿）")
    ap.add_argument("--out", default=None, help="markdown 输出路径（默认打印到 stdout）")
    ap.add_argument("--json", default=None, help="原始数据调试落盘路径（可选）")
    args = ap.parse_args()

    sessdata = bt.load_sessdata(None)
    if not sessdata:
        raise SystemExit("未找到 SESSDATA（~/.bili_sessdata），该接口需要登录态")
    cli = bt.Bili(sessdata)
    up_name, nerr = get_up_name(cli, args.mid)
    if up_name:
        print("UP主：%s（mid=%s）" % (up_name, args.mid), file=sys.stderr)
    else:
        print("⚠ 取昵称失败：%s" % nerr, file=sys.stderr)
    print("开始拉取%s（慢速翻页，遇 412 自动退避）"
          % ("关键词「%s」的搜索结果" % args.keyword if args.keyword else "全部投稿"), file=sys.stderr)

    allv, total, err = get_all_submissions(cli, args.mid, keywords=args.keyword)
    seasons = []
    for bv in args.season_bv:
        s, e = get_season(cli, bv)
        if s:
            print("合集「%s」：%d 集" % (s["title"], len(s["eps"])), file=sys.stderr)
            seasons.append(s)
            time.sleep(2)
        else:
            print("⚠ %s: %s" % (bv, e), file=sys.stderr)

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"all": allv, "total": total, "err": err, "seasons": seasons},
            ensure_ascii=False, indent=1), encoding="utf-8")

    md = to_md(args.mid, allv, total, err, seasons, up_name=up_name, keyword=args.keyword)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print("输出：%s" % args.out, file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
