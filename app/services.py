import difflib
import fcntl
import hashlib
import json
import sqlite3
from pathlib import Path

from .database import connect, latest_body, rows, utcnow
from .law_api import CollectionError


class BusyError(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Monitor:
    def __init__(self, path, api):
        self.path, self.api = str(path), api

    def reserve(self):
        lock = open(self.path + '.lock', 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise BusyError('이미 조회가 진행 중입니다.') from None
        try:
            with connect(self.path) as db:
                db.execute("UPDATE jobs SET status='중단', finished_at=?, message='이전 실행이 중단되었습니다. 다시 조회하세요.' WHERE status='진행 중'", (utcnow(),))
                job_id = db.execute("INSERT INTO jobs(started_at,status) VALUES(?,'진행 중')", (utcnow(),)).lastrowid
            return job_id, lock
        except Exception:
            lock.close()
            raise

    def run(self, job_id, lock):
        failures = 0
        try:
            with connect(self.path) as db:
                laws = rows(db, 'SELECT * FROM laws WHERE active=1 ORDER BY id')
            for law in laws:
                try:
                    message = self.collect(law)
                    status = '성공'
                except (CollectionError, sqlite3.Error, OSError) as exc:
                    failures += 1
                    status = '실패'
                    message = str(exc) if isinstance(exc, CollectionError) else '저장 작업 실패. 디스크와 데이터베이스 상태를 확인하세요.'
                except Exception:
                    failures += 1
                    status, message = '실패', '예상하지 못한 수집 오류입니다. 기존 데이터는 유지됩니다.'
                with connect(self.path) as db:
                    db.execute('INSERT INTO runs(job_id,law_id,checked_at,status,message) VALUES(?,?,?,?,?)',
                               (job_id, law['id'], utcnow(), status, message))
            with connect(self.path) as db:
                db.execute('UPDATE jobs SET status=?,finished_at=?,message=? WHERE id=?',
                           ('일부 실패' if failures else '완료', utcnow(), f'{len(laws)}개 법령 / 실패 {failures}개', job_id))
        finally:
            lock.close()

    def collect(self, law):
        versions = self.api.versions(law['name'], law['official_id'])
        if not versions or len({v['version_key'] for v in versions}) != len(versions):
            raise CollectionError('버전 목록이 없거나 중복되었습니다.')
        # Re-fetch all bodies, including saved versions, to detect official corrections.
        # Fetching can fail without advancing any stored baseline for this law.
        bodies = [(version, self.api.body(version)) for version in versions]
        now = utcnow()
        changes = 0
        with connect(self.path) as db:
            baseline = db.execute('SELECT last_success FROM laws WHERE id=?', (law['id'],)).fetchone()['last_success'] is None
            db.execute("UPDATE versions SET category='unconfirmed' WHERE law_id=?", (law['id'],))
            for version, body in bodies:
                content_hash = digest(dict(articles=body['articles'], supplementary=body['supplementary']))
                old = db.execute('SELECT * FROM versions WHERE law_id=? AND version_key=?', (law['id'], version['version_key'])).fetchone()
                prior_body = latest_body(db, old['id']) if old else None
                params = [version[k] for k in ('version_key', 'mst', 'source', 'category', 'promulgated', 'effective', 'revision', 'number', 'official_url')]
                db.execute('''INSERT INTO versions(law_id,version_key,mst,source,category,promulgated,effective,revision,number,official_url,digest,first_seen,last_seen)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(law_id,version_key) DO UPDATE SET
                  source=excluded.source,category=excluded.category,promulgated=excluded.promulgated,
                  effective=excluded.effective,revision=excluded.revision,number=excluded.number,
                  official_url=excluded.official_url,digest=excluded.digest,last_seen=excluded.last_seen''',
                  (law['id'], *params, content_hash, now, now))
                vid = db.execute('SELECT id FROM versions WHERE law_id=? AND version_key=?', (law['id'], version['version_key'])).fetchone()['id']
                if prior_body is None or prior_body['digest'] != content_hash:
                    after_id = db.execute('INSERT INTO bodies(version_id,digest,collected_at,articles,supplementary,raw) VALUES(?,?,?,?,?,?)',
                        (vid, content_hash, now, json.dumps(body['articles'], ensure_ascii=False),
                         json.dumps(body['supplementary'], ensure_ascii=False), json.dumps(body['raw'], ensure_ascii=False))).lastrowid
                else:
                    after_id = prior_body['id']
                if not baseline and (old is None or old['digest'] != content_hash):
                    kind = '새 버전 발견' if old is None else '동일 버전 본문 변경'
                    db.execute('INSERT INTO changes(law_id,version_id,kind,detected_at,before_body_id,after_body_id) VALUES(?,?,?,?,?,?)',
                               (law['id'], vid, kind, now, prior_body['id'] if prior_body else None, after_id))
                    changes += 1
            db.execute('UPDATE laws SET last_success=?,official_id=? WHERE id=?', (now, versions[0]['official_id'], law['id']))
        return f'기준 데이터 등록 ({len(versions)}개 버전)' if baseline else f'{len(versions)}개 버전 확인 / 변경 {changes}건'


def compare(before, after):
    left = {a['key']: a for a in before['articles']}
    right = {a['key']: a for a in after['articles']}
    result = []
    # Preserve article order, with removed articles included at the end.
    for key in dict.fromkeys([*right, *left]):
        a, b = left.get(key), right.get(key)
        if a == b:
            kind = '변경 없음'
        elif a is None:
            kind = '신설'
        elif b is None:
            kind = '삭제'
        else:
            kind = '수정'
        old_text = a['text'] if a else ''
        new_text = b['text'] if b else ''
        result.append(dict(key=key, label=(b or a)['label'], kind=kind,
                           before=a, after=b,
                           diff='\n'.join(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), fromfile='이전 선택본', tofile='이후 선택본', lineterm=''))))
    return dict(articles=result, supplementary=dict(before=before['supplementary'], after=after['supplementary'],
                changed=before['supplementary'] != after['supplementary']),
                warning='조문 번호를 기준으로 비교합니다. 번호 이동·분할·통합은 원문 확인이 필요합니다. 부칙의 적용례·경과조치와 조문별 시행일을 함께 확인하세요.')
