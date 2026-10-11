"""Run a single algorithm case in a restricted container or explicitly trusted local mode."""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


HARNESS = r"""
import contextlib, io, json, runpy, sys
try:
    request = json.load(sys.stdin)
    with contextlib.redirect_stdout(io.StringIO()):
        namespace = runpy.run_path(sys.argv[1])
        result = namespace['solve'](request)
    print(json.dumps({'result': result}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=False))
"""
JAVA_HARNESS = Path(__file__).with_name("CoachHarness.java")
IMAGES = {"python": "python:3.12-alpine", "java": "eclipse-temurin:17-jdk"}
OUTPUT_LIMIT = 65536


def docker_available(image: str) -> None:
    if not shutil.which("docker"):
        raise RuntimeError("隔离执行需要 Docker；请安装并启动 Docker，或仅对已审阅代码显式使用 --runner local --trust-solution")
    try:
        daemon = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Docker 状态检查超时或失败；请检查守护进程") from error
    if daemon.returncode:
        raise RuntimeError("无法连接 Docker 守护进程；请启动 Docker 并确认当前用户有权限")
    try:
        cached = subprocess.run(["docker", "image", "inspect", image],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Docker 镜像检查超时或失败") from error
    if cached.returncode:
        raise RuntimeError(f"隔离镜像未在本机缓存：{image}；先自行执行 docker pull {image}，或用 --image 指定已缓存镜像")


def _limit_output() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))


def _remove_container(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _execute(command: list[str], data: dict[str, Any], timeout: int, *, cwd: Path | None = None,
             container_name: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.run(
                command, input=(json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"),
                stdout=output, stderr=subprocess.STDOUT, cwd=cwd, check=False, timeout=timeout,
                env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "PYTHONNOUSERSITE": "1"},
                preexec_fn=_limit_output,
            )
        except subprocess.TimeoutExpired:
            if container_name:
                _remove_container(container_name)
            return {"error": f"超过 {timeout} 秒时限", "elapsed_ms": timeout * 1000}
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        output.seek(0)
        body = output.read(OUTPUT_LIMIT).decode("utf-8", errors="replace")
    if process.returncode:
        if container_name:
            _remove_container(container_name)
        detail = body.strip()[-500:]
        return {"error": f"运行或编译失败（退出码 {process.returncode}）：{detail}", "elapsed_ms": elapsed}
    try:
        payload = json.loads(body.strip().splitlines()[-1])
        if not isinstance(payload, dict):
            raise ValueError("输出应为对象")
    except (ValueError, IndexError):
        return {"error": "输出不是有效 JSON；请检查 solve 返回值", "elapsed_ms": elapsed}
    payload["elapsed_ms"] = elapsed
    return payload


def run_case(solution: Path, data: dict[str, Any], language: str, runner: str, image: str | None = None) -> dict[str, Any]:
    if runner == "local":
        if language == "python":
            return _execute([sys.executable, "-I", "-S", "-c", HARNESS, str(solution)], data, 3, cwd=solution.parent)
        with tempfile.TemporaryDirectory(prefix="coach-java-") as temporary:
            work = Path(temporary)
            shutil.copy2(solution, work / "Solution.java")
            shutil.copy2(JAVA_HARNESS, work / "CoachHarness.java")
            compile_result = subprocess.run(["javac", "-d", str(work), str(work / "Solution.java"), str(work / "CoachHarness.java")],
                                            text=True, capture_output=True, timeout=10, check=False)
            if compile_result.returncode:
                return {"error": "Java 编译失败：" + compile_result.stderr[-1000:], "elapsed_ms": 0}
            return _execute(["java", "-cp", str(work), "CoachHarness"], data, 3, cwd=work)

    with tempfile.TemporaryDirectory(prefix="coach-container-") as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        filename = "solution.py" if language == "python" else "Solution.java"
        staged = work / filename
        shutil.copyfile(solution, staged)
        staged.chmod(0o644)
        if language == "java":
            harness = work / "CoachHarness.java"
            shutil.copyfile(JAVA_HARNESS, harness)
            harness.chmod(0o644)
        container_name = "interview-coach-" + uuid.uuid4().hex[:12]
        base = [
            "docker", "run", "--rm", "-i", "--pull", "never", "--network", "none", "--read-only",
            "--name", container_name, "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "512m" if language == "java" else "256m", "--cpus", "1", "--user", "65534:65534",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
            "--mount", f"type=bind,source={work},target=/work,readonly", "--workdir", "/work",
            image or IMAGES[language],
        ]
        if language == "python":
            command = base + ["python", "-I", "-S", "-c", HARNESS, "/work/solution.py"]
        else:
            command = base + ["sh", "-c", "javac -d /tmp /work/Solution.java /work/CoachHarness.java && java -cp /tmp CoachHarness"]
        return _execute(command, data, 12 if language == "java" else 5, container_name=container_name)
