"""국가법령정보 공동활용 API 기반 법령 메타데이터 모니터."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.parse
import urllib.request
import urllib.error

DEFAULTS = ['산업안전보건법', '산업안전보건법 시행령', '산업안전보건법 시행규칙', '산업안전보건기준에 관한 규칙']
ENDPOINT = 'https://www.law.go.kr/DRF/lawSearch.do'
FIELDS = ['법령ID', '법령일련번호', '법령명한글', '공포일자', '공포번호', '시행일자', '제개정구분명', '법령상세링크']


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS watch(name TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, checked_at TEXT NOT NULL,
        digest TEXT NOT NULL, payload TEXT NOT NULL);
    ''')
    return db


def fetch(name, status, oc):
    rows = []
    page = 1
    while True:
        params = dict(OC=oc, target='eflaw', type='JSON', query=name,
                      nw=status, display=100, page=page)
        request = urllib.request.Request(ENDPOINT + '?' + urllib.parse.urlencode(params),
                                         headers={'User-Agent': 'LawUpdateMonitor/0.1'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8-sig'))
        except (OSError, ValueError) as exc:
            # Request URLs contain OC. Do not include underlying exception text.
            raise RuntimeError('API 요청 실패: 인증값·네트워크 허용 설정·응답 형식을 확인하세요.') from None
        root = data.get('LawSearch') if isinstance(data, dict) else None
        if not isinstance(root, dict) or 'totalCnt' not in root:
            raise RuntimeError('예상한 LawSearch 응답이 아닙니다. API 이용 권한과 인증을 확인하세요.')
        try:
            total = int(root['totalCnt'])
        except (TypeError, ValueError):
            raise RuntimeError('API totalCnt 형식이 올바르지 않습니다.') from None
        laws = root.get('law', [])
        if isinstance(laws, dict):
            laws = [laws]
        if not isinstance(laws, list) or any(not isinstance(x, dict) for x in laws):
            raise RuntimeError('API 법령 목록 형식이 올바르지 않습니다.')
        if total and not laws:
            raise RuntimeError('API 결과가 불완전합니다. 기존 기준 데이터를 유지합니다.')
        for law in laws:
            if law.get('법령명한글') == name:
                row = {key: str(law.get(key, '')) for key in FIELDS}
                row['구분'] = '현행' if status == 3 else '시행예정'
                rows.append(row)
        if page * 100 >= total:
            return rows
        page += 1
        if page > 100:
            raise RuntimeError('API 조회 한도를 초과했습니다. 기존 기준 데이터를 유지합니다.')


def canonical(rows):
    return sorted(rows, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))


def record(db, name, rows):
    payload = json.dumps(canonical(rows), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    previous = db.execute('SELECT digest FROM snapshots WHERE name=? ORDER BY id DESC LIMIT 1', (name,)).fetchone()
    state = '기준 생성' if previous is None else ('변경 감지' if previous['digest'] != digest else '변경 없음')
    db.execute('INSERT INTO snapshots(name,checked_at,digest,payload) VALUES(?,?,?,?)',
               (name, dt.datetime.now(dt.timezone.utc).isoformat(), digest, payload))
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description='안전보건 법령 현행·시행예정 메타데이터 모니터')
    parser.add_argument('--db', default=os.environ.get('LAW_MONITOR_DB', str(Path.home() / '.local/share/law-monitor/laws.sqlite3')))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='기본 관심 법령 등록')
    sub.add_parser('list', help='관심 법령 목록')
    for command in ['add', 'remove']:
        sub.add_parser(command).add_argument('name')
    check = sub.add_parser('check', help='API 조회 및 이전 조회와 비교')
    check.add_argument('--csv', type=Path, help='조회 결과 CSV 저장')
    history = sub.add_parser('history', help='조회 및 변경 이력')
    history.add_argument('--name')
    history.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.command == 'init':
            db.executemany('INSERT OR IGNORE INTO watch VALUES(?)', [(x,) for x in DEFAULTS])
            db.commit()
            print('기본 관심 법령 4개 등록 완료')
        elif args.command == 'add':
            name = args.name.strip()
            if not name:
                raise RuntimeError('법령명을 입력하세요.')
            db.execute('INSERT OR IGNORE INTO watch VALUES(?)', (name,))
            db.commit()
            print('관심 법령 등록 완료')
        elif args.command == 'remove':
            db.execute('DELETE FROM watch WHERE name=?', (args.name,))
            db.commit()
            print('관심 법령 삭제 완료 (조회 이력 보존)')
        elif args.command == 'list':
            for row in db.execute('SELECT name FROM watch ORDER BY name'):
                print(row['name'])
        elif args.command == 'check':
            oc = os.environ.get('LAW_OC', '').strip()
            if not oc:
                raise RuntimeError('LAW_OC 환경 변수에 법제처 API 인증값을 설정하세요.')
            names = [r['name'] for r in db.execute('SELECT name FROM watch ORDER BY name')]
            if not names:
                raise RuntimeError('관심 법령이 없습니다. init 또는 add를 실행하세요.')
            # Fetch all results before committing any baseline changes.
            results = []
            for name in names:
                rows = fetch(name, 3, oc) + fetch(name, 2, oc)
                if not any(r['구분'] == '현행' for r in rows):
                    raise RuntimeError(f'{name}: 현행 법령 일치 결과가 없습니다. 법령명·응답을 확인하세요. 기준은 변경하지 않았습니다.')
                results.append((name, rows))
            with db:
                for name, rows in results:
                    print(f'{name}: {record(db, name, rows)} (현행·시행예정 {len(rows)}건)')
            if args.csv:
                with args.csv.open('w', encoding='utf-8-sig', newline='') as output:
                    writer = csv.DictWriter(output, fieldnames=['관심법령', '구분'] + FIELDS)
                    writer.writeheader()
                    for name, rows in results:
                        for row in rows:
                            # Prevent spreadsheet formulas in externally supplied data.
                            safe = {k: "'" + v if v.startswith(('=', '+', '-', '@')) else v for k, v in dict(row, 관심법령=name).items()}
                            writer.writerow(safe)
                print(f'CSV 저장: {args.csv}')
        elif args.command == 'history':
            if args.limit < 1:
                raise RuntimeError('--limit은 1 이상이어야 합니다.')
            query = 'SELECT name,checked_at,payload FROM snapshots'
            params = []
            if args.name:
                query += ' WHERE name=?'
                params.append(args.name)
            query += ' ORDER BY id DESC LIMIT ?'
            params.append(args.limit)
            for row in db.execute(query, params):
                print(json.dumps(dict(row), ensure_ascii=False))
        return 0
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
