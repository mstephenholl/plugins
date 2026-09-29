#!/usr/bin/env python3
"""Find and read Claude Code and Codex session transcripts for pstack skills.

  transcripts.py current
      Path of the transcript for the session running this command.
  transcripts.py list [--cwd DIR] [--days N] [--harness claude|codex] [--subagents]
      Sessions whose working directory is DIR or below it, newest first, as TSV:
      modified, harness, session id, path, title.
  transcripts.py dump PATH [--no-tools] [--max-chars N]
      One readable line per message, tool call, and tool result. Entries other
      than user turns are cut at N characters (default 2000, 0 for no limit).
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
INJECTED_BLOCK = re.compile(r"^\s*<([a-z_]+)>.*</\1>\s*$", re.S)


def fail(message):
    print(f"transcripts: {message}", file=sys.stderr)
    sys.exit(1)


def harness_of_this_session():
    """The nearest claude or codex ancestor process, since each CLI inherits the other's env vars when nested.

    Codex's sandbox forbids `ps`, and Claude Code under Codex runs unsandboxed, so a failed walk means Codex.
    """
    pid = os.getppid()
    while pid > 1:
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout.split(None, 1)
        except OSError:
            break
        if len(out) < 2:
            break
        name = os.path.basename(out[1].strip())
        if name in ("claude", "codex"):
            return name
        pid = int(out[0])
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude"
    return None


def codex_state_db():
    dbs = glob.glob(os.path.join(CODEX_HOME, "state_*.sqlite"))
    if not dbs:
        return None
    newest = max(dbs, key=lambda p: int(re.search(r"state_(\d+)", p).group(1)))
    return sqlite3.connect(f"file:{newest}?mode=ro", uri=True)


def codex_rollout(thread_id):
    db = codex_state_db()
    if db:
        row = db.execute("select rollout_path from threads where id = ?", (thread_id,)).fetchone()
        if row and os.path.exists(row[0]):
            return row[0]
    found = glob.glob(os.path.join(CODEX_HOME, "sessions", "*", "*", "*", f"rollout-*-{thread_id}.jsonl"))
    return found[0] if found else None


def cmd_current(_args):
    harness = harness_of_this_session()
    if harness == "claude":
        session = os.environ.get("CLAUDE_CODE_SESSION_ID") or fail("CLAUDE_CODE_SESSION_ID is not set")
        found = glob.glob(os.path.join(CLAUDE_PROJECTS, "*", f"{session}.jsonl"))
        print(found[0] if found else fail(f"no transcript for Claude Code session {session}"))
    elif harness == "codex":
        thread = os.environ.get("CODEX_THREAD_ID") or fail("CODEX_THREAD_ID is not set")
        print(codex_rollout(thread) or fail(f"no transcript for Codex thread {thread}"))
    else:
        fail("not running inside a Claude Code or Codex session")


def under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def claude_session_info(path):
    cwd, title, first_prompt = None, None, None
    with open(path, errors="replace") as f:
        for line in f:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            cwd = cwd or record.get("cwd")
            if record.get("type") == "ai-title":
                title = record.get("aiTitle")
            if first_prompt is None and record.get("type") == "user":
                content = (record.get("message") or {}).get("content")
                if isinstance(content, str):
                    first_prompt = content
    return cwd, title or first_prompt or ""


def list_claude(root, since, subagents):
    slugs = tuple(re.sub(r"[^A-Za-z0-9]", "-", p.rstrip("/")) for p in {root, os.path.realpath(root)})
    dirs = [d for d in glob.glob(os.path.join(CLAUDE_PROJECTS, "*")) if os.path.basename(d).startswith(slugs)]
    if not dirs:
        dirs = glob.glob(os.path.join(CLAUDE_PROJECTS, "*"))
    rows = []
    for d in dirs:
        files = glob.glob(os.path.join(d, "*.jsonl"))
        if subagents:
            files += glob.glob(os.path.join(d, "*", "subagents", "*.jsonl"))
        for path in files:
            mtime = os.path.getmtime(path)
            if mtime < since:
                continue
            cwd, title = claude_session_info(path)
            if cwd and not under(os.path.realpath(cwd), os.path.realpath(root)):
                continue
            rows.append((mtime, "claude", os.path.basename(path)[:-6], path, title))
    return rows


def list_codex(root, since, subagents):
    db = codex_state_db()
    if not db:
        return []
    rows = []
    query = """select id, rollout_path, updated_at, cwd, title, first_user_message, source, agent_role
               from threads where archived = 0 and updated_at >= ?"""
    for tid, path, updated, cwd, title, first, source, role in db.execute(query, (int(since),)):
        if not under(os.path.realpath(cwd), os.path.realpath(root)) or not os.path.exists(path):
            continue
        if not subagents and (role or source.startswith("{")):
            continue
        rows.append((os.path.getmtime(path), "codex", tid, path, title or first or ""))
    return rows


def cmd_list(args):
    root = os.path.abspath(args.cwd)
    since = time.time() - args.days * 86400
    rows = []
    if args.harness in (None, "claude"):
        rows += list_claude(root, since, args.subagents)
    if args.harness in (None, "codex"):
        rows += list_codex(root, since, args.subagents)
    for mtime, harness, sid, path, title in sorted(rows, reverse=True):
        title = " ".join(title.split())[:100]
        print(f"{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}\t{harness}\t{sid}\t{path}\t{title}")


def clip(text, limit):
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f" [...{len(text) - limit} more chars]"


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def stamp(ts):
    return ts[5:16].replace("T", " ") if isinstance(ts, str) else "           "


def claude_entries(record):
    ts = stamp(record.get("timestamp"))
    message = record.get("message") or {}
    content = message.get("content")
    if record.get("type") == "user":
        if isinstance(content, str):
            yield ts, "USER", content
            return
        for part in content or []:
            if part.get("type") == "text":
                yield ts, "USER", part.get("text", "")
            elif part.get("type") == "tool_result":
                yield ts, "RESULT", text_of(part.get("content"))
    elif record.get("type") == "assistant":
        for part in content or []:
            if part.get("type") == "text":
                yield ts, "ASSISTANT", part.get("text", "")
            elif part.get("type") == "tool_use":
                yield ts, f"TOOL {part.get('name')}", json.dumps(part.get("input"), ensure_ascii=False)


def codex_entries(record):
    ts = stamp(record.get("timestamp"))
    if record.get("type") != "response_item":
        return
    item = record.get("payload") or {}
    kind = item.get("type")
    if kind == "message" and item.get("role") in ("user", "assistant"):
        role = item["role"].upper()
        for part in item.get("content") or []:
            text = part.get("text", "")
            if role == "USER" and INJECTED_BLOCK.match(text):
                continue
            yield ts, role, text
    elif kind in ("function_call", "custom_tool_call", "local_shell_call"):
        args = item.get("arguments") or item.get("input") or json.dumps(item.get("action"))
        yield ts, f"TOOL {item.get('name', 'shell')}", args if isinstance(args, str) else json.dumps(args)
    elif kind in ("function_call_output", "custom_tool_call_output"):
        output = item.get("output")
        yield ts, "RESULT", output if isinstance(output, str) else text_of(output)


def cursor_entries(record):
    role = str(record.get("role", "")).upper()
    content = (record.get("message") or {}).get("content")
    if role in ("USER", "ASSISTANT"):
        yield "           ", role, text_of(content)


def cmd_dump(args):
    if not os.path.exists(args.path):
        fail(f"no such transcript: {args.path}")
    entries = None
    header = {"path": args.path}
    with open(args.path, errors="replace") as f:
        for line in f:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get("type")
            if entries is None:
                if kind in ("session_meta", "response_item", "event_msg", "turn_context"):
                    entries, header["harness"] = codex_entries, "codex"
                elif kind in ("user", "assistant") or "sessionId" in record:
                    entries, header["harness"] = claude_entries, "claude"
                elif "role" in record:
                    entries, header["harness"] = cursor_entries, "cursor"
                else:
                    continue
            if kind == "session_meta":
                header["cwd"] = (record.get("payload") or {}).get("cwd")
            elif "cwd" not in header and record.get("cwd"):
                header["cwd"] = record["cwd"]
            if header.get("cwd") and not header.get("printed"):
                print(f"# {header['harness']} transcript  cwd: {header['cwd']}  path: {args.path}")
                header["printed"] = True
            for ts, label, text in entries(record):
                tool = label.startswith(("TOOL", "RESULT"))
                if tool and args.no_tools:
                    continue
                text = SYSTEM_REMINDER.sub("", text)
                if text.strip():
                    limit = 0 if label == "USER" else args.max_chars
                    print(f"[{ts}] {label}: {clip(text, limit)}")
    if entries is None:
        fail(f"unrecognized transcript format: {args.path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("current").set_defaults(run=cmd_current)
    listing = sub.add_parser("list")
    listing.add_argument("--cwd", default=os.getcwd())
    listing.add_argument("--days", type=float, default=7)
    listing.add_argument("--harness", choices=["claude", "codex"])
    listing.add_argument("--subagents", action="store_true")
    listing.set_defaults(run=cmd_list)
    dump = sub.add_parser("dump")
    dump.add_argument("path")
    dump.add_argument("--no-tools", action="store_true")
    dump.add_argument("--max-chars", type=int, default=2000)
    dump.set_defaults(run=cmd_dump)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
