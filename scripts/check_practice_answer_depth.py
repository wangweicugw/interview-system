from __future__ import annotations
import argparse,json,re
from pathlib import Path

def compact(s):
    return re.sub(r'\s+', '', s or '')

def inspect(root,case=None):
    paths=[Path(case)/'practice-answers.json'] if case else sorted((root/'interviews').glob('*/practice-answers.json'))
    report=[]
    for path in paths:
        if not path.exists():continue
        data=json.loads(path.read_text())
        for item in data.get('questions',[]):
            a=item.get('reference_answer') or {}
            steps=a.get('steps') or []
            short=next((compact(x.get('body')) for x in steps if '30' in x.get('heading','')),'')
            full=next((compact(x.get('body')) for x in steps if '2 分钟' in x.get('heading','')),'')
            if not short or not full:continue
            flags=[]
            if len(full)<=len(short):flags.append('two_minute_not_longer')
            if len(full)<150:flags.append('two_minute_under_150_chars')
            if full.startswith(('补充','展开','另列','举例说明','用一个例子')):flags.append('writing_instruction')
            if short and (short in full or full in short) and abs(len(full)-len(short))<80:flags.append('near_duplicate')
            if flags:report.append({'case':path.parent.name,'question_id':item.get('question_id'),'short_chars':len(short),'full_chars':len(full),'flags':flags})
    return report

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--case',type=Path);args=ap.parse_args()
    report=inspect(args.root,args.case)
    print(json.dumps({'flagged':len(report),'items':report},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
