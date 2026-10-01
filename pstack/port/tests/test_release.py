"""release.py: version planning from merged pull requests, release notes, and a release cut from a real git history."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

PORT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PORT))
SCRIPT = os.path.join(PORT, "release.py")
CI = os.path.join(ROOT, ".github", "workflows", "pstack-ci.yml")
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

Version, Level, Commit, Change, Release = release.Version, release.Level, release.Commit, release.Change, release.Release
SHA = "a" * 40


def commit(subject, body="", sha=SHA):
    return Commit(sha, subject, body)


def change(sha=SHA, pr=None, type=None, scope=None, description="", breaking=False, release_as=()):
    return Change(sha, pr, type, scope, description, breaking, release_as)


class VersionTest(unittest.TestCase):
    def test_parse_accepts_plain_triples_with_or_without_v(self):
        self.assertEqual(Version.parse("0.4.2"), Version(0, 4, 2))
        self.assertEqual(Version.parse("v10.0.31"), Version(10, 0, 31))

    def test_parse_rejects_everything_else(self):
        for text in ["v1.0.0-rc.1", "1.0.0+build", "v2", "v1.2", "vfoo", "v1.2.3.4", "v01.2.3", " v1.2.3", "v1.2.3\n", ""]:
            with self.assertRaises(ValueError, msg=text):
                Version.parse(text)

    def test_tag(self):
        self.assertEqual(Version(1, 2, 3).tag, "v1.2.3")

    def test_bump_resets_what_is_below_the_level(self):
        v = Version(1, 2, 3)
        self.assertEqual(v.bump(Level.PATCH), Version(1, 2, 4))
        self.assertEqual(v.bump(Level.MINOR), Version(1, 3, 0))
        self.assertEqual(v.bump(Level.MAJOR), Version(2, 0, 0))

    def test_successors_are_the_next_patch_minor_and_major(self):
        self.assertEqual(Version(0, 4, 2).successors(), (Version(0, 4, 3), Version(0, 5, 0), Version(1, 0, 0)))

    def test_versions_sort_numerically(self):
        self.assertGreater(Version(0, 10, 0), Version(0, 9, 9))


class ParseChangeTest(unittest.TestCase):
    def test_a_legacy_merge_commit_takes_its_title_from_the_body(self):
        got = release.parse_change(commit(
            "Merge pull request #2 from mstephenholl/fix/single-harness-install",
            "fix(pstack): install for the harness CLIs on PATH by default\n",
        ))
        self.assertEqual(got, change(pr=2, type="fix", scope="pstack", description="install for the harness CLIs on PATH by default"))

    def test_a_legacy_merge_commit_keeps_the_rest_of_its_body_for_directives(self):
        got = release.parse_change(commit(
            "Merge pull request #9 from o/b",
            "\nfeat: add a thing\n\nReleases the thing.\n\nRelease-As: 1.0.0\n",
        ))
        self.assertEqual(got, change(pr=9, type="feat", description="add a thing", release_as=("1.0.0",)))

    def test_a_legacy_merge_commit_with_no_body_is_titled_by_its_subject(self):
        got = release.parse_change(commit("Merge pull request #4 from o/b", ""))
        self.assertEqual(got, change(pr=4, description="Merge pull request #4 from o/b"))

    def test_a_pr_title_commit_is_conventional_with_the_pr_number_stripped(self):
        got = release.parse_change(commit("feat(cli)!: drop the old flag (#7)"))
        self.assertEqual(got, change(pr=7, type="feat", scope="cli", description="drop the old flag", breaking=True))

    def test_the_type_is_lowercased(self):
        self.assertEqual(release.parse_change(commit("Fix(Docs): typo (#3)")), change(pr=3, type="fix", scope="Docs", description="typo"))

    def test_a_plain_title_has_no_type_and_no_pr(self):
        got = release.parse_change(commit("Keep pstack update from moving a clone backwards"))
        self.assertEqual(got, change(description="Keep pstack update from moving a clone backwards"))

    def test_a_sync_title_is_plain(self):
        got = release.parse_change(commit("Merge upstream cursor/plugins (#5)", "Upstream: cursor/plugins@0123456789ab\n"))
        self.assertEqual(got, change(pr=5, description="Merge upstream cursor/plugins"))

    def test_a_breaking_change_footer_marks_the_change(self):
        got = release.parse_change(commit("refactor(cli): rename the command (#8)", "Renames it.\n\nBREAKING CHANGE: pstack now starts as pst.\n"))
        self.assertEqual(got, change(pr=8, type="refactor", scope="cli", description="rename the command", breaking=True))

    def test_the_hyphenated_breaking_footer_counts_too(self):
        got = release.parse_change(commit("fix: x (#8)", "BREAKING-CHANGE: yes\n"))
        self.assertTrue(got.breaking)

    def test_directives_inside_fenced_code_are_ignored(self):
        body = "Documents the footers.\n\n```\nBREAKING CHANGE: example\nRelease-As: 9.9.9\n```\n\n~~~md\nRelease-As: 8.8.8\n~~~\n\nRelease-As: 1.0.0\n"
        got = release.parse_change(commit("docs: describe the footers (#6)", body))
        self.assertEqual(got, change(pr=6, type="docs", description="describe the footers", release_as=("1.0.0",)))

    def test_a_directive_after_an_unterminated_fence_is_ignored(self):
        got = release.parse_change(commit("docs: x (#6)", "```\nBREAKING CHANGE: example\n"))
        self.assertFalse(got.breaking)

    def test_a_directive_must_start_its_line(self):
        got = release.parse_change(commit("fix: x (#6)", "Do not write BREAKING CHANGE: in prose.\nSee Release-As: 2.0.0 in the docs.\n"))
        self.assertEqual((got.breaking, got.release_as), (False, ()))


class PlanReleaseTest(unittest.TestCase):
    def plan(self, previous, *titles):
        commits = [commit(title, sha=f"{i:040x}") for i, title in enumerate(titles, 1)]
        return release.plan_release(previous, commits[0].sha, commits)

    def test_a_plain_title_is_a_patch(self):
        got = self.plan(Version(0, 4, 2), "Keep pstack update from moving a clone backwards")
        self.assertEqual((got.version, got.previous), (Version(0, 4, 3), Version(0, 4, 2)))

    def test_a_sync_title_is_a_patch(self):
        self.assertEqual(self.plan(Version(0, 4, 2), "Merge upstream cursor/plugins (#5)").version, Version(0, 4, 3))

    def test_a_fix_is_a_patch_and_a_feat_is_a_minor(self):
        self.assertEqual(self.plan(Version(1, 4, 2), "fix(x): y (#1)").version, Version(1, 4, 3))
        self.assertEqual(self.plan(Version(1, 4, 2), "fix(x): y (#1)", "feat(x): z (#2)").version, Version(1, 5, 0))

    def test_breaking_is_a_minor_before_1_0_and_a_major_after(self):
        self.assertEqual(self.plan(Version(0, 4, 2), "feat!: x (#1)").version, Version(0, 5, 0))
        self.assertEqual(self.plan(Version(1, 4, 2), "feat!: x (#1)").version, Version(2, 0, 0))

    def test_the_release_covers_every_change_newest_first(self):
        got = self.plan(Version(0, 1, 0), "fix: newer (#4)", "feat: older (#3)")
        self.assertEqual([c.pr for c in got.changes], [4, 3])
        self.assertEqual(got.target, f"{1:040x}")

    def test_release_as_names_the_next_minor_or_major(self):
        body = "Release-As: 1.0.0\n"
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#1)", body)])
        self.assertEqual((got.version, got.warnings), (Version(1, 0, 0), ()))

    def test_a_pr_may_lower_its_own_level_with_release_as(self):
        for title in ("feat: x (#1)", "feat!: x (#1)"):
            with self.subTest(title):
                got = release.plan_release(Version(0, 4, 2), SHA, [commit(title, "Release-As: v0.4.3\n")])
                self.assertEqual((got.version, got.warnings), (Version(0, 4, 3), ()))

    def test_release_as_sets_only_its_own_prs_level(self):
        commits = [commit("feat: a (#1)", sha="1" * 40), commit("fix: b (#2)", "Release-As: 0.4.3\n", sha="2" * 40)]
        got = release.plan_release(Version(0, 4, 2), "1" * 40, commits)
        self.assertEqual((got.version, got.warnings), (Version(0, 5, 0), ()))

    def test_release_as_does_not_hide_a_breaking_change_in_another_pr(self):
        commits = [commit("feat!: drop flag (#3)", sha="1" * 40), commit("docs: typo (#4)", "Release-As: 1.4.3\n", sha="2" * 40)]
        self.assertEqual(release.plan_release(Version(1, 4, 2), "1" * 40, commits).version, Version(2, 0, 0))

    def test_an_explicit_major_is_not_clamped_before_1_0(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("docs: x (#1)", "Release-As: 1.0.0\n")])
        self.assertEqual((got.version, got.warnings), (Version(1, 0, 0), ()))

    def test_the_highest_release_as_within_one_pr_sets_its_level(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#1)", "Release-As: 0.5.0\nRelease-As: 0.4.3\n")])
        self.assertEqual(got.version, Version(0, 5, 0))

    def test_a_release_as_with_trailing_words_is_ignored_and_the_pr_keeps_its_kind_level(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("feat: x (#7)", "Release-As: 1.0.0 (first stable)\n")])
        self.assertEqual(got.version, Version(0, 5, 0))
        self.assertEqual(got.warnings, ("#7: ignored Release-As 1.0.0 (first stable), which is not one of v0.4.3, v0.5.0, v1.0.0",))

    def test_a_release_as_that_is_not_the_next_version_is_ignored_with_a_warning(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#7)", "Release-As: 10.0.0\n")])
        self.assertEqual(got.version, Version(0, 4, 3))
        self.assertEqual(got.warnings, ("#7: ignored Release-As 10.0.0, which is not one of v0.4.3, v0.5.0, v1.0.0",))

    def test_a_release_as_that_does_not_parse_is_ignored_with_a_warning(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#7)", "Release-As: soon\n")])
        self.assertEqual((got.version, len(got.warnings)), (Version(0, 4, 3), 1))

    def test_a_warning_without_a_pr_names_the_commit(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("Direct push", "Release-As: 3.0.0\n")])
        self.assertTrue(got.warnings[0].startswith("aaaaaaaaaaaa: ignored Release-As 3.0.0"), got.warnings)

    def test_the_highest_valid_release_as_wins(self):
        commits = [commit("fix: a (#2)", "Release-As: 0.5.0\n", sha="1" * 40), commit("fix: b (#1)", "Release-As: 1.0.0\n", sha="2" * 40)]
        self.assertEqual(release.plan_release(Version(0, 4, 2), "1" * 40, commits).version, Version(1, 0, 0))

    def test_an_empty_range_is_nothing_to_release(self):
        self.assertIsNone(release.plan_release(Version(0, 4, 2), SHA, []))


class NotesTest(unittest.TestCase):
    def test_sections_lines_and_footer(self):
        got = release.notes(Release(
            version=Version(0, 2, 0),
            previous=Version(0, 1, 0),
            target="d" * 40,
            changes=(
                change(pr=7, type="feat", scope="cli", description="drop the old flag", breaking=True),
                change(pr=6, type="feat", scope="setup", description="add a models line"),
                change(pr=2, type="fix", scope="pstack", description="install for the harness CLIs on PATH by default"),
                change(pr=5, description="Merge upstream cursor/plugins"),
                change(description="Keep pstack update from moving a clone backwards"),
                change(pr=9, type="docs", description="explain releases"),
            ),
            warnings=("#7: ignored Release-As 10.0.0",),
        ), "mstephenholl/pstack-claude-codex")
        self.assertEqual(got, (
            "## Breaking changes\n\n"
            "- **cli:** drop the old flag (#7)\n\n"
            "## Features\n\n"
            "- **setup:** add a models line (#6)\n\n"
            "## Fixes\n\n"
            "- **pstack:** install for the harness CLIs on PATH by default (#2)\n\n"
            "## Other changes\n\n"
            "- Merge upstream cursor/plugins (#5)\n"
            "- Keep pstack update from moving a clone backwards\n"
            "- explain releases (#9)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/mstephenholl/pstack-claude-codex/compare/v0.1.0...v0.2.0"
        ))

    def test_empty_sections_are_omitted(self):
        got = release.notes(Release(Version(0, 1, 1), Version(0, 1, 0), SHA, (change(pr=4, type="fix", scope="pstack", description="y"),), ()), "o/r")
        self.assertEqual(got, "## Fixes\n\n- **pstack:** y (#4)\n\nUpdate with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.1.1")


    def test_sections_follow_the_table_whatever_the_order_of_the_changes(self):
        got = release.notes(Release(
            version=Version(0, 2, 0),
            previous=Version(0, 1, 0),
            target=SHA,
            changes=(
                change(pr=4, description="plain"),
                change(pr=3, type="fix", description="repair"),
                change(pr=2, type="feat", description="add"),
                change(pr=1, type="fix", description="drop", breaking=True),
            ),
            warnings=(),
        ), "o/r")
        self.assertEqual(got, (
            "## Breaking changes\n\n- drop (#1)\n\n"
            "## Features\n\n- add (#2)\n\n"
            "## Fixes\n\n- repair (#3)\n\n"
            "## Other changes\n\n- plain (#4)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.2.0"
        ))


class GitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        self.bin = os.path.join(self.tmp, "bin")
        self.gh_dir = os.path.join(self.tmp, "gh")
        for d in (self.repo, self.bin, self.gh_dir):
            os.makedirs(d)
        patcher = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": self.bin + os.pathsep + os.environ["PATH"],
            "FAKE_GH_DIR": self.gh_dir,
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("GITHUB_REPOSITORY", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS", "FAKE_GH_EXIT"):
            os.environ.pop(name, None)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

        self.git("init", "-q", "-b", "main")
        self.commit("initial")
        self.git("tag", "-a", "v0.1.0", "-m", "v0.1.0")
        self.tagged = self.head()
        self.git("checkout", "-q", "-b", "upstream")
        self.commit("feat(x): upstream feature")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge upstream cursor/plugins (#3)", "upstream")
        self.commit("fix(pstack): y (#4)")

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def install_gh(self, script):
        path = os.path.join(self.bin, "gh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def gh_calls(self):
        path = os.path.join(self.gh_dir, "argv")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            argv = f.read().splitlines()
        with open(os.path.join(self.gh_dir, "stdin")) as f:
            return argv, f.read()


class PreviewTest(GitCase):
    def test_a_preview_reads_first_parent_history_only(self):
        sha = self.head()
        code, out, err = self.run_main("--repo", "o/r")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, (
            f"would release v0.1.1 at {sha[:12]} after v0.1.0 (2 changes)\n\n"
            "## Fixes\n\n"
            "- **pstack:** y (#4)\n\n"
            "## Other changes\n\n"
            "- Merge upstream cursor/plugins (#3)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.1.1\n"
        ))

    def test_a_preview_needs_no_repository(self):
        code, out, err = self.run_main()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("would release v0.1.1 at "), out)
        self.assertIn("https://github.com/OWNER/REPO/compare/v0.1.0...v0.1.1", out)

    def test_a_preview_never_calls_gh(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        code, out, _ = self.run_main("--repo", "o/r")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("would release"), out)
        self.assertIsNone(self.gh_calls())

    def test_the_repository_defaults_to_github_repository(self):
        os.environ["GITHUB_REPOSITORY"] = "env/repo"
        _, out, _ = self.run_main()
        self.assertIn("https://github.com/env/repo/compare/v0.1.0...v0.1.1", out)

    def test_a_breaking_pr_takes_the_version_to_0_2_0(self):
        self.commit("feat(cli)!: drop the old flag (#5)")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertTrue(out.startswith("would release v0.2.0 "), out)
        self.assertIn("## Breaking changes\n\n- **cli:** drop the old flag (#5)\n", out)

    def test_target_selects_an_older_commit(self):
        merge = self.git("rev-parse", "HEAD~1")
        _, out, _ = self.run_main("--repo", "o/r", "--target", merge)
        self.assertTrue(out.startswith(f"would release v0.1.1 at {merge[:12]} after v0.1.0 (1 change)\n"), out)

    def test_an_ignored_release_as_is_reported_but_stays_out_of_the_notes(self):
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        lines = out.splitlines()
        self.assertEqual(lines[1], "warning: #6: ignored Release-As 10.0.0, which is not one of v0.1.1, v0.2.0, v1.0.0")
        self.assertNotIn("Release-As", "\n".join(lines[2:]))

    def test_warnings_become_annotations_on_github_actions(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertIn("::warning title=release.py::#6: ignored Release-As 10.0.0, which is not one of v0.1.1, v0.2.0, v1.0.0\n", out)

    def test_no_annotation_is_printed_elsewhere(self):
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertNotIn("::warning", out)

    def test_the_decision_and_notes_go_to_the_step_summary_without_annotations(self):
        summary = os.path.join(self.tmp, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = summary
        os.environ["GITHUB_ACTIONS"] = "true"
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        with open(summary) as f:
            written = f.read()
        self.assertEqual(written, "".join(line for line in out.splitlines(keepends=True) if not line.startswith("::warning")))
        self.assertIn("warning: #6: ignored Release-As 10.0.0", written)

    def test_at_the_tagged_commit_there_is_nothing_to_release(self):
        code, out, err = self.run_main("--repo", "o/r", "--target", self.tagged)
        self.assertEqual((code, out, err), (0, f"nothing to release: {self.tagged[:12]} is covered by v0.1.0\n", ""))

    def test_a_target_below_the_tag_is_covered_too(self):
        self.commit("later")
        self.git("tag", "-a", "v0.1.1", "-m", "v0.1.1")
        code, out, _ = self.run_main("--target", "HEAD~1")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^nothing to release: [0-9a-f]{12} is covered by v0\.1\.1\n$")

    def test_the_newest_tag_decides_the_base(self):
        self.git("tag", "-a", "v0.9.0", "-m", "v0.9.0", "HEAD~1")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertRegex(out, r"^would release v0\.9\.1 at [0-9a-f]{12} after v0\.9\.0 \(1 change\)\n")

    def test_a_tag_that_is_not_plain_is_skipped_with_a_warning(self):
        for name in ("v2", "v0.9", "v1.0.0-rc.1"):
            with self.subTest(name):
                self.git("tag", name)
                code, out, err = self.run_main("--repo", "o/r")
                self.git("tag", "-d", name)
                self.assertEqual((code, err), (0, ""))
                lines = out.splitlines()
                self.assertRegex(lines[0], r"^would release v0\.1\.1 at [0-9a-f]{12} after v0\.1\.0 \(2 changes\)$")
                self.assertEqual(lines[1], f"warning: skipped tag {name}, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.")

    def test_a_skipped_tag_is_reported_when_there_is_nothing_to_release_too(self):
        self.git("tag", "v2")
        code, out, _ = self.run_main("--target", self.tagged)
        self.assertEqual(code, 0)
        self.assertEqual(out, (
            f"nothing to release: {self.tagged[:12]} is covered by v0.1.0\n"
            "warning: skipped tag v2, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.\n"
        ))

    def test_with_no_plain_tag_there_is_nothing_to_release_after(self):
        self.git("tag", "-d", "v0.1.0")
        self.git("tag", "v2")
        code, out, err = self.run_main()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no plain vX.Y.Z tag", err)

    def test_no_tag_fails(self):
        self.git("tag", "-d", "v0.1.0")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("no plain vX.Y.Z tag", err)

    def test_a_newest_tag_off_the_targets_history_fails_with_the_recovery_pointer(self):
        self.git("checkout", "-q", "--orphan", "elsewhere")
        self.commit("elsewhere")
        self.git("tag", "-a", "v0.3.0", "-m", "v0.3.0")
        self.git("checkout", "-q", "main")
        code, out, err = self.run_main()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("v0.3.0", err)
        self.assertIn('"Repository settings" in pstack/port/README.md', err)

    def test_an_unknown_target_fails(self):
        code, _, err = self.run_main("--target", "no-such-ref")
        self.assertEqual(code, 1)
        self.assertIn("no-such-ref", err)


class PublishTest(GitCase):
    def test_publish_creates_the_tag_and_release_in_one_call(self):
        self.install_gh('printf \'%s\\n\' "$@" > "$FAKE_GH_DIR/argv"\ncat > "$FAKE_GH_DIR/stdin"\n')
        sha = self.head()
        code, out, err = self.run_main("--publish", "--repo", "o/r")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith(f"released v0.1.1 at {sha[:12]} after v0.1.0 (2 changes)\n"), out)
        argv, stdin = self.gh_calls()
        self.assertEqual(argv, ["api", "-X", "POST", "repos/o/r/releases", "--input", "-"])
        payload = json.loads(stdin)
        self.assertEqual(payload, {
            "tag_name": "v0.1.1",
            "target_commitish": sha,
            "name": "pstack v0.1.1",
            "body": (
                "## Fixes\n\n- **pstack:** y (#4)\n\n"
                "## Other changes\n\n- Merge upstream cursor/plugins (#3)\n\n"
                "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.1.1"
            ),
            "make_latest": "true",
        })

    def test_publish_needs_a_repository(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        code, out, err = self.run_main("--publish")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--repo", err)
        self.assertIsNone(self.gh_calls())

    def test_publish_takes_the_repository_from_github_repository(self):
        self.install_gh('printf \'%s\\n\' "$@" > "$FAKE_GH_DIR/argv"\ncat > "$FAKE_GH_DIR/stdin"\n')
        os.environ["GITHUB_REPOSITORY"] = "env/repo"
        code, _, _ = self.run_main("--publish")
        self.assertEqual(code, 0)
        self.assertIn("repos/env/repo/releases", self.gh_calls()[0])

    def test_nothing_to_release_never_calls_gh(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        code, out, _ = self.run_main("--publish", "--repo", "o/r", "--target", self.tagged)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("nothing to release"), out)
        self.assertIsNone(self.gh_calls())

    def test_a_failed_publish_exits_1_and_claims_no_release(self):
        self.install_gh('cat > /dev/null\necho "HTTP 422: tag exists" >&2\nexit 1\n')
        code, out, err = self.run_main("--publish", "--repo", "o/r")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("HTTP 422: tag exists", err)

    def test_a_missing_gh_exits_1(self):
        only_git = os.path.join(self.tmp, "only-git")
        os.makedirs(only_git)
        os.symlink(shutil.which("git"), os.path.join(only_git, "git"))
        with mock.patch.dict(os.environ, {"PATH": only_git}):
            code, _, err = self.run_main("--publish", "--repo", "o/r")
        self.assertEqual(code, 1)
        self.assertIn("gh", err)


def ci_jobs():
    with open(CI) as f:
        body = f.read().split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):[ \t]*$", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


class CiWiringTest(unittest.TestCase):
    def test_the_gate_needs_every_other_job(self):
        jobs = ci_jobs()
        needs = re.search(r"^    needs: \[([^\]]*)\]", jobs["gate"], re.M).group(1)
        self.assertEqual(set(re.split(r"\s*,\s*", needs)), set(jobs) - {"gate", "release"})

    def test_the_gate_runs_even_when_a_job_it_needs_fails(self):
        self.assertRegex(ci_jobs()["gate"], re.compile(r"^    if: always\(\)$", re.M))

    def test_the_release_waits_for_the_gate_and_runs_only_on_main(self):
        job = ci_jobs()["release"]
        self.assertRegex(job, re.compile(r"^    needs: gate$", re.M))
        self.assertRegex(job, re.compile(r"^    if: github\.ref == 'refs/heads/main'$", re.M))

    def test_the_release_job_alone_can_write_contents(self):
        jobs = ci_jobs()
        self.assertRegex(jobs["release"], re.compile(r"^    permissions:\n      contents: write$", re.M))
        self.assertEqual([name for name, job in jobs.items() if "contents: write" in job], ["release"])

    def test_overlapping_releases_queue_instead_of_cancelling(self):
        self.assertRegex(
            ci_jobs()["release"],
            re.compile(r"^    concurrency:\n      group: pstack-release\n      queue: max$", re.M),
            "the release job's concurrency needs `queue: max`, so every merge releases in turn. "
            "The local actionlint rejects the key, but GitHub documents it, so do not delete it to quiet the linter",
        )

    def test_the_release_checks_out_the_full_history_without_keeping_credentials(self):
        job = ci_jobs()["release"]
        self.assertRegex(job, re.compile(r"^          fetch-depth: 0$", re.M))
        self.assertRegex(job, re.compile(r"^          persist-credentials: false$", re.M))

    def test_the_release_publishes_and_cannot_hang(self):
        job = ci_jobs()["release"]
        self.assertRegex(job, re.compile(r"^        run: .*\brelease\.py --publish\b", re.M))
        self.assertRegex(job, re.compile(r"^    timeout-minutes: [1-9][0-9]*$", re.M))


if __name__ == "__main__":
    unittest.main()
