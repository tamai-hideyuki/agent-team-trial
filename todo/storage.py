"""TODO CLI のデータ永続化層。

保存先ディレクトリ、JSONファイルの読み書き、アトミックな書き込み、
簡易ファイルロック(stale lock対策込み)、ID採番ルールを担当する。
公開関数のインターフェースは DESIGN.md 3章・4章に準拠する。
"""

import json
import os
import time
from datetime import datetime, timezone

DATA_FILENAME = "todo.json"
TMP_FILENAME = "todo.json.tmp"
LOCK_FILENAME = "todo.lock"

STALE_LOCK_SECONDS = 10


class StorageError(Exception):
    """storage モジュールが送出する例外の基底クラス。"""


class LockAcquisitionError(StorageError):
    """ロック取得に失敗した場合に送出する。"""


class CorruptDataError(StorageError):
    """データファイルのJSONパースや構造検証に失敗した場合に送出する。"""


class ItemNotFoundError(StorageError):
    """指定されたIDのTODOが存在しない場合に送出する。"""

    def __init__(self, item_id):
        self.item_id = item_id
        super().__init__("エラー: ID {} は存在しません".format(item_id))


class InvalidTextError(StorageError):
    """TODOの本文が空文字・空白のみの場合に送出する。"""

    def __init__(self):
        super().__init__("エラー: TODOの内容を入力してください")


def get_storage_dir():
    """保存先ディレクトリを返す。

    環境変数 TODO_HOME が設定されていればそれを使い、
    なければ ~/.todo を使う。
    """
    todo_home = os.environ.get("TODO_HOME")
    if todo_home:
        return os.path.abspath(os.path.expanduser(todo_home))
    return os.path.expanduser("~/.todo")


def _paths(storage_dir):
    return {
        "data": os.path.join(storage_dir, DATA_FILENAME),
        "tmp": os.path.join(storage_dir, TMP_FILENAME),
        "lock": os.path.join(storage_dir, LOCK_FILENAME),
    }


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 存在はするが別ユーザー所有などでシグナル送信不可 -> 生存扱い
        return True
    except OSError:
        return False
    return True


def _read_lock_file(lock_path):
    """ロックファイルの内容を (pid, acquired_at) で返す。壊れていれば (None, None)。"""
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return None, None
    lines = content.splitlines()
    if len(lines) < 2:
        return None, None
    try:
        pid = int(lines[0])
        acquired_at = float(lines[1])
    except ValueError:
        return None, None
    return pid, acquired_at


def _is_stale(pid, acquired_at):
    if pid is None:
        return True
    if not _pid_alive(pid):
        return True
    if time.time() - acquired_at > STALE_LOCK_SECONDS:
        return True
    return False


def _acquire_lock(storage_dir):
    """ロックファイルを作成する。取得できなければ LockAcquisitionError を送出する。

    stale なロック(所有プロセスが存在しない、または一定時間経過)は
    破棄したうえで取得を試みる。
    """
    lock_path = _paths(storage_dir)["lock"]
    os.makedirs(storage_dir, exist_ok=True)

    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid, acquired_at = _read_lock_file(lock_path)
            if _is_stale(pid, acquired_at):
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue
            raise LockAcquisitionError(
                "エラー: 他のtodoプロセスが実行中です。しばらくして再試行してください"
            )
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("{}\n{}\n".format(os.getpid(), time.time()))
            return

    raise LockAcquisitionError(
        "エラー: 他のtodoプロセスが実行中です。しばらくして再試行してください"
    )


def _release_lock(storage_dir):
    lock_path = _paths(storage_dir)["lock"]
    pid, _acquired_at = _read_lock_file(lock_path)
    if pid == os.getpid():
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


class _LockGuard:
    def __init__(self, storage_dir):
        self.storage_dir = storage_dir

    def __enter__(self):
        _acquire_lock(self.storage_dir)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _release_lock(self.storage_dir)
        return False


def _default_data():
    return {"next_id": 1, "items": []}


def load(storage_dir=None):
    """保存データを読み込んで dict で返す。

    ファイルが存在しない場合は初期状態 {"next_id": 1, "items": []} を返す
    (ファイルはこの時点では作成しない)。
    JSONのパースに失敗、または想定した構造でない場合は CorruptDataError を送出する。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    data_path = _paths(storage_dir)["data"]

    try:
        with open(data_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return _default_data()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise CorruptDataError(
            "エラー: データファイルが壊れています。手動で確認してください: {}".format(
                data_path
            )
        )

    if (
        not isinstance(data, dict)
        or "next_id" not in data
        or "items" not in data
        or not isinstance(data["next_id"], int)
        or not isinstance(data["items"], list)
    ):
        raise CorruptDataError(
            "エラー: データファイルが壊れています。手動で確認してください: {}".format(
                data_path
            )
        )

    return data


def save(data, storage_dir=None):
    """データをアトミックに書き込む。

    一時ファイル(todo.json.tmp)に全件書き出したのち rename で置き換える。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    os.makedirs(storage_dir, exist_ok=True)
    paths = _paths(storage_dir)

    with open(paths["tmp"], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

    os.replace(paths["tmp"], paths["data"])


def _next_item_id(data):
    max_existing = max((item["id"] for item in data["items"]), default=0)
    return max(data.get("next_id", 1), max_existing + 1)


def add(text, storage_dir=None):
    """新規TODOを追加する。

    引数:
        text: TODOの本文。空文字・空白のみは InvalidTextError。
        storage_dir: 保存先ディレクトリ(省略時は get_storage_dir() を使用)。

    返り値: 追加された item(dict)。

    例外:
        InvalidTextError: text が空文字・空白のみの場合。
        LockAcquisitionError: ロック取得に失敗した場合。
        CorruptDataError: 既存データが壊れている場合。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    if text is None or text.strip() == "":
        raise InvalidTextError()

    with _LockGuard(storage_dir):
        data = load(storage_dir)
        new_id = _next_item_id(data)
        item = {
            "id": new_id,
            "text": text,
            "done": False,
            "created_at": _now_iso(),
            "completed_at": None,
        }
        data["items"].append(item)
        data["next_id"] = new_id + 1
        save(data, storage_dir)

    return item


def list_items(include_done=True, storage_dir=None):
    """保存済みのTODO一覧を id 昇順で返す(読み取り専用、ロック不要)。

    include_done=False の場合は未完了のみを返す。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    data = load(storage_dir)
    items = sorted(data["items"], key=lambda item: item["id"])
    if not include_done:
        items = [item for item in items if not item["done"]]
    return items


def complete(item_id, storage_dir=None):
    """指定IDのTODOを完了にする。

    返り値: 更新後の item(dict)。
    例外: ItemNotFoundError(該当IDが存在しない場合)。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()

    with _LockGuard(storage_dir):
        data = load(storage_dir)
        target = None
        for item in data["items"]:
            if item["id"] == item_id:
                target = item
                break
        if target is None:
            raise ItemNotFoundError(item_id)

        target["done"] = True
        target["completed_at"] = _now_iso()
        save(data, storage_dir)

    return target


def remove(item_id, storage_dir=None):
    """指定IDのTODOを削除する。

    返り値: 削除された item(dict)。
    例外: ItemNotFoundError(該当IDが存在しない場合)。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()

    with _LockGuard(storage_dir):
        data = load(storage_dir)
        target = None
        for item in data["items"]:
            if item["id"] == item_id:
                target = item
                break
        if target is None:
            raise ItemNotFoundError(item_id)

        data["items"] = [item for item in data["items"] if item["id"] != item_id]
        save(data, storage_dir)

    return target
