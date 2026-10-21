"""Durable checkpoints for explicitly registered generation jobs only.

No model calls, legacy-task discovery, or automatic deletion belongs here.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

SCHEMA = 1
MAX_ATTEMPTS = 3
MAX_RESTARTS = 3
_LOCK = threading.RLock()


class RecoveryPaused(RuntimeError):
    pass


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def retryable(error, phase):
    """Retry only known terminal errors. Unknown requests must be reconciled first."""
    message = str(error)
    if isinstance(error, RecoveryPaused) or any(word in message for word in (
            "已取消", "PUBG", "游戏模式", "V41_NO_MODEL", "输入已改变", "余额", "权限", "政策",
            "未知", "生成超时", "prompt_id", "磁盘空间", "No space left", "Disk full")):
        return False
    if phase == "render":
        return any(word in message for word in ("H3 工作流失败", "视频校验失败", "视频文件无效", "视频文件损坏"))
    if phase == "merge":
        return not isinstance(error, PermissionError)
    # Contract errors already received targeted repairs in the director.
    if getattr(error, "code", None):
        return False
    return any(word in message.lower() for word in ("connection refused", "temporarily unavailable", "service unavailable", "503", "429", "未就绪"))


class RecoveryStore:
    def __init__(self, root=None):
        if root is None:
            from . import store
            root = store.DATA / "generation_jobs"
        self.root = Path(root)

    def path(self, sid):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(sid)):
            raise ValueError("无效项目编号")
        return self.root / (str(sid) + ".json")

    def load(self, sid):
        try:
            value = json.loads(self.path(sid).read_text(encoding="utf-8"))
            return value if (isinstance(value, dict) and value.get("schema") == SCHEMA
                             and value.get("sid") == sid and isinstance(value.get("request"), dict)) else None
        except (OSError, ValueError, TypeError):
            return None

    def save(self, record):
        with _LOCK:
            previous = self.load(record["sid"])
            if previous and previous.get('id') == record.get('id') and previous.get('cleared_at') and not record.get('cleared_at'):
                return previous
            if previous and previous.get('id') != record.get('id') and previous.get('created_at', 0) > record.get('created_at', 0):
                return previous
            destination = self.path(record["sid"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".json.tmp")
            record["updated_at"] = time.time()
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        return record

    def register(self, sid, request, input_hash):
        from . import runtime_reset
        runtime_reset.assert_current()
        previous = self.load(sid)
        if previous:
            # Preserve previous records for diagnosis; no task history is removed.
            history = self.root / "history"
            history.mkdir(parents=True, exist_ok=True)
            archive = history / (sid + "_" + str(previous.get("id") or uuid.uuid4().hex) + ".json")
            if not archive.exists():
                archive.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.save({"schema": SCHEMA, "id": uuid.uuid4().hex, "sid": sid,
                          "request": dict(request), "input_hash": input_hash,
                          "runtime_generation": runtime_reset.generation(),
                          "status": "queued", "phase": "text", "attempts": {},
                          "restarts": 0, "external_inflight": "", "cancel": False,
                          "created_at": time.time(), "updated_at": time.time(), "job": {}})

    def candidates(self):
        if not self.root.exists():
            return []
        return [value for path in self.root.glob("*.json")
                for value in [self.load(path.stem)] if value]


def automatic_resume_reason(record, input_hash):
    if record.get("cancel") or record.get("status") == "cancelled":
        return "用户已取消"
    if record.get("status") not in ("queued", "running", "retrying", "waiting_resource", "waiting_task"):
        return "任务不是可自动恢复状态"
    if record.get("input_hash") != input_hash:
        return "输入已改变，请按当前内容重新点击生成；已有成果保留"
    if int(record.get("restarts") or 0) >= MAX_RESTARTS:
        return "自动恢复次数已用完，已有成果保留，可点击继续生成"
    if record.get("external_inflight") and not str(record["external_inflight"]).startswith("video:"):
        return "上次素材请求状态未知，暂停等待核对，避免重复提交"
    return ""
