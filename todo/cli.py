"""TODO CLI のコマンドラインインターフェース。

DESIGN.md 2章(コマンド一覧・使用例)・5章(UX上の設計判断)に準拠する。
"""

import argparse
import sys

from todo import storage


def _format_item(item, show_completed_at):
    mark = "x" if item["done"] else " "
    line = "#{} [{}] {}".format(item["id"], mark, item["text"])
    if show_completed_at and item["done"] and item["completed_at"]:
        dt = item["completed_at"]
        # ISO8601 "YYYY-MM-DDTHH:MM:SS..." から MM/DD を取り出す
        month = dt[5:7]
        day = dt[8:10]
        line += " (完了: {}/{})".format(month, day)
    return line


def _cmd_add(args):
    item = storage.add(args.text)
    print("追加しました: #{} {}".format(item["id"], item["text"]))


def _cmd_list(args):
    items = storage.list_items(include_done=args.all)
    if not items:
        print("TODOはありません")
        return
    for item in items:
        print(_format_item(item, show_completed_at=args.all))


def _cmd_done(args):
    item = storage.complete(args.id)
    print("完了にしました: #{} {}".format(item["id"], item["text"]))


def _cmd_rm(args):
    item = storage.remove(args.id)
    print("削除しました: #{} {}".format(item["id"], item["text"]))


def _build_parser():
    parser = argparse.ArgumentParser(prog="todo", description="軽量なTODO管理CLI")
    subparsers = parser.add_subparsers(dest="command")

    add_parser = subparsers.add_parser("add", help="新規TODOを追加する")
    add_parser.add_argument("text", nargs="?", default="")
    add_parser.set_defaults(func=_cmd_add)

    list_parser = subparsers.add_parser("list", aliases=["ls"], help="TODOを一覧表示する")
    list_parser.add_argument("--all", action="store_true")
    list_parser.set_defaults(func=_cmd_list)

    done_parser = subparsers.add_parser("done", help="指定IDのTODOを完了にする")
    done_parser.add_argument("id", type=int)
    done_parser.set_defaults(func=_cmd_done)

    rm_parser = subparsers.add_parser("rm", help="指定IDのTODOを削除する")
    rm_parser.add_argument("id", type=int)
    rm_parser.set_defaults(func=_cmd_rm)

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        args = parser.parse_args(["list"])

    try:
        args.func(args)
    except storage.StorageError as e:
        print(str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
