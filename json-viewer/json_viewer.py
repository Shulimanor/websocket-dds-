#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""json_viewer.py - JSON 查看 / 格式化命令行工具

功能：
  1. 格式化输出 JSON（缩进、键排序可选）
  2. 默认将字符串值中的 \\n 转义还原为真实换行，方便人眼阅读
     （使用 --raw 可输出标准合法 JSON，保留转义）
  3. -r/--recursive 递归解析字符串值中的内嵌 JSON 文本，
     将其展开为格式化结构展示（如 "data": "{\\"a\\": 1}"）
  4. 支持从文件或标准输入读取

用法示例：
  python json_viewer.py data.json
  cat data.json | python json_viewer.py
  echo '{"a": "line1\\nline2"}' | python json_viewer.py
  python json_viewer.py -r data.json              # 递归解析内嵌 JSON
  python json_viewer.py --raw data.json           # 输出标准 JSON
  python json_viewer.py --sort-keys data.json     # 按键排序
  python json_viewer.py -i 4 data.json            # 自定义缩进
  python json_viewer.py --no-color data.json      # 关闭颜色
"""

import argparse
import json
import re
import sys

# 占位符：递归把字符串值中的真实换行替换为该控制字符，
# 序列化后它会被转义成字面 "\\u0001"（6 个 ASCII 字符），
# 再在输出文本中精确替换回真实换行，避免误伤字面 "\\n" 内容。
PLACEHOLDER = "\u0001"

# ANSI 颜色
RESET = "\033[0m"
C_KEY = "\033[35m"
C_STR = "\033[32m"
C_NUM = "\033[33m"
C_KW = "\033[36m"


def mark_newlines(node):
    """递归将字符串值中的换行替换为占位符。"""
    if isinstance(node, dict):
        return {k: mark_newlines(v) for k, v in node.items()}
    if isinstance(node, list):
        return [mark_newlines(i) for i in node]
    if isinstance(node, str):
        return node.replace("\n", PLACEHOLDER)
    return node


def _parse_embedded(s):
    """尝试把字符串解析为内嵌 JSON（dict/list 才展开）。"""
    text = s.strip()
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, (dict, list)) else None


def unroll_embedded(node):
    """递归展开字符串值中合法的内嵌 JSON（支持多层嵌套）。"""
    if isinstance(node, dict):
        return {k: unroll_embedded(v) for k, v in node.items()}
    if isinstance(node, list):
        return [unroll_embedded(i) for i in node]
    if isinstance(node, str):
        embedded = _parse_embedded(node)
        if embedded is not None:
            return unroll_embedded(embedded)
        return node
    return node


def format_json(
    data, indent=2, sort_keys=False, raw=False, newline=True, recursive=False
):
    """按视图模式序列化。

    raw=True   -> 标准合法 JSON（保留 \\n 等转义）
    newline=True -> 格式化，并将字符串值里的 \\n 还原为真实换行
    recursive=True -> 递归解析字符串值中的内嵌 JSON（raw 模式下忽略）
    """
    if recursive and not raw:
        data = unroll_embedded(data)
    if raw:
        return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
    if newline:
        marked = mark_newlines(data)
        text = json.dumps(
            marked, ensure_ascii=False, indent=indent, sort_keys=sort_keys
        )
        return text.replace("\\u0001", "\n")
    return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=sort_keys)


def highlight(text):
    """轻量 JSON 语法高亮（单遍扫描，避免嵌套着色）。"""
    out = []
    i, n = 0, len(text)
    keywords = ("true", "false", "null")
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 2
                    continue
                j += 1
            j = min(j, n - 1)
            token = text[i : j + 1]
            k = j + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            is_key = k < n and text[k] == ":"
            out.append((C_KEY if is_key else C_STR, token))
            i = j + 1
        elif ch == "-" or ch.isdigit():
            j = i + 1
            while j < n and (text[j].isdigit() or text[j] in ".eE+-"):
                j += 1
            out.append((C_NUM, text[i:j]))
            i = j
        else:
            hit = False
            for kw in keywords:
                if text.startswith(kw, i):
                    after = i + len(kw)
                    if after >= n or not (text[after].isalnum() or text[after] == "_"):
                        out.append((C_KW, kw))
                        i = after
                        hit = True
                        break
            if not hit:
                out.append(("", ch))
                i += 1
    return "".join(code + tok + RESET for code, tok in out)


def read_bytes(path):
    """读取文件，UTF-8 优先，失败回退 GBK。"""
    raw_b = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw_b.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_b.decode("utf-8", errors="replace")


def load_text(path):
    if path:
        return read_bytes(path)
    raw = sys.stdin.read()
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    return raw


def parse_json(raw):
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        lines = raw.splitlines()
        line_text = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) else ""
        caret = " " * max(e.colno - 1, 0) + "^"
        msg = "JSON 解析失败（第 {} 行，第 {} 列）: {}".format(e.lineno, e.colno, e.msg)
        return None, "\n".join([msg, "  " + line_text, "  " + caret])
    except Exception as e:  # noqa: BLE001 - 兜底错误展示
        return None, "JSON 解析失败: {}".format(e)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="json_viewer.py",
        description="JSON 格式化查看工具：默认美化输出，并将字符串值中的 \\n 还原为换行显示。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python json_viewer.py data.json\n"
            "  cat data.json | python json_viewer.py\n"
            "  python json_viewer.py --raw data.json      # 标准合法 JSON\n"
            "  python json_viewer.py --sort-keys a.json   # 按键排序\n"
        ),
    )
    parser.add_argument(
        "file", nargs="?", default=None, help="JSON 文件路径（缺省从标准输入读取）"
    )
    parser.add_argument(
        "--raw", action="store_true", help="输出标准合法 JSON，保留 \\n 等转义"
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="递归解析字符串值中的内嵌 JSON 并格式化展示（与 --raw 同用时忽略）",
    )
    parser.add_argument("--sort-keys", action="store_true", help="对象键按键名排序")
    parser.add_argument(
        "-i", "--indent", type=int, default=2, metavar="N", help="缩进空格数（默认 2）"
    )
    color_group = parser.add_mutually_exclusive_group()
    color_group.add_argument("--color", action="store_true", help="强制开启 ANSI 颜色")
    color_group.add_argument("--no-color", action="store_true", help="关闭 ANSI 颜色")
    args = parser.parse_args(argv)

    if args.indent < 0:
        args.indent = 0

    try:
        raw = load_text(args.file)
    except OSError as e:
        print("读取失败: {}".format(e), file=sys.stderr)
        return 2

    if not raw.strip():
        print("输入为空", file=sys.stderr)
        return 1

    data, err = parse_json(raw)
    if err:
        print(err, file=sys.stderr)
        return 1

    text = format_json(
        data,
        indent=args.indent,
        sort_keys=args.sort_keys,
        raw=args.raw,
        recursive=args.recursive,
    )

    want_color = None
    if args.color:
        want_color = True
    elif args.no_color:
        want_color = False
    else:
        want_color = (
            sys.stdout.isatty()
            and not args.raw
            and "NO_COLOR" not in __import__("os").environ
        )

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if want_color:
        print(highlight(text))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
