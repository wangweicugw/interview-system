"""Run an already installed local ASR engine and import its raw result."""

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

from check_data_compatibility import require_store_compatible

def executable_for(engine: str, supplied: Path | None) -> Path:
    if supplied:
        path = supplied.expanduser().resolve()
    else:
        name = "mlx_whisper" if engine == "mlx-whisper" else "whisperx"
        sibling = Path(sys.executable).parent / name
        found = str(sibling) if sibling.is_file() else shutil.which(name)
        if not found:
            raise RuntimeError(f"未找到本地 {name} 可执行程序；请先在独立环境安装，或指定 --engine-executable")
        path = Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"引擎不可执行：{path}")
    return path


def local_model(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_dir():
        return path
    if "/" in value and not value.startswith(("/", ".", "~")):
        cache = Path.home() / ".cache" / "huggingface" / "hub"
        snapshots = cache / ("models--" + value.replace("/", "--")) / "snapshots"
        available = sorted((item for item in snapshots.glob("*") if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
        if available:
            return available[0]
    raise RuntimeError(f"{label} 必须是已下载的本地模型目录或已缓存的模型 ID；不会自动下载：{value}")


def make_command(args: argparse.Namespace, executable: Path, output: Path) -> list[str]:
    model = local_model(args.model, "--model")
    command = [str(executable), str(args.audio), "--model", str(model)]
    if args.engine == "mlx-whisper":
        if args.diarize:
            raise RuntimeError("MLX Whisper 不执行说话人分离；请改用 --engine whisperx --diarize")
        command += ["--output-dir", str(output), "--output-format", "json", "--verbose", "False"]
    else:
        command += [
            "--output_dir", str(output), "--output_format", "json",
            "--device", args.device, "--compute_type", args.compute_type,
            "--model_cache_only", "True", "--print_progress", "True",
        ]
        if args.align_model:
            command += ["--align_model", str(local_model(args.align_model, "--align-model"))]
        else:
            command += ["--no_align"]
        if args.diarize:
            if not args.diarize_model:
                raise RuntimeError("--diarize 需要 --diarize-model 指向已下载的 pyannote 模型目录")
            if not args.align_model:
                raise RuntimeError("--diarize 需要 --align-model 提供本地词级对齐模型，以保留说话人切换")
            diarize_model = local_model(args.diarize_model, "--diarize-model")
            command += ["--diarize", "--diarize_model", str(diarize_model)]
            if args.min_speakers:
                command += ["--min_speakers", str(args.min_speakers)]
            if args.max_speakers:
                command += ["--max_speakers", str(args.max_speakers)]
    if args.language:
        command += ["--language", args.language]
    return command


def find_result(output: Path) -> Path:
    files = list(output.rglob("*.json"))
    if len(files) != 1:
        raise RuntimeError(f"识别引擎应产生 1 个 JSON，实际为 {len(files)} 个")
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list) or not payload["segments"]:
        raise RuntimeError("识别引擎未产生非空 segments；原始输出不会导入")
    return files[0]


def text_view(raw: Path, target: Path) -> None:
    payload = json.loads(raw.read_text(encoding="utf-8"))
    lines = []
    for item in payload["segments"]:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("start")
        stamp = f"[{start:.2f}s] " if isinstance(start, (float, int)) else ""
        speaker = f"{item['speaker']}：" if item.get("speaker") else ""
        lines.append(stamp + speaker + text)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="用已安装、已缓存的本地引擎转写并导入录音")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--engine", choices=("mlx-whisper", "whisperx"), default="mlx-whisper")
    parser.add_argument("--engine-executable", type=Path)
    parser.add_argument("--model", help="已下载模型目录或 Hugging Face 本地缓存 ID")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--diarize-model", help="已下载 pyannote 模型的本地目录")
    parser.add_argument("--align-model", help="可选的本地词级对齐模型；缺省为片段时间戳")
    parser.add_argument("--nltk-data", type=Path, help="WhisperX 对齐所需的本地 NLTK 数据目录")
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--compute-type", choices=("int8", "float32", "float16"), default="int8")
    parser.add_argument("--company")
    parser.add_argument("--role")
    parser.add_argument("--round", dest="round_name")
    parser.add_argument("--date")
    parser.add_argument("--jd", type=Path)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    args.audio = args.audio.expanduser().resolve()
    if not (root / "config.json").is_file():
        raise RuntimeError("数据目录尚未初始化")
    require_store_compatible(root)
    if not args.audio.is_file():
        raise RuntimeError(f"音频不存在：{args.audio}")
    if args.min_speakers and args.max_speakers and args.min_speakers > args.max_speakers:
        raise RuntimeError("最少说话人数不能大于最多说话人数")
    if not args.model:
        if args.engine == "whisperx":
            raise RuntimeError("WhisperX 需通过 --model 指定已缓存模型")
        args.model = "mlx-community/whisper-large-v3-turbo-q4"
    executable = executable_for(args.engine, args.engine_executable)
    offline_env = os.environ.copy()
    offline_env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"})
    nltk_data = args.nltk_data or root / "models" / "nltk_data"
    if args.nltk_data and not nltk_data.is_dir():
        raise RuntimeError(f"NLTK 数据目录不存在：{nltk_data}")
    if nltk_data.is_dir():
        current = offline_env.get("NLTK_DATA")
        offline_env["NLTK_DATA"] = str(nltk_data.resolve()) + (os.pathsep + current if current else "")
    with tempfile.TemporaryDirectory(prefix=".local-asr-", dir=root) as temporary:
        output = Path(temporary)
        command = make_command(args, executable, output)
        print(json.dumps({"phase": "recognizing", "engine": args.engine}, ensure_ascii=False), flush=True)
        started = time.monotonic()
        with (output / "engine.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, env=offline_env, text=True, stdout=log, stderr=subprocess.STDOUT)
            try:
                while True:
                    try:
                        returncode = process.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        print(json.dumps({"phase": "recognizing", "elapsed_seconds": int(time.monotonic() - started)}, ensure_ascii=False), flush=True)
            except KeyboardInterrupt:
                process.terminate()
                process.wait(timeout=10)
                raise
        if returncode:
            logs = root / "logs"
            logs.mkdir(mode=0o700, exist_ok=True)
            saved_log = logs / f"local-asr-{uuid.uuid4().hex[:12]}.log"
            shutil.copy2(output / "engine.log", saved_log)
            saved_log.chmod(0o600)
            raise RuntimeError(f"本地识别失败（退出码 {returncode}）；引擎日志仅保存在本机：{saved_log}")
        raw = find_result(output)
        txt = output / "transcription-view.txt"
        text_view(raw, txt)
        importer = Path(__file__).with_name("ingest_audio_transcript.py")
        import_command = [
            sys.executable, str(importer), "--root", str(root), "--audio", str(args.audio),
            "--transcript", str(raw), "--raw-output", str(txt),
            "--engine", args.engine, "--model", args.model, "--language", args.language or "unknown",
        ]
        if args.diarize:
            import_command += ["--speaker-method", "diarization"]
        for option, value in (
            ("--company", args.company), ("--role", args.role),
            ("--round", args.round_name), ("--date", args.date),
            ("--jd", args.jd), ("--resume", args.resume),
        ):
            if value is not None:
                import_command += [option, str(value)]
        imported = subprocess.run(import_command, text=True, capture_output=True, check=False)
        if imported.returncode:
            raise RuntimeError("识别已完成，但导入失败；请检查转写 JSON 格式及导入参数")
        print(imported.stdout.strip().splitlines()[-1])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
