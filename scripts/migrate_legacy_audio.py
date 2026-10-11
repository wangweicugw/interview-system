#!/usr/bin/env python3
"""Record provenance for text-first cases with later supplemental local audio."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import now_iso, read_json, read_supported_text, sha256_file, sha256_text, write_json


def private_file(case: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or path.parts[0] != 'private':
        raise ValueError(f'文件必须位于 case/private：{relative}')
    resolved = (case / path).resolve()
    resolved.relative_to(case)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def text_digest(path: Path) -> str:
    text = read_supported_text(path, allow_pdf=False)
    normalized = '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').split('\n')).strip()
    return sha256_text(normalized)


def main() -> int:
    parser = argparse.ArgumentParser(description='迁移文字稿先导入、音频后补充的旧场次元数据')
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument(
        '--timeline-exception', nargs=3, action='append', default=[],
        metavar=('PREVIOUS_SEGMENT', 'SEGMENT', 'REASON'),
        help='明确登记原文字稿中相邻片段的时间戳回退，不修改原文',
    )
    args = parser.parse_args()
    case = args.case.expanduser().resolve()
    manifest = read_json(case / 'manifest.json')
    raw = read_json(case / 'transcript.raw.json')
    if manifest.get('legacy_audio_migration'):
        raise RuntimeError('该场已登记旧格式迁移；不会重复执行')
    audio = manifest.get('inputs', {}).get('audio')
    if not isinstance(audio, dict):
        raise RuntimeError('该场没有已登记的音频')
    original = private_file(case, manifest['inputs']['transcript_original'])
    if text_digest(original) != raw.get('source_sha256') or raw.get('source_sha256') != manifest.get('transcript_sha256'):
        raise RuntimeError('原始文字稿与原始逐字稿来源哈希不一致')
    audio_file = private_file(case, audio['original'])
    if sha256_file(audio_file) != audio.get('sha256'):
        raise RuntimeError('音频文件哈希与 manifest 不一致')

    legacy = manifest.get('transcription') or audio.get('transcription')
    if not isinstance(legacy, dict):
        raise RuntimeError('缺少旧版本地转写元数据')
    primary = legacy.get('primary_raw') or legacy.get('raw_json') or legacy.get('primary_json')
    outputs = legacy.get('raw_outputs') or [
        legacy[field] for field in ('raw_json', 'raw_srt', 'primary_json', 'primary_txt', 'srt', 'vtt')
        if isinstance(legacy.get(field), str) and legacy[field]
    ]
    outputs = list(dict.fromkeys(outputs))
    if not isinstance(primary, str) or primary not in outputs or not outputs:
        raise RuntimeError('无法从旧元数据确认主要原始转写和输出清单')
    output_hashes = {relative: sha256_file(private_file(case, relative)) for relative in outputs}
    supporting = {}
    acoustic = legacy.get('acoustic_role_analysis')
    if isinstance(acoustic, str) and acoustic:
        supporting[acoustic] = sha256_file(private_file(case, acoustic))

    segments = raw.get('segments', [])
    reversals = []
    previous = None
    for segment in segments:
        start = segment.get('start')
        if previous and isinstance(start, (int, float)) and isinstance(previous.get('start'), (int, float)) and start < previous['start']:
            reversals.append((previous, segment))
        if isinstance(start, (int, float)):
            previous = segment
    requested = {(before, after): reason for before, after, reason in args.timeline_exception}
    actual = {(a['segment_id'], b['segment_id']) for a, b in reversals}
    if actual != set(requested) or any(not reason.strip() for reason in requested.values()):
        raise RuntimeError(f'时间戳例外与实际回退不一致：实际 {sorted(actual)}，指定 {sorted(requested)}')
    exceptions = [
        {
            'previous_segment_id': before['segment_id'],
            'segment_id': after['segment_id'],
            'previous_start': before['start'],
            'start': after['start'],
            'reason': requested[(before['segment_id'], after['segment_id'])],
        }
        for before, after in reversals
    ]

    backup = case / 'analysis-history' / 'manifest-before-legacy-audio-migration.json'
    backup.parent.mkdir(exist_ok=True)
    if backup.exists():
        raise RuntimeError(f'备份已存在，停止以避免覆盖：{backup}')
    shutil.copy2(case / 'manifest.json', backup)

    audio['processing'] = 'local_only'
    audio['role'] = 'supplemental_reference'
    transcription = dict(legacy)
    transcription.update({
        'engine': legacy['engine'],
        'model': legacy['model'],
        'language': legacy['language'],
        'primary_raw': primary,
        'raw_outputs': outputs,
        'speaker_method': 'none',
        'speaker_confidence': None,
        'prompt': None,
    })
    manifest['transcription'] = transcription
    manifest['legacy_audio_migration'] = {
        'kind': 'text_first_audio_supplement_v1',
        'migrated_at': now_iso(),
        'raw_file_sha256': sha256_file(case / 'transcript.raw.json'),
        'original_file_sha256': sha256_file(original),
        'raw_output_sha256': output_hashes,
        'supporting_artifact_sha256': supporting,
        'timeline_exceptions': exceptions,
    }
    warning = '原始逐字稿来自文字稿；录音和本地转写是后补核对证据，未覆盖原始逐字稿。'
    manifest.setdefault('warnings', [])
    if warning not in manifest['warnings']:
        manifest['warnings'].append(warning)
    if exceptions:
        manifest['warnings'].append('原文字稿有已登记的时间戳回退；保留原始时间，按片段 ID 追溯。')
    manifest['updated_at'] = now_iso()
    write_json(case / 'manifest.json', manifest)
    print(f'case={manifest["case_id"]} supplemental_audio=true timeline_exceptions={len(exceptions)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
