"""Offline ASR installation check and short real-audio acceptance test."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from transcribe_local_audio import find_result, local_model, make_command
from common import open_private_text


def defaults(root: Path, args: argparse.Namespace) -> None:
    if args.engine == "whisperx":
        args.engine_executable = args.engine_executable or root / ".venv-whisperx" / "bin" / "whisperx"
        args.model = args.model or str(root / "models" / "faster-whisper-large-v3-turbo")
        args.align_model = args.align_model or str(root / "models" / "chinese-wav2vec2-align")
        args.diarize_model = args.diarize_model or str(root / "models" / "pyannote-speaker-diarization-community-1")
    else:
        args.engine_executable = args.engine_executable or root / ".venv" / "bin" / "mlx_whisper"
        args.model = args.model or "mlx-community/whisper-large-v3-turbo-q4"


def readiness(args: argparse.Namespace) -> dict:
    checks: dict[str, dict] = {}
    executable = args.engine_executable.expanduser().resolve()
    checks["engine"] = {"ready": executable.is_file() and os.access(executable, os.X_OK), "path": str(executable)}
    for key, value in (("model", args.model), ("align_model", args.align_model if args.engine == "whisperx" else None),
                       ("diarize_model", args.diarize_model if args.engine == "whisperx" and args.diarize else None)):
        if not value:
            continue
        try:
            path = local_model(value, key)
            checks[key] = {"ready": True, "path": str(path)}
        except RuntimeError as error:
            checks[key] = {"ready": False, "reason": str(error)}
    if args.engine == "whisperx" and args.diarize:
        nltk = args.nltk_data or args.root / "models" / "nltk_data"
        checks["nltk_data"] = {"ready": nltk.is_dir(), "path": str(nltk)}
    checks["ffprobe"] = {"ready": bool(shutil.which("ffprobe"))}
    return {"status": "ready" if all(item["ready"] for item in checks.values()) else "incomplete",
            "engine": args.engine, "diarize": args.diarize, "checks": checks,
            "actual_recognition_tested": False}


def duration(audio: Path) -> float:
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
    if result.returncode:
        raise RuntimeError("ffprobe 无法读取音频；请提供 5–30 秒的真实语音片段")
    try:
        seconds = float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError("无法读取音频时长") from error
    if not 1 <= seconds <= 30:
        raise RuntimeError("实测音频须为 1–30 秒；请截取含人声的短片段")
    return seconds


def smoke(args: argparse.Namespace) -> dict:
    state = readiness(args)
    if state["status"] != "ready":
        return state
    if not args.audio or not args.audio.is_file():
        raise RuntimeError("实测需要 --audio 指定本地真实语音片段")
    seconds = duration(args.audio)
    with tempfile.TemporaryDirectory(prefix="asr-smoke-") as temporary:
        output = Path(temporary)
        command = make_command(args, args.engine_executable.resolve(), output)
        env = os.environ.copy()
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"})
        nltk = args.nltk_data or args.root / "models" / "nltk_data"
        if nltk.is_dir():
            env["NLTK_DATA"] = str(nltk.resolve())
        started = time.monotonic()
        with open_private_text(output / "engine.log") as log:
            try:
                result = subprocess.run(command, text=True, stdout=log, stderr=subprocess.STDOUT,
                                        env=env, timeout=args.timeout, check=False)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"实测超过 {args.timeout} 秒；请检查本机资源和引擎日志") from error
        if result.returncode:
            logs = args.log_dir or args.root / "logs"
            logs.mkdir(mode=0o700, exist_ok=True)
            saved = logs / f"asr-smoke-{uuid.uuid4().hex[:12]}.log"
            shutil.copy2(output / "engine.log", saved)
            saved.chmod(0o600)
            raise RuntimeError(f"本地语音实测失败（退出码 {result.returncode}），日志：{saved}")
        try:
            raw = find_result(output)
        except RuntimeError as error:
            return {"status": "failed", "engine": args.engine, "diarize": args.diarize,
                    "audio_seconds": round(seconds, 2), "actual_recognition_tested": True,
                    "reason": str(error)}
        payload = json.loads(raw.read_text(encoding="utf-8"))
        segments = payload["segments"]
        nonempty = sum(bool(str(item.get("text") or "").strip()) for item in segments)
        speakers = {str(item["speaker"]) for item in segments if item.get("speaker")}
        word_speakers = {str(word["speaker"]) for item in segments for word in item.get("words", [])
                         if isinstance(word, dict) and word.get("speaker")}
        speakers |= word_speakers
        timestamps = sum(isinstance(item.get("start"), (int, float)) and isinstance(item.get("end"), (int, float))
                         for item in segments)
        passed = nonempty > 0 and timestamps > 0 and (not args.diarize or bool(speakers))
        return {"status": "passed" if passed else "failed", "engine": args.engine,
                "diarize": args.diarize, "audio_seconds": round(seconds, 2),
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "segments": len(segments), "nonempty_segments": nonempty,
                "timestamped_segments": timestamps, "speaker_labels": sorted(speakers),
                "actual_recognition_tested": True,
                "note": "仅验证真实本机推理及声学标签；说话人身份和识别准确率仍需人工核对。"}


def installation_plan(args: argparse.Namespace) -> dict:
    """Return an explicit local plan without installing packages or downloading models."""
    state = readiness(args)
    missing = [name for name, value in state["checks"].items() if not value.get("ready")]
    if args.engine == "whisperx":
        venv = args.root / ".venv-whisperx"
        commands = [
            ["python3.12", "-m", "venv", str(venv)],
            [str(venv / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"],
            [str(venv / "bin" / "python"), "-m", "pip", "install", "whisperx==3.8.6", "pyannote.audio==4.0.7", "huggingface_hub"],
            [str(venv / "bin" / "hf"), "download", "h2oai/faster-whisper-large-v3-turbo", "--local-dir", str(args.root / "models" / "faster-whisper-large-v3-turbo")],
            [str(venv / "bin" / "hf"), "download", "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn", "--local-dir", str(args.root / "models" / "chinese-wav2vec2-align")],
        ]
        if args.diarize:
            commands.extend([
                [str(venv / "bin" / "hf"), "download", "pyannote/speaker-diarization-community-1", "--local-dir", str(args.root / "models" / "pyannote-speaker-diarization-community-1")],
                [str(venv / "bin" / "python"), "-m", "nltk.downloader", "-d", str(args.root / "models" / "nltk_data"), "punkt_tab"],
            ])
    else:
        venv = args.root / ".venv"
        commands = [
            ["python3", "-m", "venv", str(venv)],
            [str(venv / "bin" / "python"), "-m", "pip", "install", "mlx-whisper"],
        ]
    return {
        "status": "plan",
        "engine": args.engine,
        "diarize": args.diarize,
        "missing": missing,
        "commands": commands if missing else [],
        "requires_authorization": bool(missing),
        "notes": [
            "此计划未执行任何安装、下载或登录。",
            "WhisperX/pyannote 模型较大；下载前确认网络、磁盘和模型条款。",
            "pyannote 受限模型需用户自行接受条款并完成本机登录，不得把令牌放入命令或聊天。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="本地语音安装检查与短音频实测，不导入面试档案")
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "smoke", "plan"):
        item = sub.add_parser(name)
        item.add_argument("--engine", choices=("mlx-whisper", "whisperx"), default="whisperx")
        item.add_argument("--engine-executable", type=Path)
        item.add_argument("--model")
        item.add_argument("--align-model")
        item.add_argument("--diarize-model")
        item.add_argument("--nltk-data", type=Path)
        item.add_argument("--diarize", action="store_true")
        item.add_argument("--language", default="zh")
        item.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
        item.add_argument("--compute-type", choices=("int8", "float32", "float16"), default="int8")
        item.add_argument("--min-speakers", type=int)
        item.add_argument("--max-speakers", type=int)
        item.add_argument("--audio", type=Path)
        item.add_argument("--timeout", type=int, default=900)
        item.add_argument("--log-dir", type=Path, help="实测失败时的本地日志目录")
    args = parser.parse_args()
    args.root = args.root.expanduser().resolve()
    if not args.root.is_dir():
        raise RuntimeError("本地数据目录不存在")
    if args.min_speakers and args.max_speakers and args.min_speakers > args.max_speakers:
        raise RuntimeError("最少说话人数不能大于最多说话人数")
    if args.timeout < 1 or args.timeout > 3600:
        raise RuntimeError("--timeout 须在 1–3600 秒之间")
    if args.audio:
        args.audio = args.audio.expanduser().resolve()
    defaults(args.root, args)
    if args.command == "check":
        result = readiness(args)
    elif args.command == "plan":
        result = installation_plan(args)
    else:
        result = smoke(args)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ready", "passed", "plan"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
