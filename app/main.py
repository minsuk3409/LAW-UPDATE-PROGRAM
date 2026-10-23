import csv
import io
import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import connect, default_path, initialize, latest_body, rows, utcnow
from .law_api import CollectionError, LawAPI, scalar
from .services import BusyError, Monitor, compare

STATIC = Path(__file__).parent / 'static'
CATEGORIES = {'previous': '개정 이전', 'current': '현행', 'upcoming': '시행예정', 'unconfirmed': '최신 목록 미확인'}


class LawInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    official_id: str | None = Field(default=None, pattern=r'^\d{1,20}$')


class ReviewInput(BaseModel):
    status: str
    memo: str = Field(default='', max_length=5000)


def create_app(db_path=None, api=None, demo=False):
    path = str(db_path or default_path())
    if demo and db_path is None:
        path = str(Path(path).with_name('demo.sqlite3'))
    initialize(path)
    with connect(path) as db:
        saved_demo = db.execute("SELECT 1 FROM settings WHERE key='demo'").fetchone() is not None
        if saved_demo and not demo:
            raise RuntimeError('예시 DB는 --demo 모드에서만 열 수 있습니다.')
    if demo:
        from .demo import DemoAPI, seed
        seed(path)
        api = api or DemoAPI()
    api = api or LawAPI()
    monitor = Monitor(path, api)
    app = FastAPI(title='안전보건 법령 모니터', docs_url='/api/docs')
    app.state.db_path, app.state.monitor = path, monitor

    @app.middleware('http')
    async def protect_requests(request: Request, call_next):
        # JSON/custom-header mutations cannot be submitted by cross-site HTML forms.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if request.headers.get('x-law-monitor') != '1':
                return JSONResponse({'detail': '화면을 새로고침한 후 다시 시도하세요.'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': '다른 사이트에서의 요청은 허용되지 않습니다.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(CollectionError)
    async def collection_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/api/health')
    def health():
        with connect(path) as db:
            db.execute('SELECT 1').fetchone()
        return {'status': 'ok', 'demo': demo}

    @app.get('/api/dashboard')
    def dashboard():
        with connect(path) as db:
            laws = rows(db, '''SELECT l.*,
                (SELECT count(*) FROM versions v WHERE v.law_id=l.id AND category='upcoming') AS upcoming_count,
                (SELECT count(*) FROM changes c WHERE c.law_id=l.id AND review_status!='검토 완료') AS pending_count,
                (SELECT message FROM runs r WHERE r.law_id=l.id ORDER BY r.id DESC LIMIT 1) AS last_message,
                (SELECT status FROM runs r WHERE r.law_id=l.id ORDER BY r.id DESC LIMIT 1) AS last_status
                FROM laws l WHERE active=1 ORDER BY id''')
            changes = rows(db, '''SELECT c.*, l.name, v.effective, v.category FROM changes c
                JOIN laws l ON l.id=c.law_id JOIN versions v ON v.id=c.version_id
                WHERE l.active=1 ORDER BY c.id DESC LIMIT 100''')
            jobs = rows(db, 'SELECT * FROM jobs ORDER BY id DESC LIMIT 10')
            runs = rows(db, '''SELECT r.*,l.name FROM runs r JOIN laws l ON l.id=r.law_id ORDER BY r.id DESC LIMIT 50''')
        return dict(demo=demo, credential_configured=demo or bool(getattr(api, 'oc', True)), laws=laws,
                    changes=changes, jobs=jobs, runs=runs, server_time=utcnow())

    @app.get('/api/search')
    def search(q: str):
        query = q.strip()
        if not query or len(query) > 150:
            raise HTTPException(400, '검색어를 1~150자로 입력하세요.')
        result = api.search(query)
        unique = {}
        for row in result:
            name, identity = scalar(row.get('법령명한글')), scalar(row.get('법령ID'))
            if name and identity.isdigit():
                unique[identity] = dict(name=name, official_id=identity)
        return list(unique.values())

    @app.post('/api/laws')
    def add_law(data: LawInput):
        name = data.name.strip()
        if not name:
            raise HTTPException(400, '법령명을 입력하세요.')
        if demo:
            raise HTTPException(400, '예시 모드에서는 관심 법령을 추가할 수 없습니다. 실제 모드로 시작하세요.')
        with connect(path) as db:
            old = db.execute('SELECT * FROM laws WHERE name=?', (name,)).fetchone()
            if old and old['official_id'] and data.official_id and old['official_id'] != data.official_id:
                raise HTTPException(409, '저장된 법령 ID와 다릅니다. 공식 법령명을 확인하세요.')
            db.execute('INSERT INTO laws(name,official_id) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET active=1, official_id=COALESCE(laws.official_id,excluded.official_id)', (name, data.official_id))
            law_id = db.execute('SELECT id FROM laws WHERE name=?', (name,)).fetchone()['id']
        return {'id': law_id}

    @app.delete('/api/laws/{law_id}')
    def remove_law(law_id: int):
        if demo:
            raise HTTPException(400, '예시 모드에서는 관심 법령을 삭제할 수 없습니다.')
        with connect(path) as db:
            if not db.execute('SELECT 1 FROM laws WHERE id=? AND active=1', (law_id,)).fetchone():
                raise HTTPException(404, '관심 법령을 찾을 수 없습니다.')
            db.execute('UPDATE laws SET active=0 WHERE id=?', (law_id,))
        return {'message': '관심 목록에서 제외했습니다. 기존 이력은 보존됩니다.'}

    @app.post('/api/check', status_code=202)
    def check(background: BackgroundTasks):
        try:
            job_id, lock = monitor.reserve()
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from None
        background.add_task(monitor.run, job_id, lock)
        return {'job_id': job_id}

    @app.get('/api/laws/{law_id}')
    def detail(law_id: int):
        with connect(path) as db:
            law = db.execute('SELECT * FROM laws WHERE id=?', (law_id,)).fetchone()
            if not law:
                raise HTTPException(404, '법령을 찾을 수 없습니다.')
            versions = rows(db, 'SELECT * FROM versions WHERE law_id=? ORDER BY effective DESC,promulgated DESC,id DESC', (law_id,))
            for version in versions:
                version['category_label'] = CATEGORIES[version['category']]
            runs = rows(db, 'SELECT * FROM runs WHERE law_id=? ORDER BY id DESC LIMIT 20', (law_id,))
        return dict(law=dict(law), versions=versions, runs=runs)

    @app.get('/api/versions/{version_id}')
    def version_detail(version_id: int):
        with connect(path) as db:
            version = db.execute('SELECT * FROM versions WHERE id=?', (version_id,)).fetchone()
            if not version:
                raise HTTPException(404, '버전을 찾을 수 없습니다.')
            body = latest_body(db, version_id)
        if not body:
            raise HTTPException(404, '저장된 본문이 없습니다.')
        return dict(version=dict(version), body=body)

    @app.get('/api/compare')
    def comparison(before: int, after: int):
        if before == after:
            raise HTTPException(400, '서로 다른 두 버전을 선택하세요.')
        with connect(path) as db:
            a = db.execute('SELECT * FROM versions WHERE id=?', (before,)).fetchone()
            b = db.execute('SELECT * FROM versions WHERE id=?', (after,)).fetchone()
            if not a or not b:
                raise HTTPException(404, '비교할 버전을 찾을 수 없습니다.')
            if a['law_id'] != b['law_id']:
                raise HTTPException(400, '같은 법령의 버전끼리 비교하세요.')
            left, right = latest_body(db, before), latest_body(db, after)
            if left is None or right is None:
                raise HTTPException(404, '비교할 본문이 없습니다.')
        return dict(before=dict(a), after=dict(b), **compare(left, right))

    @app.get('/api/changes/{change_id}/compare')
    def compare_correction(change_id: int):
        with connect(path) as db:
            change = db.execute('SELECT * FROM changes WHERE id=?', (change_id,)).fetchone()
            if not change:
                raise HTTPException(404, '변경 이력이 없습니다.')
            if change['before_body_id'] is None:
                raise HTTPException(400, '새 버전입니다. 법령 상세에서 비교할 이전 버전을 선택하세요.')
            import json
            bodies = []
            for body_id in (change['before_body_id'], change['after_body_id']):
                row = db.execute('SELECT articles,supplementary FROM bodies WHERE id=?', (body_id,)).fetchone()
                bodies.append({k: json.loads(row[k]) for k in row.keys()})
        return compare(*bodies)

    @app.patch('/api/changes/{change_id}')
    def review(change_id: int, data: ReviewInput):
        if data.status not in ('미확인', '검토 중', '검토 완료'):
            raise HTTPException(400, '올바른 검토 상태를 선택하세요.')
        with connect(path) as db:
            if not db.execute('SELECT 1 FROM changes WHERE id=?', (change_id,)).fetchone():
                raise HTTPException(404, '변경 이력을 찾을 수 없습니다.')
            db.execute('UPDATE changes SET review_status=?,memo=? WHERE id=?', (data.status, data.memo, change_id))
        return {'message': '검토 내용을 저장했습니다.'}

    @app.get('/api/report.csv')
    def report():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['법령명', '변경 유형', '감지일시(UTC)', '시행일', '검토 상태', '업무 메모'])
        with connect(path) as db:
            records = rows(db, '''SELECT l.name,c.kind,c.detected_at,v.effective,c.review_status,c.memo FROM changes c
                JOIN laws l ON l.id=c.law_id JOIN versions v ON v.id=c.version_id ORDER BY c.id DESC''')
        for record in records:
            values = list(record.values())
            writer.writerow(["'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s for s in values])
        return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="law-monitor-report.csv"'})

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
