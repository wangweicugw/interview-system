"""The web confirmation must really persist an answer and reject stale edits."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import shutil
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from demo_smoke import run_demo  # noqa: E402
from local_answer_server import LocalAnswerHandler, LocalAnswerServer, ServerInstanceLock  # noqa: E402
from render_interview_html import answer_fingerprint  # noqa: E402


class LocalAnswerServerTest(unittest.TestCase):
    def test_relations_are_independent_and_cluster_add_uses_stale_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            run_demo(root)
            first_path = next((root / "interviews").iterdir())
            first_manifest = json.loads((first_path / "manifest.json").read_text(encoding="utf-8"))
            first_case_id = first_manifest["case_id"]
            second_case_id = first_case_id + "-second"
            second_path = root / "interviews" / "demo-second"
            shutil.copytree(first_path, second_path)
            manifest = json.loads((second_path / "manifest.json").read_text(encoding="utf-8"))
            manifest.update({"case_id": second_case_id, "date": "2026-01-02", "company": "虚构第二家公司", "analysis_status": "not_started"})
            (second_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            qa_path = second_path / "qa-tree.json"
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            qa["case_id"] = second_case_id
            qa_path.write_text(json.dumps(qa, ensure_ascii=False), encoding="utf-8")
            cluster = {
                "cluster_id": "demo-cluster", "label": "虚构同题组", "topic": "other",
                "status": "pending", "note": "虚构测试。", "questions": [
                    {"case_id": first_case_id, "question_id": "q-001"},
                    {"case_id": second_case_id, "question_id": "q-001"},
                ],
            }
            (root / "profile" / "question-clusters.json").write_text(
                json.dumps({"schema_version": 1, "clusters": [cluster]}, ensure_ascii=False), encoding="utf-8"
            )
            answer = {
                "answer_id": "demo-cross-answer", "topic": "other", "title": "虚构跨场答案",
                "status": "pending", "basis": "虚构测试资料。",
                "source_questions": list(cluster["questions"]),
                "versions": {"short_30s": "虚构短答", "standard_2m": "虚构长答", "followups": []},
                "limitations": [], "sources": [],
            }
            (root / "profile" / "standard-answers.json").write_text(
                json.dumps({"schema_version": 1, "answers": [answer]}, ensure_ascii=False), encoding="utf-8"
            )
            rendered = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_interview_html.py"), "--root", str(root), "--all"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout[-1000:] + rendered.stderr[-1000:])
            server = LocalAnswerServer(root)
            try:
                handler = object.__new__(LocalAnswerHandler)
                handler.server = server
                saved = handler.remove_question_relation({
                    "relation_kind": "standard_answer", "relation_id": "demo-cross-answer",
                    "relation_status": "pending", "expected_fingerprint": answer_fingerprint(answer),
                    "case_id": second_case_id, "question_id": "q-001",
                })
                self.assertEqual(saved["status"], "saved")
                answer_after = json.loads((root / "profile" / "standard-answers.json").read_text(encoding="utf-8"))["answers"][0]
                self.assertEqual(answer_after["source_questions"], [cluster["questions"][0]])
                cluster_after = json.loads((root / "profile" / "question-clusters.json").read_text(encoding="utf-8"))["clusters"][0]
                self.assertEqual(len(cluster_after["questions"]), 2)
                self.assertIn('"before":', (root / "profile" / "standard-answer-revisions.jsonl").read_text(encoding="utf-8"))

                added = handler.add_questions_to_cluster({
                    "cluster_id": "demo-cluster", "expected_status": "pending",
                    "expected_fingerprint": answer_fingerprint(cluster_after),
                    "questions": [{"case_id": second_case_id, "question_id": "q-002"}],
                })
                self.assertEqual(added["status"], "saved")
                cluster_after_add = json.loads((root / "profile" / "question-clusters.json").read_text(encoding="utf-8"))["clusters"][0]
                self.assertEqual(len(cluster_after_add["questions"]), 3)
                removed = handler.remove_question_relation({
                    "relation_kind": "question_cluster", "relation_id": "demo-cluster",
                    "relation_status": "pending", "expected_fingerprint": answer_fingerprint(cluster_after_add),
                    "case_id": second_case_id, "question_id": "q-002",
                })
                self.assertEqual(removed["status"], "saved")
                cluster_after_remove = json.loads((root / "profile" / "question-clusters.json").read_text(encoding="utf-8"))["clusters"][0]
                self.assertEqual(len(cluster_after_remove["questions"]), 2)
                revisions_path = root / "profile" / "question-cluster-revisions.jsonl"
                revisions_before_invalid = revisions_path.read_bytes()
                with self.assertRaises(ValueError):
                    handler.remove_question_relation({
                        "relation_kind": "question_cluster", "relation_id": "demo-cluster",
                        "relation_status": "pending", "expected_fingerprint": answer_fingerprint(cluster_after_remove),
                        "case_id": second_case_id, "question_id": "q-001",
                    })
                self.assertEqual(revisions_path.read_bytes(), revisions_before_invalid)
            finally:
                server.server_close()

    def test_confirm_saves_answer_and_rejects_stale_or_unauthorized_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            run_demo(root)
            case_path = next((root / "interviews").iterdir())
            case_id = json.loads((case_path / "qa-tree.json").read_text(encoding="utf-8"))["case_id"]
            answer = {
                "answer_id": "demo-answer", "topic": "other", "title": "介绍缓存项目",
                "status": "pending", "basis": "根据虚构面试记录整理。",
                "source_questions": [{"case_id": case_id, "question_id": "q-002"}],
                "versions": {"short_30s": "原短答案", "standard_2m": "原完整答案", "followups": []},
                "limitations": [], "sources": [],
            }
            (root / "profile" / "standard-answers.json").write_text(
                json.dumps({"schema_version": 1, "answers": [answer]}, ensure_ascii=False), encoding="utf-8"
            )
            rendered = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_interview_html.py"), "--root", str(root), "--all"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout[-1000:] + rendered.stderr[-1000:])
            self.assertIn('data-answer-id="demo-answer"',
                          (root / "interview-history.html").read_text(encoding="utf-8"))
            server = LocalAnswerServer(root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method: str, path: str, body: dict | None = None,
                            cookie: str = "", origin: str = "") -> tuple[int, bytes, dict[str, str]]:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                    headers = {"Host": f"127.0.0.1:{server.server_port}"}
                    if cookie:
                        headers["Cookie"] = cookie
                    if origin:
                        headers["Origin"] = origin
                        headers["Content-Type"] = "application/json"
                    connection.request(method, path, json.dumps(body).encode() if body else None, headers)
                    response = connection.getresponse()
                    result = response.status, response.read(), dict(response.getheaders())
                    connection.close()
                    return result

                status, _, headers = request("GET", "/open?token=" + server.session_token)
                self.assertEqual(status, 303)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                status, page, _ = request("GET", "/interview-history.html", cookie=cookie)
                self.assertEqual(status, 200)
                self.assertIn(b"connect-src 'self'", page)
                self.assertEqual(request("GET", "/interview-history.html")[0], 403)

                edit = {"answer_id": "demo-answer", "expected_hash": answer_fingerprint(answer),
                        "short_30s": "新短答案", "standard_2m": "新完整答案", "confirmed_facts": True}
                self.assertEqual(request("POST", "/api/answers/confirm", edit,
                                         origin=server.origin)[0], 403)
                self.assertEqual(request("POST", "/api/answers/confirm", edit,
                                         cookie=cookie, origin="http://evil.example")[0], 403)
                status, body, _ = request("POST", "/api/answers/confirm", edit,
                                          cookie=cookie, origin=server.origin)
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["status"], "saved")
                saved = json.loads((root / "profile" / "standard-answers.json").read_text(encoding="utf-8"))["answers"][0]
                self.assertEqual(saved["status"], "confirmed")
                self.assertEqual(saved["versions"]["short_30s"], "新短答案")
                self.assertTrue((root / "profile" / "standard-answer-revisions.jsonl").is_file())
                self.assertNotIn("新短答案", (root / "interview-history.html").read_text(encoding="utf-8"))
                self.assertIn("新短答案", (root / "interview-answers.html").read_text(encoding="utf-8"))
                status, refreshed, _ = request("GET", "/interview-history.html", cookie=cookie)
                self.assertEqual(status, 200)
                self.assertIn("已确认", refreshed.decode("utf-8"))
                self.assertEqual(request("POST", "/api/answers/confirm", edit,
                                         cookie=cookie, origin=server.origin)[0], 409)

                single = {"case_id": case_id, "question_id": "q-001",
                          "expected_hash": answer_fingerprint({}),
                          "short_30s": "本题短答案", "standard_2m": "本题完整答案",
                          "confirmed_facts": True}
                case_html_before = (case_path / "interview-review.html").read_text(encoding="utf-8")
                self.assertIn('data-answer-kind="case"', case_html_before)
                self.assertEqual(request("POST", "/api/case-answers/confirm", single,
                                         cookie=cookie, origin="http://evil.example")[0], 403)
                status, body, _ = request("POST", "/api/case-answers/confirm", single,
                                          cookie=cookie, origin=server.origin)
                self.assertEqual(status, 200, body)
                self.assertEqual(request("POST", "/api/case-answers/confirm", single,
                                         cookie=cookie, origin=server.origin)[0], 409)
                data = json.loads((case_path / "practice-answers.json").read_text(encoding="utf-8"))
                self.assertEqual(next(q for q in data["questions"] if q["question_id"] == "q-001")
                                 ["curated_answer"]["versions"]["short_30s"], "本题短答案")
                self.assertTrue((root / "profile" / "case-answer-revisions.jsonl").is_file())
                case_html_after = (case_path / "interview-review.html").read_text(encoding="utf-8")
                self.assertIn("本题短答案", case_html_after)
                self.assertIn("我的现场回答", case_html_after)
                self.assertIn('>本场练习</a>', case_html_after)
                self.assertNotIn('>完整题库</a>', case_html_after)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_practice_records_directly_and_retries_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            run_demo(root)
            case_path = next((root / "interviews").iterdir())
            case_id = json.loads((case_path / "qa-tree.json").read_text(encoding="utf-8"))["case_id"]
            server = LocalAnswerServer(root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method: str, path: str, body: dict | None = None,
                            cookie: str = "", origin: str = "") -> tuple[int, bytes, dict[str, str]]:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                    headers = {"Host": f"127.0.0.1:{server.server_port}"}
                    if cookie:
                        headers["Cookie"] = cookie
                    if origin:
                        headers["Origin"] = origin
                        headers["Content-Type"] = "application/json"
                    connection.request(method, path, json.dumps(body).encode() if body else None, headers)
                    response = connection.getresponse()
                    result = response.status, response.read(), dict(response.getheaders())
                    connection.close()
                    return result

                status, _, headers = request("GET", "/open?token=" + server.session_token)
                self.assertEqual(status, 303)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                attempt = {
                    "attempt_id": "practice-0123456789abcdef",
                    "case_id": case_id,
                    "question_id": "q-001",
                    "answer": "这是一次虚构练习回答。",
                    "duration_seconds": 3,
                    "self_rating": 4,
                    "note": "虚构备注",
                }
                path = "/api/practice-attempts/record"
                status, body, _ = request("POST", path, attempt, cookie=cookie, origin=server.origin)
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["status"], "recorded")
                self.assertEqual(json.loads(body)["review_status"], "pending")
                status, body, _ = request("POST", path, attempt, cookie=cookie, origin=server.origin)
                self.assertEqual(status, 200, body)
                self.assertTrue(json.loads(body)["deduplicated"])
                events = [
                    line for line in (root / "practice" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertEqual(len(events), 1)
                self.assertTrue((root / "practice" / "private" / "practice-0123456789abcdef.txt").is_file())

                status, body, _ = request("GET", "/")
                self.assertEqual(status, 403)
                self.assertIn("本地会话未授权", body.decode("utf-8"))
                self.assertNotIn("http.server", body.decode("utf-8"))
                status, body, _ = request("GET", "/open?token=invalid")
                self.assertEqual(status, 403)
                text = body.decode("utf-8")
                self.assertIn("会话已过期或无效", text)
                self.assertNotIn(server.session_token, text)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_review_actions_apply_in_page_with_stale_guard_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            run_demo(root)
            case_path = next((root / "interviews").iterdir())
            case_id = json.loads((case_path / "qa-tree.json").read_text(encoding="utf-8"))["case_id"]
            answer = {
                "answer_id": "demo-review-answer", "topic": "other", "title": "虚构答案",
                "status": "pending", "basis": "虚构测试资料。",
                "source_questions": [{"case_id": case_id, "question_id": "q-002"}],
                "versions": {"short_30s": "旧短答案", "standard_2m": "旧完整答案", "followups": []},
                "limitations": [], "sources": [],
            }
            (root / "profile" / "standard-answers.json").write_text(
                json.dumps({"schema_version": 1, "answers": [answer]}, ensure_ascii=False), encoding="utf-8"
            )
            rendered = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_interview_html.py"), "--root", str(root), "--all"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stdout[-1000:] + rendered.stderr[-1000:])
            server = LocalAnswerServer(root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method: str, path: str, body: dict | None = None,
                            cookie: str = "", origin: str = "") -> tuple[int, bytes, dict[str, str]]:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                    headers = {"Host": f"127.0.0.1:{server.server_port}"}
                    if cookie:
                        headers["Cookie"] = cookie
                    if origin:
                        headers["Origin"] = origin
                        headers["Content-Type"] = "application/json"
                    connection.request(method, path, json.dumps(body).encode() if body else None, headers)
                    response = connection.getresponse()
                    result = response.status, response.read(), dict(response.getheaders())
                    connection.close()
                    return result

                status, _, headers = request("GET", "/open?token=" + server.session_token)
                self.assertEqual(status, 303)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                action = {
                    "kind": "standard_answer", "id": "demo-review-answer", "expected_status": "pending",
                    "status": "confirmed", "reason": "虚构测试确认",
                    "patch": {"short_30s": "新短答案", "standard_2m": "新完整答案"},
                }
                status, body, _ = request(
                    "POST", "/api/review-actions/apply", {"schema_version": 1, "actions": [action]},
                    cookie=cookie, origin=server.origin,
                )
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["status"], "applied")
                saved = json.loads((root / "profile" / "standard-answers.json").read_text(encoding="utf-8"))["answers"][0]
                self.assertEqual(saved["status"], "confirmed")
                self.assertEqual(saved["versions"]["short_30s"], "新短答案")
                self.assertFalse(list((root / "logs").glob("review-actions-*")))

                status, body, _ = request(
                    "POST", "/api/review-actions/apply", {"schema_version": 1, "actions": [action]},
                    cookie=cookie, origin=server.origin,
                )
                self.assertEqual(status, 409)
                self.assertIn("可能已部分写入", json.loads(body)["error"])
                self.assertEqual(
                    json.loads((root / "profile" / "standard-answers.json").read_text(encoding="utf-8"))["answers"][0]["versions"]["short_30s"],
                    "新短答案",
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_parallel_servers_do_not_overwrite_session_cookie_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            run_demo(root)
            first = LocalAnswerServer(root)
            second = LocalAnswerServer(root)
            first_thread = threading.Thread(target=first.serve_forever, daemon=True)
            second_thread = threading.Thread(target=second.serve_forever, daemon=True)
            first_thread.start()
            second_thread.start()
            try:
                def open_cookie(server: LocalAnswerServer) -> str:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                    connection.request("GET", "/open?token=" + server.session_token,
                                       headers={"Host": f"127.0.0.1:{server.server_port}"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 303)
                    cookie = dict(response.getheaders())["Set-Cookie"].split(";", 1)[0]
                    response.read()
                    connection.close()
                    return cookie

                first_cookie = open_cookie(first)
                second_cookie = open_cookie(second)
                self.assertNotEqual(first.session_cookie_name, second.session_cookie_name)
                combined = first_cookie + "; " + second_cookie
                for server in (first, second):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                    connection.request(
                        "GET", "/interview-history.html",
                        headers={"Host": f"127.0.0.1:{server.server_port}", "Cookie": combined},
                    )
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    connection.close()
            finally:
                for server, thread in ((first, first_thread), (second, second_thread)):
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_single_instance_reuses_entry_and_recovers_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-store"
            run_demo(root)
            command = [sys.executable, str(SCRIPTS / "local_answer_server.py"), "--root", str(root)]
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"

            def start() -> tuple[subprocess.Popen, dict[str, str]]:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=environment,
                )
                assert process.stdout is not None
                line = process.stdout.readline().strip()
                self.assertTrue(line)
                return process, json.loads(line)

            first, first_result = start()
            second = None
            third = None
            try:
                self.assertEqual(first_result["status"], "ready")
                second, second_result = start()
                self.assertEqual(second_result, {"status": "already-running", "url": first_result["url"]})
                self.assertEqual(second.wait(timeout=5), 0)
                self.assertEqual((root / "logs" / "local-answer-server.lock").stat().st_mode & 0o777, 0o600)
                self.assertEqual((root / "logs" / "local-answer-server.json").stat().st_mode & 0o777, 0o600)

                port = int(first_result["url"].split(":", 2)[2].split("/", 1)[0])
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
                self.assertEqual(connection.getresponse().status, 403)
                connection.close()

                # 强制终止跳过 finally，验证下次启动能由 OS 释放锁并恢复。
                first.kill()
                first.wait(timeout=5)
                third, third_result = start()
                self.assertEqual(third_result["status"], "ready")
                self.assertNotEqual(third_result["url"], first_result["url"])
            finally:
                for process in (first, second, third):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                    if process is not None:
                        if process.stdout is not None:
                            process.stdout.close()
                        if process.stderr is not None:
                            process.stderr.close()

    def test_single_instance_competition_and_different_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "same-root"
            other_root = base / "other-root"
            run_demo(root)
            run_demo(other_root)
            # The lock file persists after release; contenders must race on the
            # advisory lock itself, not delete and replace a stale marker.
            seed_lock = ServerInstanceLock(root)
            seed_owner, _ = seed_lock.acquire()
            self.assertTrue(seed_owner)
            seed_lock.release()
            command = [sys.executable, str(SCRIPTS / "local_answer_server.py"), "--root", str(root)]
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            processes = [subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=environment,
            ) for _ in range(4)]
            other = subprocess.Popen(
                [sys.executable, str(SCRIPTS / "local_answer_server.py"), "--root", str(other_root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
            )

            def read_result(process: subprocess.Popen) -> dict[str, str]:
                assert process.stdout is not None
                line = process.stdout.readline().strip()
                self.assertTrue(line)
                return json.loads(line)

            try:
                results = [read_result(process) for process in processes]
                other_result = read_result(other)
                self.assertEqual(sum(result["status"] == "ready" for result in results), 1)
                self.assertEqual({result["url"] for result in results}, {results[0]["url"]})
                self.assertEqual(other_result["status"], "ready")
                self.assertNotEqual(other_result["url"], results[0]["url"])
            finally:
                for process in processes + [other]:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()


if __name__ == "__main__":
    unittest.main()
