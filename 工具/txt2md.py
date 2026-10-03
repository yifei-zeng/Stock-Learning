#!/usr/bin/env python3
"""把 1-精简版-人读/ 的 .txt 转成可渲染 .md（三个库通用）。

兼容三种现存形态：
  A派 `## 一、xxx` 标题 + `[MM:SS] 正文` 流水（逻辑哥 12 篇、巨拳 13 篇）
  B 派 `【一、xxx】` 分节（巨拳 02/05/07/10/13/15）
  C 派 已是 v2 形态：头部「一句话结论」+ `30 秒速读`/`名词表`/`目录` + `───` 分隔线 + `### 要点`
     正文不带时间戳行首（来去由心 13 篇）

**只换载体、不改内容。**

层级判定按内容特征，不看符号：
  → `##`：数字编号（一、/1.）｜`标准N：`｜头部三件套｜待确认项/校正记录
  → `###`：其余主题小节｜原文的 `### 要点`

用法：
  python txt2md.py --dir <目录> --dry
  python txt2md.py --dir <目录>
"""
import argparse
import re
from pathlib import Path

RE_META = re.compile(r'^(视频|UP主|链接|时长)：(.*)$')
RE_TT = re.compile(r'^【([^】]+)】\s*$')
RE_H2 = re.compile(r'^##\s+(.+?)\s*$')
RE_H3 = re.compile(r'^###\s+(.+?)\s*$')
RE_RULE = re.compile(r'^[─—\-=]{4,}\s*$')
RE_ONELINE = re.compile(r'^一句话结论[:：]')

TOPICS_H2 = ('30 秒速读', '30秒速读', '名词表', '目录', '待确认项', '校正记录', '校正记录（')
RE_NUM = re.compile(r'^([一二三四五六七八九十]+、|\d+[\.、])')
RE_STD = re.compile(r'^标准[一二三四五六七八九十\d]+')


def is_h2(title):
    if any(title.startswith(t) for t in TOPICS_H2):
        return True
    return bool(RE_NUM.match(title) or RE_STD.match(title))


def parse(lines):
    """抽头部元信息与「一句话结论」，返回 (meta, oneline, body)。"""
    meta, oneline = {}, None
    i = 0
    for i, l in enumerate(lines[:10]):
        s = l.strip()
        m = RE_META.match(l)
        if m:
            meta[m.group(1)] = m.group(2).strip()
        elif RE_ONELINE.match(s):
            oneline = s
    # body 起点：跳过头部元信息行、旧标记（==== / ──── / ===== xxx =====）与空行。
    # **注意：第一个 `##` 标题要保留在 body 里**——它就是首节，不能被当成头部吃掉。
    j = 0
    while j < len(lines):
        s = lines[j].strip()
        old_mark = s.startswith('=') or RE_RULE.match(s)     # ==== / ────
        skip = (RE_META.match(lines[j]) or RE_ONELINE.match(s)
                or s == '' or old_mark or RE_TT.match(s))
        if skip:
            j += 1
        else:
            break
    return meta, oneline, lines[j:]


def convert(path, dry=False):
    lines = path.read_text(encoding='utf-8').split('\n')
    meta, oneline, body = parse(lines)

    out = ['# %s' % meta.get('视频', path.stem.replace('.精简版', '')), '']
    out += ['| 项 | 内容 |', '|---|---|',
            '| UP主 | %s |' % meta.get('UP主', ''),
            '| 时长 | %s |' % meta.get('时长', '')]
    if meta.get('链接'):
        out.append('| 链接 | [%s](%s) |' % (meta['链接'], meta['链接']))
    out.append('')
    if oneline:
        out += [oneline, '']
    out += ['---', '']

    for idx, l in enumerate(body):
        s = l.rstrip()
        if not s:
            out.append('')
            continue
        if RE_RULE.match(s.strip()):       # ──── / ==== 分隔线
            # 紧邻标题的分隔线跳过（上下都是空行且下一非空行是标题），避免冗余
            prev_blank = (not out) or out[-1] == ''
            next_is_head = False
            for k in range(idx + 1, len(body)):
                if body[k].strip():
                    nxt = body[k].strip()
                    next_is_head = bool(RE_H2.match(nxt) or RE_H3.match(nxt)
                                        or RE_TT.match(nxt))
                    break
            if prev_blank and next_is_head:
                continue
            out.append('---')
            out.append('')
            continue
        m = RE_H3.match(s)                  # 原文的 ### 要点等，保留
        if m:
            out.append('### %s' % m.group(1).strip())
            continue
        m = RE_H2.match(s)
        if m:
            t = m.group(1).strip()
            if t.startswith('待确认项'):
                out.append('## 待确认项')
            elif t.startswith('校正记录'):
                out.append('## 校正记录')
            else:
                out.append('## %s' % t)
            continue
        m = RE_TT.match(s)
        if m:
            t = m.group(1).strip()
            if t.startswith('待确认项'):
                out.append('## 待确认项')
            elif t.startswith('校正记录'):
                out.append('## 校正记录')
            elif is_h2(t):
                out.append('## %s' % t)
            else:
                out.append('### %s' % t)
            continue
        out.append(s)

    # 收拾多余空行；**分隔线只在## 二级标题前保留**，其余去掉避免与 H1 后的重复
    res = '\n'.join(out)
    res = re.sub(r'\n{3,}', '\n\n', res)
    res = re.sub(r'\n*---\s*\n(?=\|)', '\n\n', res)   # 表格前不留分隔线
    res = res.rstrip('\n-\n ') + '\n'
    if not dry:
        path.with_suffix('.md').write_text(res, encoding='utf-8')
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    d = Path(a.dir).expanduser()
    for f in sorted(d.glob('*.txt')):
        r = convert(f, a.dry)
        L = r.split('\n')
        print('%-44s → %5d字  ##%2d ###%2d' % (
            f.name[:44], len(r),
            len([x for x in L if x.startswith('## ')]),
            len([x for x in L if x.startswith('### ')])))


if __name__ == '__main__':
    main()
