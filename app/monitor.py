"""외부 스케줄러에서 실행: python -m app.monitor"""
import argparse
from .database import default_path, initialize, connect, rows
from .law_api import LawAPI
from .services import Monitor, BusyError


def main():
    parser = argparse.ArgumentParser(description='관심 법령 정기 조회 (웹 DB 공유)')
    parser.add_argument('--db', default=default_path())
    args = parser.parse_args()
    initialize(args.db)
    with connect(args.db) as db:
        if db.execute("SELECT 1 FROM settings WHERE key='demo'").fetchone():
            print('예시 DB는 정기 조회할 수 없습니다.')
            return 1
    monitor = Monitor(args.db, LawAPI())
    try:
        job, lock = monitor.reserve()
    except BusyError as exc:
        print(exc)
        return 2
    monitor.run(job, lock)
    with connect(args.db) as db:
        for row in rows(db, 'SELECT l.name,r.status,r.message FROM runs r JOIN laws l ON l.id=r.law_id WHERE job_id=?', (job,)):
            print(f"{row['name']}: {row['status']} / {row['message']}")
        status = db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()['status']
    return 0 if status == '완료' else 1


if __name__ == '__main__':
    raise SystemExit(main())
