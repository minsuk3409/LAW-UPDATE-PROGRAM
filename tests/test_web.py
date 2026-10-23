import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.database import connect, rows, latest_body
from app.demo import DemoAPI
from app.law_api import LawAPI, CollectionError, parse_body
from app.main import create_app
from app.services import BusyError, compare

HEADERS = {'X-Law-Monitor': '1'}


class FakeAPI(DemoAPI):
    def __init__(self):
        self.revised = False
        self.fail = False
        self.extra = False

    def versions(self, name, official_id=None):
        if self.fail and name == '산업안전보건법':
            raise CollectionError('가상 네트워크 실패')
        versions = super().versions(name, official_id)
        if self.extra:
            version = dict(versions[-1])
            version.update(mst='900004',version_key='900004:2099-01-01',effective='2099-01-01')
            versions.append(version)
        return versions

    def body(self, version):
        body = super().body(version)
        if self.revised and version['category'] == 'current':
            body['articles'][1]['text'] = '변경된 가상 본문'
        return body


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'laws.sqlite3')
        self.api = FakeAPI()
        self.app = create_app(self.path, api=self.api)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def check(self):
        response = self.client.post('/api/check', headers=HEADERS)
        self.assertEqual(response.status_code, 202)
        return self.client.get('/api/dashboard').json()

    def test_end_to_end_baseline_versions_compare_report(self):
        data = self.check()
        self.assertEqual(len(data['laws']), 4)
        self.assertEqual(data['changes'], [])
        self.assertEqual(len(data['runs']), 4)
        self.assertTrue(all(r['status'] == '성공' for r in data['runs']))
        law_id = data['laws'][0]['id']
        detail = self.client.get(f'/api/laws/{law_id}').json()
        self.assertEqual({v['category'] for v in detail['versions']}, {'previous','current','upcoming'})
        before = next(v for v in detail['versions'] if v['category']=='current')
        after = next(v for v in detail['versions'] if v['category']=='upcoming')
        result = self.client.get('/api/compare', params={'before':before['id'],'after':after['id']}).json()
        self.assertEqual({a['kind'] for a in result['articles']}, {'수정','신설','변경 없음'})
        self.assertEqual(len(self.client.get(f"/api/versions/{before['id']}").json()['body']['articles']),2)
        self.assertEqual(self.check()['changes'], [])
        self.api.revised = True
        data = self.check()
        self.assertEqual(len(data['changes']),4)
        change = data['changes'][0]
        result = self.client.get(f"/api/changes/{change['id']}/compare").json()
        self.assertEqual(sum(a['kind']=='수정' for a in result['articles']),1)
        response = self.client.patch(f"/api/changes/{change['id']}",headers=HEADERS,json={'status':'검토 완료','memo':'test'})
        self.assertEqual(response.status_code,404)
        csv = self.client.get('/api/report.csv').text
        self.assertIn('동일 버전 본문 변경',csv)
        self.assertNotIn('검토 상태',csv)
        self.assertNotIn('업무 메모',csv)

    def test_failed_law_preserves_its_baseline_and_others_continue(self):
        self.check()
        with connect(self.path) as db:
            law = db.execute("SELECT * FROM laws WHERE name='산업안전보건법'").fetchone()
            baseline = law['last_success']
        self.api.fail = True
        self.api.revised = True
        data = self.check()
        self.assertEqual(data['jobs'][0]['status'],'일부 실패')
        self.assertEqual(len(data['changes']),3)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT last_success FROM laws WHERE id=?',(law['id'],)).fetchone()[0],baseline)

    def test_body_failure_rolls_back_entire_law(self):
        self.check()
        with connect(self.path) as db:
            law = dict(db.execute('SELECT * FROM laws ORDER BY id LIMIT 1').fetchone())
            count = db.execute('SELECT count(*) FROM bodies').fetchone()[0]
        original = self.api.body
        def failing(version):
            if version['category']=='upcoming':
                raise CollectionError('본문 일부 수집 실패')
            body = original(version)
            body['articles'][0]['text']='변경되었지만 저장하면 안 됨'
            return body
        with patch.object(self.api,'body',side_effect=failing):
            with self.assertRaises(CollectionError):
                self.app.state.monitor.collect(law)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bodies').fetchone()[0],count)
            self.assertEqual(db.execute('SELECT count(*) FROM changes').fetchone()[0],0)

    def test_reversion_preserves_body_audit_history(self):
        self.check()
        self.api.revised=True
        self.check()
        self.api.revised=False
        data=self.check()
        self.assertEqual(len(data['changes']),8)
        with connect(self.path) as db:
            version=db.execute("SELECT id FROM versions WHERE category='current' LIMIT 1").fetchone()
            self.assertEqual(db.execute('SELECT count(*) FROM bodies WHERE version_id=?',(version[0],)).fetchone()[0],3)
            self.assertNotEqual(latest_body(db,version[0])['articles'][1]['text'],'변경된 가상 본문')

    def test_lock_prevents_web_scheduler_overlap(self):
        job,lock=self.app.state.monitor.reserve()
        try:
            self.assertEqual(self.client.post('/api/check',headers=HEADERS).status_code,409)
        finally:
            lock.close()
        self.assertEqual(self.check()['jobs'][0]['status'],'완료')

    def test_search_add_remove_and_preserve_data(self):
        results=self.client.get('/api/search',params={'q':'안전'}).json()
        response=self.client.post('/api/laws',headers=HEADERS,json=results[0])
        law_id=response.json()['id']
        self.assertEqual(response.status_code,200)
        self.check()
        self.assertEqual(self.client.delete(f'/api/laws/{law_id}',headers=HEADERS).status_code,200)
        self.assertNotIn(law_id,[l['id'] for l in self.client.get('/api/dashboard').json()['laws']])
        self.assertEqual(len(self.client.get(f'/api/laws/{law_id}').json()['versions']),3)
        self.client.post('/api/laws',headers=HEADERS,json=results[0])
        self.assertIn(law_id,[l['id'] for l in self.client.get('/api/dashboard').json()['laws']])

    def test_invalid_compare_and_cross_site_mutation(self):
        self.assertEqual(self.client.post('/api/check').status_code,403)
        self.assertEqual(self.client.post('/api/check',headers={**HEADERS,'Origin':'https://other.example'}).status_code,403)
        self.check()
        with connect(self.path) as db:
            versions=rows(db,'SELECT * FROM versions')
        a=versions[0]['id']
        b=next(v['id'] for v in versions if v['law_id']!=versions[0]['law_id'])
        self.assertEqual(self.client.get(f'/api/compare?before={a}&after={a}').status_code,400)
        self.assertEqual(self.client.get(f'/api/compare?before={a}&after={b}').status_code,400)
        self.assertEqual(self.client.get('/api/versions/999999').status_code,404)

    def test_demo_separated_and_real_mode_cannot_open_demo(self):
        demo_path=str(Path(self.tmp.name)/'demo.sqlite3')
        demo_app=create_app(demo_path,demo=True)
        with TestClient(demo_app) as client:
            data=client.get('/api/dashboard').json()
            self.assertTrue(data['demo'])
            self.assertEqual(len(data['laws']),1)
            self.assertEqual(client.post('/api/laws',headers=HEADERS,json={'name':'테스트'}).status_code,400)
        with self.assertRaises(RuntimeError):
            create_app(demo_path)


class APITests(unittest.TestCase):
    def test_nested_articles_and_supplementary(self):
        result=parse_body({'법령':{'조문':{'조문단위':[{'조문여부':'전문','조문내용':'제1장'},
            {'조문번호':'1','조문내용':'조문 본문','항':{'항내용':'① 항','호':{'호내용':'1. 호'}}}]},
            '부칙':{'부칙단위':[{'부칙내용':['제1조 시행일','제2조 경과조치']}]}}})
        self.assertEqual(len(result['articles']),1)
        self.assertIn('1. 호',result['articles'][0]['text'])
        self.assertIn('경과조치',result['supplementary'])

    def test_search_pagination_and_exact_match(self):
        api=LawAPI('secret')
        first=[{'법령명한글':'대상' if i==0 else '다른 법','법령ID':str(i+1)} for i in range(100)]
        with patch.object(api,'request',side_effect=[{'LawSearch':{'totalCnt':'101','law':first}},
                {'LawSearch':{'totalCnt':'101','law':{'법령명한글':'대상','법령ID':'101'}}}]) as request:
            result=api.search('대상',exact=True)
        self.assertEqual(len(result),2)
        self.assertEqual(request.call_args.kwargs['page'],2)

    def test_missing_data_does_not_look_like_success(self):
        api=LawAPI('secret')
        for data in [{}, {'LawSearch':{'totalCnt':2,'law':[]}}, {'LawSearch':{'totalCnt':'bad'}}]:
            with patch.object(api,'request',return_value=data):
                with self.assertRaises(CollectionError):
                    api.search('법')

    def test_versions_use_effective_history_and_id_for_renamed_law(self):
        api=LawAPI('secret')
        def search_result(endpoint, **params):
            self.assertEqual(params['target'],'eflaw')
            name='과거 법령명' if params['nw']==1 else '현재 법령명'
            row={'법령ID':'123','법령일련번호':str(100+params['nw']),
                '법령명한글':name,'공포일자':'20250101','시행일자':f"20260{params['nw']}01"}
            if params['nw']!=3:
                self.assertEqual(params['LID'],'123')
                self.assertNotIn('query',params)
            return {'LawSearch':{'totalCnt':1,'law':row}}
        with patch.object(api,'request',side_effect=search_result):
            versions=api.versions('현재 법령명')
        self.assertEqual({v['category'] for v in versions},{'previous','current','upcoming'})
        self.assertTrue(all(v['source']=='eflaw' for v in versions))

    def test_body_version_mismatch_rejected(self):
        api=LawAPI('secret')
        version=DemoAPI().versions('가상')[0]
        with patch.object(api,'request',return_value={'법령':{'기본정보':{'법령ID':'000000'}}}):
            with self.assertRaises(CollectionError):
                api.body(version)

    def test_credentials_are_not_in_request_errors(self):
        api=LawAPI('private-value')
        with patch('urllib.request.urlopen',side_effect=OSError('https://example/?OC=private-value')):
            with self.assertRaises(CollectionError) as error:
                api.search('법')
        self.assertNotIn('private-value',str(error.exception))


if __name__=='__main__':
    unittest.main()
