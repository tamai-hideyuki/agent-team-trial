"""todo CLI (python -m todo ...) のテスト。DESIGN.md 2章・5章に対応する。

実プロセスを subprocess で起動し、TODO_HOME を一時ディレクトリに向けて実行する。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="todo-cli-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_cli(self, *args):
        env = dict(os.environ)
        env["TODO_HOME"] = self.tmpdir
        result = subprocess.run(
            [sys.executable, "-m", "todo"] + list(args),
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result


class AddCommandTest(CliTestCase):
    def test_add_prints_confirmation_and_exit_0(self):
        result = self.run_cli("add", "牛乳を買う")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "追加しました: #1 牛乳を買う")

    def test_add_increments_id_across_calls(self):
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("add", "資料を作る")
        self.assertEqual(result.stdout.strip(), "追加しました: #2 資料を作る")

    def test_add_empty_text_errors_with_exit_1(self):
        result = self.run_cli("add", "")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: TODOの内容を入力してください")

    def test_add_whitespace_only_errors_with_exit_1(self):
        result = self.run_cli("add", "   ")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: TODOの内容を入力してください")

    def test_add_no_argument_errors_with_exit_1(self):
        result = self.run_cli("add")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: TODOの内容を入力してください")


class ListCommandTest(CliTestCase):
    def test_list_empty_shows_no_todos_message(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "TODOはありません")

    def test_no_args_behaves_like_list(self):
        self.run_cli("add", "牛乳を買う")
        result_noargs = self.run_cli()
        result_list = self.run_cli("list")
        self.assertEqual(result_noargs.returncode, 0)
        self.assertEqual(result_noargs.stdout, result_list.stdout)

    def test_no_args_shows_no_todos_message_when_empty(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "TODOはありません")

    def test_ls_alias_matches_list(self):
        self.run_cli("add", "牛乳を買う")
        result_ls = self.run_cli("ls")
        result_list = self.run_cli("list")
        self.assertEqual(result_ls.stdout, result_list.stdout)

    def test_list_shows_added_items_in_order(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        result = self.run_cli("list")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines, ["#1 [ ] 牛乳を買う", "#2 [ ] 資料を作る"])

    def test_list_hides_completed_items_by_default(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        self.run_cli("done", "1")
        result = self.run_cli("list")
        self.assertEqual(result.stdout.strip(), "#2 [ ] 資料を作る")

    def test_list_all_shows_completed_with_mark_and_date(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        self.run_cli("done", "1")
        result = self.run_cli("list", "--all")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(
            lines[0], r"^#1 \[x\] 牛乳を買う \(完了: \d{2}/\d{2}\)$"
        )
        self.assertEqual(lines[1], "#2 [ ] 資料を作る")

    def test_list_without_all_does_not_show_completion_date(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("done", "1")
        result = self.run_cli("list", "--all")
        self.assertNotIn("完了", self.run_cli("list").stdout)
        self.assertIn("完了", result.stdout)


class DoneCommandTest(CliTestCase):
    def test_done_prints_confirmation_and_exit_0(self):
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("done", "1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "完了にしました: #1 牛乳を買う")

    def test_done_missing_id_errors_with_exit_1(self):
        result = self.run_cli("done", "99")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: ID 99 は存在しません")


class RmCommandTest(CliTestCase):
    def test_rm_prints_confirmation_and_exit_0(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        result = self.run_cli("rm", "2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "削除しました: #2 資料を作る")
        remaining = self.run_cli("list")
        self.assertEqual(remaining.stdout.strip(), "#1 [ ] 牛乳を買う")

    def test_rm_missing_id_errors_with_exit_1(self):
        result = self.run_cli("rm", "42")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: ID 42 は存在しません")

    def test_rm_and_done_error_message_format_is_identical(self):
        rm_result = self.run_cli("rm", "42")
        done_result = self.run_cli("done", "42")
        rm_msg = re.sub(r"\d+", "N", rm_result.stdout.strip())
        done_msg = re.sub(r"\d+", "N", done_result.stdout.strip())
        self.assertEqual(rm_msg, done_msg)


class DesignExampleScenarioTest(CliTestCase):
    """DESIGN.md 2章の使用例をそのままなぞるシナリオテスト。"""

    def test_full_scenario_matches_design_doc_example(self):
        r1 = self.run_cli("add", "牛乳を買う")
        self.assertEqual(r1.stdout.strip(), "追加しました: #1 牛乳を買う")

        r2 = self.run_cli("add", "資料を作る")
        self.assertEqual(r2.stdout.strip(), "追加しました: #2 資料を作る")

        r3 = self.run_cli("list")
        self.assertEqual(
            r3.stdout.strip().splitlines(),
            ["#1 [ ] 牛乳を買う", "#2 [ ] 資料を作る"],
        )

        r4 = self.run_cli("done", "1")
        self.assertEqual(r4.stdout.strip(), "完了にしました: #1 牛乳を買う")

        r5 = self.run_cli("list")
        self.assertEqual(r5.stdout.strip(), "#2 [ ] 資料を作る")

        r6 = self.run_cli("list", "--all")
        lines = r6.stdout.strip().splitlines()
        self.assertRegex(lines[0], r"^#1 \[x\] 牛乳を買う \(完了: \d{2}/\d{2}\)$")
        self.assertEqual(lines[1], "#2 [ ] 資料を作る")

        r7 = self.run_cli("rm", "2")
        self.assertEqual(r7.stdout.strip(), "削除しました: #2 資料を作る")

        r8 = self.run_cli()
        self.assertEqual(r8.stdout.strip(), "TODOはありません")


if __name__ == "__main__":
    unittest.main()
