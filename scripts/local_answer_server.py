#!/usr/bin/env python3
"""Serve the private HTML archive on loopback and confirm edited answers locally."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import html
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from apply_review_actions import apply_actions
from check_data_compatibility import require_store_compatible
from common import append_private_text, read_json, write_json
from record_practice_attempt import record_attempt
from render_interview_html import (
    ANSWERS_HTML,
    CASE_HTML,
    HISTORY_HTML,
    PENDING_REVIEW_HTML,
    PREPARATIONS_HTML,
    QUESTION_BANK_HTML,
    answer_fingerprint,
)


ALLOWED_ROOT_PAGES = {
    HISTORY_HTML, QUESTION_BANK_HTML, ANSWERS_HTML, PENDING_REVIEW_HTML, PREPARATIONS_HTML,
}
PRACTICE_ENDPOINT = "/api/practice-attempts/record"
MAX_REQUEST_BYTES = 512 * 1024
REVIEW_ACTIONS_ENDPOINT = "/api/review-actions/apply"
RELATION_REMOVE_ENDPOINT = "/api/question-relations/remove"
CLUSTER_ADD_ENDPOINT = "/api/question-clusters/add"
INSTANCE_LOCK_FILENAME = "local-answer-server.lock"
INSTANCE_STATE_FILENAME = "local-answer-server.json"


def session_cookie_name_for_port(port: int) -> str:
    return f"interview_session_{port}"


def public_error(error: Exception) -> str:
    """Return actionable Chinese errors without exposing Python details or local paths."""
    message = str(error).strip()
    safe_messages = {
        "答案版本或内容缺失",
        "请先核对并确认个人事实",
        "答案长度超过上限",
        "答案 ID 缺失",
        "场次或题目缺失",
        "本场面试不存在，请刷新页面",
        "本场题目不存在，请刷新页面",
        "答案已被修改，请刷新页面后核对新版本",
        "关联已变化，请刷新页面后重试",
        "移出后题组必须至少保留两道跨场原题",
        "跨场答案至少引用一道真实问题",
        "请至少选择 1 道未归组原题",
        "题目已不存在或页面已过期，请刷新页面重试",
        "题组关系保存失败，可能已部分写入；请刷新页面核对",
        "这份答案已不存在或不能确认，请刷新页面",
        "答案内容过长或为空",
        "请求格式错误",
        "只接受 JSON 请求",
        "练习回答不能为空",
        "练习回答超过 100000 字符",
        "练习时长须在 1–3600 秒之间",
        "自评分须在 1–5 之间",
        "练习记录编号无效，请刷新页面后重试",
        "练习记录编号已存在但内容不同，请刷新页面后重试",
        "练习记录正文缺失，请重新保存本次练习",
        "练习记录正文已损坏，请重新保存本次练习",
        "审核结果应用失败，可能已部分写入；请核对档案后刷新页面",
    }
    if message in safe_messages:
        return message
    if message.startswith("找不到真实问题："):
        return "题目已不存在或页面已过期，请刷新页面后重试"
    return "本地保存失败，请检查档案状态后刷新页面重试"


class ServerInstanceLock:
    """Coordinate one local server per archive root with a held OS advisory lock."""

    def __init__(self, root: Path, *, wait_seconds: float = 5.0):
        self.root = root.resolve()
        self.directory = self.root / "logs"
        self.lock_path = self.directory / INSTANCE_LOCK_FILENAME
        self.state_path = self.directory / INSTANCE_STATE_FILENAME
        self.wait_seconds = wait_seconds
        self.owner = False
        self._descriptor: int | None = None

    @staticmethod
    def _read_private_json(path: Path) -> dict[str, object] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _open_lock(self) -> int:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.chmod(self.lock_path, 0o600)
        return descriptor

    @staticmethod
    def _try_lock(descriptor: int) -> bool:
        try:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError) as error:
            if getattr(error, "errno", None) not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            return False

    @staticmethod
    def _unlock(descriptor: int) -> None:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)

    def _healthy(self, state: dict[str, object]) -> bool:
        port = state.get("port")
        token = state.get("token")
        if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or not token:
            return False
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.4)
        try:
            connection.request(
                "GET", "/", headers={
                    "Host": f"127.0.0.1:{port}",
                    "Cookie": f"{session_cookie_name_for_port(port)}={token}",
                },
            )
            response = connection.getresponse()
            response.read()
            return response.status == HTTPStatus.SEE_OTHER
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def acquire(self) -> tuple[bool, dict[str, object] | None]:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            descriptor = self._open_lock()
            if not self._try_lock(descriptor):
                os.close(descriptor)
                state = self._read_private_json(self.state_path)
                if state and self._healthy(state):
                    return False, state
                if time.monotonic() >= deadline:
                    raise RuntimeError("已有本机服务正在启动，请稍后重试")
                time.sleep(0.05)
                continue

            self._descriptor = descriptor
            self.owner = True
            try:
                self.state_path.unlink()
            except FileNotFoundError:
                pass
            return True, None

    def mark_ready(self, server: "LocalAnswerServer") -> None:
        if not self.owner:
            raise RuntimeError("未持有本机服务锁")
        write_json(self.state_path, {
            "pid": os.getpid(),
            "port": server.server_port,
            "token": server.session_token,
            "url": server.open_url,
            "state": "ready",
        })

    def release(self) -> None:
        if not self.owner or self._descriptor is None:
            return
        try:
            state = self._read_private_json(self.state_path)
            if not state or state.get("pid") == os.getpid():
                try:
                    self.state_path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            self._unlock(self._descriptor)
            os.close(self._descriptor)
            self._descriptor = None
            self.owner = False


class LocalAnswerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int = 0):
        self.root = root.resolve()
        self.session_token = secrets.token_urlsafe(32)
        self.write_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), LocalAnswerHandler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    @property
    def open_url(self) -> str:
        return f"{self.origin}/open?token={self.session_token}"

    @property
    def session_cookie_name(self) -> str:
        # Cookie 不按端口隔离；端口派生名称避免同一浏览器的随机端口实例互相覆盖。
        return session_cookie_name_for_port(self.server_port)


class LocalAnswerHandler(BaseHTTPRequestHandler):
    server: LocalAnswerServer

    def log_message(self, _format: str, *_args: object) -> None:
        # Never log tokens, private paths, interview text, or request bodies.
        return

    def send_data(self, status: int, body: bytes, content_type: str, *, cookie: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, value: dict[str, object]) -> None:
        self.send_data(status, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    def send_message_page(self, status: int, title: str, message: str) -> None:
        body = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{html.escape(title)}</title></head><body>
<main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p>请回到启动服务的终端，复制最新打印的本机入口；如果入口已失效，请重新运行启动命令。</p></main>
</body></html>""".encode("utf-8")
        self.send_data(status, body, "text/html; charset=utf-8")

    def valid_host(self) -> bool:
        return self.headers.get("Host", "") == f"127.0.0.1:{self.server.server_port}"

    def authenticated(self) -> bool:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        token = cookies.get(self.server.session_cookie_name)
        return token is not None and secrets.compare_digest(token.value, self.server.session_token)

    def do_GET(self) -> None:  # noqa: N802
        if not self.valid_host():
            self.send_message_page(HTTPStatus.FORBIDDEN, "访问被拒绝", "请使用本机服务打印的带令牌入口。")
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/open":
            token = parse_qs(parsed.query).get("token", [""])[0]
            if not secrets.compare_digest(token, self.server.session_token):
                self.send_message_page(
                    HTTPStatus.FORBIDDEN,
                    "本地会话已过期或无效",
                    "请从启动服务的终端复制最新的 /open?token=… 入口。",
                )
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/{HISTORY_HTML}")
            self.send_header(
                "Set-Cookie",
                f"{self.server.session_cookie_name}={self.server.session_token}; HttpOnly; SameSite=Strict; Path=/",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            return
        if not self.authenticated():
            self.send_message_page(
                HTTPStatus.FORBIDDEN,
                "本地会话未授权",
                "请从启动服务的终端复制带令牌的入口，不要直接打开裸地址。",
            )
            return
        if parsed.path == "/":
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/{HISTORY_HTML}")
            self.end_headers()
            return
        relative = unquote(parsed.path).lstrip("/")
        parts = Path(relative).parts
        allowed = relative in ALLOWED_ROOT_PAGES or re.fullmatch(
            r"interview-preparation-[a-z0-9-]+\.html", relative
        ) is not None or (
            len(parts) == 3 and parts[0] == "interviews" and parts[-1] == CASE_HTML
        )
        if not allowed:
            self.send_message_page(HTTPStatus.NOT_FOUND, "页面不存在", "请回到面试总览，或重新打开本机入口。")
            return
        target = (self.server.root / relative).resolve()
        if self.server.root not in target.parents or not target.is_file():
            self.send_message_page(HTTPStatus.NOT_FOUND, "页面不存在", "请回到面试总览，或重新打开本机入口。")
            return
        page = target.read_text(encoding="utf-8")
        page = page.replace("connect-src 'none'", "connect-src 'self'", 1)
        self.send_data(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if not self.valid_host() or not self.authenticated():
            self.send_json(HTTPStatus.FORBIDDEN, {"status": "error", "error": "本地会话未授权"})
            return
        endpoint = urlsplit(self.path).path
        if (endpoint not in {"/api/answers/confirm", "/api/case-answers/confirm", PRACTICE_ENDPOINT,
                             REVIEW_ACTIONS_ENDPOINT, RELATION_REMOVE_ENDPOINT, CLUSTER_ADD_ENDPOINT}
                or self.headers.get("Origin") != self.server.origin
                or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
            self.send_json(HTTPStatus.FORBIDDEN, {"status": "error", "error": "请求来源不受信任"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"status": "error", "error": "只接受 JSON 请求"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST_BYTES:
                raise ValueError("答案内容过长或为空")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("请求格式错误")
            if endpoint == REVIEW_ACTIONS_ENDPOINT:
                with self.server.write_lock:
                    result = self.apply_review_actions(data)
                self.send_json(HTTPStatus.OK, result)
                return
            if endpoint == PRACTICE_ENDPOINT:
                result = self.record_practice_attempt(data)
                self.send_json(HTTPStatus.OK, result)
                return
            if endpoint == RELATION_REMOVE_ENDPOINT:
                with self.server.write_lock:
                    result = self.remove_question_relation(data)
                self.send_json(HTTPStatus.OK, result)
                return
            if endpoint == CLUSTER_ADD_ENDPOINT:
                with self.server.write_lock:
                    result = self.add_questions_to_cluster(data)
                self.send_json(HTTPStatus.OK, result)
                return
            answer_id = str(data.get("answer_id") or "")
            expected_hash = str(data.get("expected_hash") or "")
            short = str(data.get("short_30s") or "").strip()
            full = str(data.get("standard_2m") or "").strip()
            if data.get("confirmed_facts") is not True:
                raise ValueError("请先核对并确认个人事实")
            if not expected_hash or not short or not full:
                raise ValueError("答案版本或内容缺失")
            if len(short) > 5_000 or len(full) > 15_000:
                raise ValueError("答案长度超过上限")
            with self.server.write_lock:
                if endpoint == "/api/case-answers/confirm":
                    result = self.confirm_case_answer(
                        str(data.get("case_id") or ""), str(data.get("question_id") or ""),
                        expected_hash, short, full)
                else:
                    if not answer_id:
                        raise ValueError("答案 ID 缺失")
                    result = self.confirm_answer(answer_id, expected_hash, short, full)
        except (OSError, ValueError, RuntimeError) as error:
            self.send_json(HTTPStatus.CONFLICT, {"status": "error", "error": public_error(error)})
            return
        self.send_json(HTTPStatus.OK, result)

    def apply_review_actions(self, data: dict[str, object]) -> dict[str, object]:
        actions = data.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("审核结果应用失败，可能已部分写入；请核对档案后刷新页面")
        payload = {"schema_version": 1, "actions": actions}
        try:
            with tempfile.TemporaryDirectory(
                prefix="review-actions-", dir=self.server.root / "logs"
            ) as temporary:
                action_path = Path(temporary) / "actions.json"
                write_json(action_path, payload)
                result = apply_actions(self.server.root, action_path)
        except (OSError, ValueError, RuntimeError) as error:
            raise ValueError("审核结果应用失败，可能已部分写入；请核对档案后刷新页面") from error

        scripts = Path(__file__).parent
        for command in (
            [sys.executable, str(scripts / "audit_standard_answers.py"), "--root", str(self.server.root), "--reset-confirmed-stale"],
            [sys.executable, str(scripts / "build_review_plan.py"), "--root", str(self.server.root)],
            [sys.executable, str(scripts / "render_interview_html.py"), "--root", str(self.server.root), "--all"],
        ):
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            if completed.returncode != 0:
                return {
                    **result,
                    "page_refresh_required": True,
                    "warning": "审核结果已写入档案，但后续页面重建失败；请手动运行审核校验并刷新页面。",
                }
        return result

    def record_practice_attempt(self, data: dict[str, object]) -> dict[str, object]:
        case_id = str(data.get("case_id") or "")
        question_id = str(data.get("question_id") or "")
        answer = str(data.get("answer") or "")
        duration = data.get("duration_seconds")
        rating = data.get("self_rating")
        if type(duration) is not int or type(rating) is not int:
            raise ValueError("练习时长须在 1–3600 秒之间")
        if not case_id or not question_id:
            raise ValueError("场次或题目缺失")
        attempt_id = str(data.get("attempt_id") or "")
        with self.server.write_lock:
            event = record_attempt(
                self.server.root,
                case_id=case_id,
                question_id=question_id,
                answer=answer,
                duration_seconds=duration,
                self_rating=rating,
                note=str(data.get("note") or ""),
                attempt_id=attempt_id or None,
            )
        result = {
            "status": "recorded",
            "attempt_id": event["attempt_id"],
            "review_status": event.get("review_status", "pending"),
            "deduplicated": bool(event.get("deduplicated")),
        }
        if not result["deduplicated"]:
            render = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("render_interview_html.py")),
                 "--root", str(self.server.root), "--all"],
                text=True, capture_output=True, check=False,
            )
            if render.returncode != 0:
                result["page_refresh_required"] = True
                result["warning"] = "练习记录已写入档案，但页面重建失败；请手动重建页面后刷新。"
        return result

    def _apply_relation_action(self, action: dict[str, object]) -> dict[str, object]:
        root = self.server.root
        try:
            with tempfile.TemporaryDirectory(prefix="relation-update-", dir=root / "logs") as temporary:
                action_path = Path(temporary) / "action.json"
                write_json(action_path, {"schema_version": 1, "actions": [action]})
                apply_actions(root, action_path)
        except (OSError, ValueError, RuntimeError) as error:
            message = str(error)
            if "至少引用一道真实问题" in message:
                raise ValueError("跨场答案至少引用一道真实问题") from error
            if "至少引用两道真实问题" in message or "覆盖一场面试" in message:
                raise ValueError("移出后题组必须至少保留两道跨场原题") from error
            raise ValueError("题组关系保存失败，可能已部分写入；请刷新页面核对") from error
        render = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("render_interview_html.py")), "--root", str(root), "--all"],
            text=True, capture_output=True, check=False,
        )
        result: dict[str, object] = {"status": "saved"}
        if render.returncode != 0:
            result["page_refresh_required"] = True
            result["warning"] = "关系已写入档案，但页面没有重建成功；请手动运行渲染器后刷新。"
        return result

    def remove_question_relation(self, data: dict[str, object]) -> dict[str, object]:
        root = self.server.root
        require_store_compatible(root)
        kind = str(data.get("relation_kind") or "")
        relation_id = str(data.get("relation_id") or "")
        case_id = str(data.get("case_id") or "")
        question_id = str(data.get("question_id") or "")
        expected_status = str(data.get("relation_status") or "")
        expected_fingerprint = str(data.get("expected_fingerprint") or "")
        if kind not in {"question_cluster", "standard_answer"} or not relation_id or not case_id or not question_id:
            raise ValueError("请求格式错误")
        if not expected_fingerprint:
            raise ValueError("关联已变化，请刷新页面后重试")
        if kind == "question_cluster":
            path = root / "profile" / "question-clusters.json"
            payload = read_json(path)
            target = next((item for item in payload.get("clusters", [])
                           if isinstance(item, dict) and item.get("cluster_id") == relation_id), None)
            if target is None or str(target.get("status") or "") not in {"pending", "confirmed"}:
                raise ValueError("关联已变化，请刷新页面后重试")
            if expected_status and str(target.get("status") or "") != expected_status:
                raise ValueError("关联已变化，请刷新页面后重试")
            if not secrets.compare_digest(answer_fingerprint(target), expected_fingerprint):
                raise ValueError("关联已变化，请刷新页面后重试")
            current_refs = [ref for ref in target.get("questions", []) if isinstance(ref, dict)]
            selected_key = (case_id, question_id)
            if not any(
                (str(ref.get("case_id") or ""), str(ref.get("question_id") or "")) == selected_key
                for ref in current_refs
            ):
                raise ValueError("关联已变化，请刷新页面后重试")
            refs = [ref for ref in current_refs
                    if (str(ref.get("case_id") or ""), str(ref.get("question_id") or "")) != selected_key]
            dissolves_cluster = len(refs) < 2 or len({str(ref.get("case_id") or "") for ref in refs}) < 2
            action = {"kind": "question_cluster", "id": relation_id,
                      "expected_status": str(target.get("status") or "pending"),
                      "status": "rejected" if dissolves_cluster else str(target.get("status") or "pending"),
                      "reason": ("用户在本机页面移出指定关联；剩余成员不再构成跨场题组，因此解散整组"
                                 if dissolves_cluster else "用户在本机页面移出指定同题组关联"),
                      "patch": {} if dissolves_cluster else {"questions": refs}}
        else:
            path = root / "profile" / "standard-answers.json"
            payload = read_json(path)
            target = next((item for item in payload.get("answers", [])
                           if isinstance(item, dict) and item.get("answer_id") == relation_id), None)
            if target is None or str(target.get("status") or "") not in {"pending", "confirmed"}:
                raise ValueError("关联已变化，请刷新页面后重试")
            if expected_status and str(target.get("status") or "") != expected_status:
                raise ValueError("关联已变化，请刷新页面后重试")
            if not secrets.compare_digest(answer_fingerprint(target), expected_fingerprint):
                raise ValueError("关联已变化，请刷新页面后重试")
            refs = [ref for ref in target.get("source_questions", [])
                    if not (isinstance(ref, dict) and str(ref.get("case_id") or "") == case_id
                            and str(ref.get("question_id") or "") == question_id)]
            action = {"kind": "standard_answer", "id": relation_id,
                      "expected_status": str(target.get("status") or "pending"),
                      "status": str(target.get("status") or "pending"),
                      "reason": "用户在本机页面移出指定跨场答案引用",
                      "patch": {"source_questions": refs}}
        result = self._apply_relation_action(action)
        if kind == "question_cluster" and dissolves_cluster:
            result["dissolved_cluster"] = True
        return result

    def add_questions_to_cluster(self, data: dict[str, object]) -> dict[str, object]:
        root = self.server.root
        require_store_compatible(root)
        cluster_id = str(data.get("cluster_id") or "")
        expected_status = str(data.get("expected_status") or "")
        expected_fingerprint = str(data.get("expected_fingerprint") or "")
        incoming = data.get("questions")
        if not cluster_id or not expected_fingerprint or not isinstance(incoming, list) or not incoming:
            raise ValueError("请至少选择 1 道未归组原题")
        path = root / "profile" / "question-clusters.json"
        payload = read_json(path)
        target = next((item for item in payload.get("clusters", [])
                       if isinstance(item, dict) and item.get("cluster_id") == cluster_id), None)
        if target is None or str(target.get("status") or "") not in {"pending", "confirmed"}:
            raise ValueError("关联已变化，请刷新页面后重试")
        if expected_status and str(target.get("status") or "") != expected_status:
            raise ValueError("关联已变化，请刷新页面后重试")
        if not secrets.compare_digest(answer_fingerprint(target), expected_fingerprint):
            raise ValueError("关联已变化，请刷新页面后重试")
        current = [ref for ref in target.get("questions", []) if isinstance(ref, dict)]
        current_keys = {(str(ref.get("case_id") or ""), str(ref.get("question_id") or "")) for ref in current}
        additions = []
        for ref in incoming:
            if not isinstance(ref, dict):
                raise ValueError("请求格式错误")
            key = (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            if not all(key) or key in current_keys or any(
                isinstance(old, dict) and (str(old.get("case_id") or ""), str(old.get("question_id") or "")) == key
                for old in additions
            ):
                raise ValueError("关联已变化，请刷新页面后重试")
            if self._question_exists(root, *key) is False:
                raise ValueError("题目已不存在或页面已过期，请刷新页面后重试")
            additions.append({"case_id": key[0], "question_id": key[1]})
        action = {"kind": "question_cluster", "id": cluster_id,
                  "expected_status": str(target.get("status") or "pending"),
                  "status": str(target.get("status") or "pending"),
                  "reason": "用户在本机页面将未归组原题加入指定题组",
                  "patch": {"questions": current + additions}}
        return self._apply_relation_action(action)

    @staticmethod
    def _question_exists(root: Path, case_id: str, question_id: str) -> bool:
        for path in (root / "interviews").glob("*/qa-tree.json"):
            payload = read_json(path)
            if payload.get("case_id") == case_id and any(
                isinstance(item, dict) and item.get("question_id") == question_id
                for item in payload.get("questions", [])
            ):
                return True
        return False

    def confirm_answer(self, answer_id: str, expected_hash: str,
                       short: str, full: str) -> dict[str, object]:
        root = self.server.root
        require_store_compatible(root)
        payload = json.loads((root / "profile" / "standard-answers.json").read_text(encoding="utf-8"))
        target = next((item for item in payload.get("answers", [])
                       if isinstance(item, dict) and item.get("answer_id") == answer_id), None)
        if target is None or target.get("status") not in {"pending", "confirmed"}:
            raise ValueError("这份答案已不存在或不能确认，请刷新页面")
        if not secrets.compare_digest(answer_fingerprint(target), expected_hash):
            raise ValueError("答案已被修改，请刷新页面后核对新版本")
        action = {"schema_version": 1, "actions": [{
            "kind": "standard_answer", "id": answer_id,
            "expected_status": target["status"], "status": "confirmed",
            "reason": "用户在本地网页核对事实并确认当前答案版本",
            "patch": {"short_30s": short, "standard_2m": full},
        }]}
        with tempfile.TemporaryDirectory(prefix="answer-confirm-", dir=root / "logs") as temporary:
            action_path = Path(temporary) / "action.json"
            write_json(action_path, action)
            apply_actions(root, action_path)
        updated = json.loads((root / "profile" / "standard-answers.json").read_text(encoding="utf-8"))
        confirmed = next(item for item in updated["answers"] if item.get("answer_id") == answer_id)
        render = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("render_interview_html.py")),
             "--root", str(root), "--all"],
            text=True, capture_output=True, check=False,
        )
        if render.returncode != 0:
            return {"status": "saved", "answer_id": answer_id,
                    "fingerprint": answer_fingerprint(confirmed),
                    "page_refresh_required": True,
                    "warning": "答案已写入档案，但页面没有重建成功；请手动刷新并检查页面"}
        return {"status": "saved", "answer_id": answer_id,
                "fingerprint": answer_fingerprint(confirmed)}

    def confirm_case_answer(self, case_id: str, question_id: str, expected_hash: str,
                            short: str, full: str) -> dict[str, object]:
        root = self.server.root
        require_store_compatible(root)
        if not case_id or not question_id:
            raise ValueError("场次或题目缺失")
        case_path = next((path for path in (root / "interviews").iterdir()
                          if path.is_dir() and (path / "manifest.json").is_file()
                          and read_json(path / "manifest.json").get("case_id") == case_id), None)
        if case_path is None:
            raise ValueError("本场面试不存在，请刷新页面")
        qa = read_json(case_path / "qa-tree.json")
        if question_id not in {str(q.get("question_id") or "") for q in qa.get("questions", [])
                               if isinstance(q, dict)}:
            raise ValueError("本场题目不存在，请刷新页面")
        path = case_path / "practice-answers.json"
        payload = read_json(path) if path.is_file() else {"schema_version": 1, "questions": []}
        questions = payload.get("questions", [])
        if not isinstance(questions, list):
            raise ValueError("逐题答案文件格式错误")
        entry = next((q for q in questions if isinstance(q, dict)
                      and q.get("question_id") == question_id), None)
        before = entry.get("curated_answer") if entry is not None else None
        if not secrets.compare_digest(answer_fingerprint(before if isinstance(before, dict) else {}), expected_hash):
            raise ValueError("答案已被修改，请刷新页面后核对新版本")
        after = {"status": "confirmed", "versions": {"short_30s": short, "standard_2m": full}}
        if entry is None:
            entry = {"question_id": question_id}
            questions.append(entry)
        entry["curated_answer"] = after
        write_json(path, payload)
        revision = {"schema_version": 1, "at": datetime.now(timezone.utc).isoformat(),
                    "case_id": case_id, "question_id": question_id,
                    "reason": "用户在本机网页核对个人事实并保存本题答案",
                    "before": before, "after": after}
        append_private_text(root / "profile" / "case-answer-revisions.jsonl",
                            json.dumps(revision, ensure_ascii=False) + "\n")
        render = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("render_interview_html.py")),
             "--root", str(root), "--all"], text=True, capture_output=True, check=False,
        )
        result: dict[str, object] = {"status": "saved", "fingerprint": answer_fingerprint(after)}
        if render.returncode != 0:
            result["page_refresh_required"] = True
            result["warning"] = "答案已写入档案，但页面没有重建成功；请手动刷新并检查页面"
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="在本机网页直接核对并保存跨场及单场答案")
    parser.add_argument("--root", required=True, type=Path, help="用户已指定的私有档案绝对路径")
    parser.add_argument("--port", type=int, default=0, help="本机端口；默认随机空闲端口")
    parser.add_argument("--open-browser", action="store_true", help="启动后在默认浏览器打开本机入口")
    args = parser.parse_args()
    os.umask(0o077)
    requested = args.root.expanduser()
    if not requested.is_absolute():
        parser.error("--root 必须是用户指定的绝对路径")
    root = requested.resolve()
    require_store_compatible(root)
    if not (root / HISTORY_HTML).is_file():
        parser.error("档案首页不存在，请先生成 HTML")
    instance_lock = ServerInstanceLock(root)
    try:
        is_owner, existing = instance_lock.acquire()
    except RuntimeError as error:
        parser.error(str(error))
    if not is_owner:
        assert existing is not None
        existing_url = str(existing["url"])
        print(json.dumps({"status": "already-running", "url": existing_url}, ensure_ascii=False), flush=True)
        if args.open_browser and not webbrowser.open(existing_url):
            print("浏览器未自动打开，请复制上面的本机网址。", file=sys.stderr, flush=True)
        return 0

    server: LocalAnswerServer | None = None
    try:
        server = LocalAnswerServer(root, args.port)
        instance_lock.mark_ready(server)
        print(json.dumps({"status": "ready", "url": server.open_url}, ensure_ascii=False), flush=True)
        if args.open_browser and not webbrowser.open(server.open_url):
            print("浏览器未自动打开，请复制上面的本机网址。", file=sys.stderr, flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        instance_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
