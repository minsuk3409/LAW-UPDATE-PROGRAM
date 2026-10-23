"""명확히 표시된 가상 데이터. 실제 법령 내용이 아닙니다."""
from datetime import date, timedelta
from .database import connect
from .services import Monitor


class DemoAPI:
    def search(self, query, category='current', exact=False):
        return [{'법령명한글': '예시 안전보건법 (가상)', '법령ID': '999999'}] if query else []

    def versions(self, name, official_id=None):
        today = date.today()
        result = []
        for offset, category, mst in [(-365, 'previous', '900001'), (-30, 'current', '900002'), (60, 'upcoming', '900003')]:
            effective = (today + timedelta(days=offset)).isoformat()
            result.append(dict(version_key=f'{mst}:{effective}', mst=mst, source='law', category=category,
                promulgated=(today + timedelta(days=offset-90)).isoformat(), effective=effective,
                revision='일부개정 (가상)', number='예시', official_id='999999', official_url=''))
        return result

    def body(self, version):
        text = {'previous': '가상 교육을 연 1회 실시한다.', 'current': '가상 교육을 반기 1회 실시한다.',
                'upcoming': '가상 교육을 분기 1회 실시하고 결과를 기록한다.'}[version['category']]
        articles = [dict(key='1-0', label='제1조', title='목적', text='이 자료는 화면 확인을 위한 가상 법령이며 법적 효력이 없습니다.', effective=''),
                    dict(key='2-0', label='제2조', title='교육 (가상)', text=text, effective=version['effective'])]
        if version['category'] == 'upcoming':
            articles.append(dict(key='3-0', label='제3조', title='기록 (가상)', text='가상 교육 기록을 보관한다.', effective=''))
        return dict(articles=articles, supplementary='가상 부칙: 표시된 시행일부터 적용한다.', raw={'demo': True})


def seed(path):
    with connect(path) as db:
        if db.execute("SELECT 1 FROM settings WHERE key='demo'").fetchone():
            return
        if db.execute('SELECT 1 FROM versions LIMIT 1').fetchone():
            raise RuntimeError('실제 데이터가 있는 DB에 예시 자료를 넣을 수 없습니다. 별도 DB를 사용하세요.')
        db.execute('DELETE FROM laws')
        db.execute("INSERT INTO laws(name,official_id) VALUES('예시 안전보건법 (가상)','999999')")
    monitor = Monitor(path, DemoAPI())
    job, lock = monitor.reserve()
    monitor.run(job, lock)
    with connect(path) as db:
        db.execute("INSERT INTO settings VALUES('demo','1')")
