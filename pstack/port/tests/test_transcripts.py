"""transcripts.py against small synthetic Claude Code and Codex logs in a temporary home."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "transcripts.py")
LONG_RESULT = "r" * 3000
LONG_PROMPT = "p" * 3000


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


class TranscriptsTest(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.project = os.path.join(self.home, "work", "proj")
        os.makedirs(os.path.join(self.project, "sub"))
        slug = re.sub(r"[^A-Za-z0-9]", "-", self.project)
        ts = "2026-09-29T17:22:00.000Z"

        self.claude = os.path.join(self.home, ".claude", "projects", slug, "s1.jsonl")
        write_jsonl(self.claude, [
            {"type": "user", "cwd": self.project, "sessionId": "s1", "timestamp": ts,
             "message": {"role": "user", "content": "Fix the cache<system-reminder>harness noise</system-reminder>"}},
            {"type": "ai-title", "aiTitle": "Fix the stale cache", "sessionId": "s1"},
            {"type": "assistant", "cwd": self.project, "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Looking at cache.py."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "cat cache.py"}}]}},
            {"type": "user", "cwd": self.project, "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": LONG_RESULT}]}},
            {"type": "user", "cwd": self.project, "timestamp": ts, "message": {"role": "user", "content": LONG_PROMPT}},
        ])
        write_jsonl(os.path.join(os.path.dirname(self.claude), "s1", "subagents", "agent-a1.jsonl"), [
            {"type": "user", "cwd": self.project, "timestamp": ts, "message": {"role": "user", "content": "Explore cache.py"}},
        ])

        codex_home = os.path.join(self.home, ".codex")
        self.codex = os.path.join(codex_home, "sessions", "2026", "09", "29", "rollout-2026-09-29T10-00-00-t1.jsonl")
        write_jsonl(self.codex, [
            {"timestamp": ts, "type": "session_meta", "payload": {"id": "t1", "cwd": os.path.join(self.project, "sub")}},
            {"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "<environment_context>\n<cwd>/x</cwd>\n</environment_context>"}]}},
            {"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Review cache.py"}]}},
            {"timestamp": ts, "type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": "{\"cmd\": \"cat cache.py\"}"}},
            {"timestamp": ts, "type": "response_item", "payload": {"type": "function_call_output", "output": "def get_user(): ..."}},
            {"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "update_user leaves a stale entry."}]}},
        ])
        self.codex_sub = os.path.join(os.path.dirname(self.codex), "rollout-2026-09-29T10-01-00-t2.jsonl")
        write_jsonl(self.codex_sub, [{"timestamp": ts, "type": "session_meta", "payload": {"id": "t2", "cwd": self.project}}])

        db = sqlite3.connect(os.path.join(codex_home, "state_5.sqlite"))
        db.execute("create table threads (id text, rollout_path text, updated_at integer, cwd text, title text, "
                   "first_user_message text, source text, agent_role text, archived integer)")
        now = int(time.time())
        db.execute("insert into threads values (?,?,?,?,?,?,?,?,0)", ("t1", self.codex, now, os.path.join(self.project, "sub"), "Review cache.py", "Review cache.py", "cli", None))
        db.execute("insert into threads values (?,?,?,?,?,?,?,?,0)", ("t2", self.codex_sub, now, self.project, "", "", '{"subagent":{"other":"guardian"}}', None))
        db.commit()
        db.close()

        os.utime(self.claude, (now - 60, now - 60))
        os.utime(self.codex, (now, now))
        self.env = {"HOME": self.home, "CODEX_HOME": codex_home, "PATH": os.environ["PATH"]}

    def tearDown(self):
        shutil.rmtree(self.home)

    def run_script(self, *args, env=None):
        proc = subprocess.run(["python3", SCRIPT, *args], capture_output=True, text=True, env={**self.env, **(env or {})})
        return proc.returncode, proc.stdout, proc.stderr

    def listed(self, *args):
        code, out, err = self.run_script("list", "--cwd", self.project, *args)
        self.assertEqual(code, 0, err)
        return [line.split("\t") for line in out.splitlines()]

    def test_list_covers_both_harnesses_newest_first(self):
        rows = self.listed()
        self.assertEqual([(r[1], r[2]) for r in rows], [("codex", "t1"), ("claude", "s1")])
        self.assertEqual(rows[1][4], "Fix the stale cache")

    def test_list_leaves_out_subagents_unless_asked(self):
        ids = {r[2] for r in self.listed("--subagents")}
        self.assertEqual(ids, {"t1", "t2", "s1", "agent-a1"})

    def test_list_filters_by_harness_and_directory(self):
        self.assertEqual([r[2] for r in self.listed("--harness", "claude")], ["s1"])
        code, out, _ = self.run_script("list", "--cwd", os.path.join(self.home, "work", "other"))
        self.assertEqual((code, out), (0, ""))

    def test_dump_claude(self):
        code, out, err = self.run_script("dump", self.claude)
        self.assertEqual(code, 0, err)
        self.assertIn(f"# claude transcript  cwd: {self.project}", out)
        self.assertRegex(out, r"\] USER: Fix the cache\n")
        self.assertNotIn("harness noise", out)
        self.assertIn('TOOL Bash: {"command": "cat cache.py"}', out)
        self.assertIn(f"RESULT: {'r' * 2000} [...1000 more chars]", out)
        self.assertIn(f"USER: {LONG_PROMPT}\n", out)

    def test_dump_limits(self):
        _, no_tools, _ = self.run_script("dump", self.claude, "--no-tools")
        self.assertNotRegex(no_tools, r"\] (TOOL|RESULT)")
        _, unlimited, _ = self.run_script("dump", self.claude, "--max-chars", "0")
        self.assertIn(f"RESULT: {LONG_RESULT}\n", unlimited)

    def test_dump_codex(self):
        code, out, err = self.run_script("dump", self.codex)
        self.assertEqual(code, 0, err)
        self.assertIn(f"# codex transcript  cwd: {self.project}/sub", out)
        self.assertNotIn("environment_context", out)
        self.assertRegex(out, r"\] USER: Review cache\.py\n")
        self.assertIn('TOOL exec_command: {"cmd": "cat cache.py"}', out)
        self.assertIn("RESULT: def get_user(): ...", out)
        self.assertIn("ASSISTANT: update_user leaves a stale entry.", out)

    def test_current(self):
        code, out, _ = self.run_script("current", env={"PSTACK_HARNESS": "claude", "CLAUDE_CODE_SESSION_ID": "s1"})
        self.assertEqual((code, out.strip()), (0, self.claude))
        code, out, _ = self.run_script("current", env={"PSTACK_HARNESS": "codex", "CODEX_THREAD_ID": "t1"})
        self.assertEqual((code, out.strip()), (0, self.codex))
        code, _, err = self.run_script("current", env={"PSTACK_HARNESS": "claude", "CLAUDE_CODE_SESSION_ID": "nope"})
        self.assertEqual(code, 1)
        self.assertIn("no transcript", err)


if __name__ == "__main__":
    unittest.main()
