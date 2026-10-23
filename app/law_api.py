"""법제처 JSON 어댑터. 실제 계정/권한으로 응답 계약 검증이 필요합니다."""
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request

BASE = 'https://www.law.go.kr/DRF/'


class CollectionError(Exception):
    pass



def check_api_error(data):
    """인증값이 섞일 수 있는 원문 대신 안전한 오류 분류만 노출합니다."""
    if not isinstance(data, dict) or 'msg' not in data:
        return
    if isinstance(data.get('LawSearch'), dict) or isinstance(data.get('법령'), dict):
        return
    message = str(data.get('msg', ''))
    lower = message.lower()
    if any(word in lower for word in ('ip', '도메인', '서버장비')) and any(word in message for word in ('등록', '검증', '허용')):
        raise CollectionError(
            '[CALLER_REGISTRATION] 법제처가 호출 서버의 IP·도메인 등록 확인을 요구했습니다. '
            '국가법령정보 공동활용의 API 신청 시스템 정보에서 실제 요청을 보내는 PC/서버의 등록 정보를 확인하세요. '
            'IP 제한을 별도로 설정하지 않았어도 이 응답이 올 수 있습니다. '
            '등록 정보가 맞다면 법제처 이용 지원에 이 오류 코드를 문의하세요. 인증값은 공유하지 마세요.')
    if any(word in message for word in ('인증', '사용자', '로그인')) or 'oc' in lower:
        raise CollectionError(
            '[API_AUTHENTICATION] 법제처가 인증·사용자 확인 오류를 반환했습니다. '
            'API인증키관리의 OC와 입력한 값, 해당 계정의 신청 상태를 확인한 후 다시 실행하세요. '
            '인증값을 채팅이나 화면 캡처로 공유하지 마세요.')
    if any(word in message for word in ('권한', '승인', '신청')):
        raise CollectionError(
            '[API_PERMISSION] 법제처가 신청·이용 권한 관련 오류를 반환했습니다. '
            '시행일 기준 법령 목록·본문 API의 신청 및 승인 상태를 확인하세요.')
    if any(word in message for word in ('초과', '횟수', '과다')):
        raise CollectionError('[API_RATE_LIMIT] 법제처가 요청 한도 관련 오류를 반환했습니다. 잠시 후 다시 조회하세요.')
    raise CollectionError(
        '[API_RESPONSE_ERROR] 법제처가 정상 법령 데이터 대신 오류 응답을 반환했습니다. '
        '국가법령정보 공동활용의 오류자가진단 또는 이용 지원에서 신청 상태를 확인하세요. '
        '응답 원문에는 인증 정보가 포함될 수 있어 표시하지 않았습니다.')


def scalar(value):
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ''


def date_value(value):
    text = scalar(value)
    digits = re.sub(r'[^0-9]', '', text)
    if len(digits) != 8:
        raise CollectionError('공포일·시행일이 정확한 날짜가 아닙니다. 공식 원문을 확인하세요.')
    try:
        return dt.datetime.strptime(digits, '%Y%m%d').date().isoformat()
    except ValueError:
        raise CollectionError('유효하지 않은 법령 날짜입니다.') from None


def as_list(value):
    if value is None or value == '':
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(v, dict) for v in value):
        return value
    raise CollectionError('API 목록 응답 형식이 예상과 다릅니다.')


def flatten(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return '\n'.join(filter(None, (flatten(x) for x in value)))
    if isinstance(value, dict):
        return '\n'.join(filter(None, (flatten(v) for k, v in value.items()
                           if '내용' in k or k in ('항', '호', '목', '부칙단위'))))
    return ''


def parse_body(data):
    law = data.get('법령') if isinstance(data, dict) else None
    if not isinstance(law, dict):
        raise CollectionError('법령 본문 응답이 아닙니다. 본문 API 이용 권한을 확인하세요.')
    section = law.get('조문')
    if not isinstance(section, dict):
        raise CollectionError('조문을 찾을 수 없습니다. 기존 본문을 유지합니다.')
    articles = []
    seen = set()
    for item in as_list(section.get('조문단위')):
        if scalar(item.get('조문여부')) == '전문':
            continue
        number = scalar(item.get('조문번호'))
        branch = scalar(item.get('조문가지번호'))
        if not number.isdigit() or (branch and not branch.isdigit()):
            raise CollectionError('조문 번호 형식이 예상과 다릅니다.')
        number, branch = str(int(number)), str(int(branch or '0'))
        key = f'{number}-{branch}'
        if not number or key in seen:
            raise CollectionError('조문 번호가 없거나 중복되었습니다. 자동 비교를 중단합니다.')
        seen.add(key)
        title = scalar(item.get('조문제목'))
        label = f'제{number}조' + (f'의{branch}' if branch and branch != '0' else '')
        text = flatten(item)
        if not text:
            raise CollectionError('빈 조문 응답입니다. 기존 본문을 유지합니다.')
        articles.append({'key': key, 'label': label, 'title': title, 'text': text,
                         'effective': scalar(item.get('조문시행일자'))})
    if not articles:
        raise CollectionError('비교 가능한 조문이 없습니다. 공식 원문을 확인하세요.')
    supplementary = law.get('부칙', {})
    return {'articles': articles, 'supplementary': flatten(supplementary), 'raw': data}


class LawAPI:
    def __init__(self, oc=None):
        self.oc = oc if oc is not None else os.environ.get('LAW_OC', '').strip()

    def request(self, endpoint, **params):
        if not self.oc:
            raise CollectionError('LAW_OC 인증값이 없습니다. 환경 설정에서 등록한 후 서버를 다시 시작하세요.')
        query = urllib.parse.urlencode(dict(OC=self.oc, type='JSON', **params))
        request = urllib.request.Request(BASE + endpoint + '?' + query,
                                         headers={'User-Agent': 'LawUpdateMonitor/1.0'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(20 * 1024 * 1024 + 1)
                if len(raw) > 20 * 1024 * 1024:
                    raise CollectionError('API 응답 크기 한도를 초과했습니다.')
                result = json.loads(raw.decode('utf-8-sig'))
        except (OSError, ValueError):
            # URLs may contain OC: never expose exception messages or raw responses.
            raise CollectionError('법제처 요청 실패: 인증값·API 권한·네트워크 접근 및 응답 형식을 확인하세요.') from None
        if not isinstance(result, dict):
            raise CollectionError('API 최상위 응답 형식이 올바르지 않습니다.')
        check_api_error(result)
        return result

    def search(self, query, category='current', exact=False, official_id=None):
        target, nw = {'previous': ('eflaw', 1), 'current': ('eflaw', 3), 'upcoming': ('eflaw', 2)}[category]
        collected = []
        total_expected = None
        for page in range(1, 101):
            criteria = {'LID': official_id} if official_id else {'query': query}
            data = self.request('lawSearch.do', target=target, nw=nw, display=100, page=page, **criteria)
            root = data.get('LawSearch')
            if not isinstance(root, dict):
                raise CollectionError('법령 검색 응답이 아닙니다. API 이용 권한을 확인하세요.')
            try:
                total = int(root['totalCnt'])
            except (KeyError, ValueError, TypeError):
                raise CollectionError('API 검색 건수를 확인할 수 없습니다.') from None
            if total < 0 or total > 10000 or (total_expected is not None and total != total_expected):
                raise CollectionError('검색 결과가 너무 많거나 조회 중 변경되었습니다. 다시 조회하세요.')
            total_expected = total
            batch = as_list(root.get('law'))
            expected = min(100, max(0, total - (page - 1) * 100))
            if len(batch) != expected:
                raise CollectionError('검색 결과 일부가 누락되었습니다. 기존 기준을 유지합니다.')
            collected.extend(batch)
            if page * 100 >= total:
                break
        if exact and not official_id:
            collected = [r for r in collected if scalar(r.get('법령명한글')) == query]
        return collected

    def versions(self, name, official_id=None):
        result = {}
        current = self.search(name, 'current', exact=True, official_id=official_id)
        identities = {scalar(r.get('법령ID')) for r in current}
        if len(identities) != 1 or not next(iter(identities), '').isdigit():
            raise CollectionError('일치하는 현행 법령을 하나로 식별할 수 없습니다. 법령명과 이용 권한을 확인하세요.')
        resolved_id = next(iter(identities))
        for category in ('previous', 'current', 'upcoming'):
            entries = current if category == 'current' else self.search(name, category, official_id=resolved_id)
            for row in entries:
                identity = scalar(row.get('법령ID'))
                if identity != resolved_id:
                    continue
                mst = scalar(row.get('법령일련번호'))
                if not mst.isdigit() or not identity.isdigit():
                    raise CollectionError('법령 버전 식별자가 올바르지 않습니다.')
                effective = date_value(row.get('시행일자'))
                promulgated = date_value(row.get('공포일자'))
                source = 'eflaw'
                # Same promulgation may have several effective-date versions.
                key = f'{mst}:{effective}'
                result[key] = dict(version_key=key, mst=mst, source=source,
                    category=category, effective=effective, promulgated=promulgated,
                    revision=scalar(row.get('제개정구분명')), number=scalar(row.get('공포번호')),
                    official_id=identity,
                    official_url='https://www.law.go.kr/법령/' + urllib.parse.quote(name, safe='') + '/' + effective.replace('-', ''))
        versions = list(result.values())
        if not any(v['category'] == 'current' for v in versions):
            raise CollectionError('일치하는 현행 법령이 없습니다. 법령명·API 권한을 확인하세요.')
        if len({v['official_id'] for v in versions}) != 1:
            raise CollectionError('동일 이름의 법령 식별자가 여러 개입니다. 검색에서 법령을 지정하세요.')
        return versions

    def body(self, version):
        params = dict(target=version['source'], MST=version['mst'])
        if version['source'] == 'eflaw':
            params['efYd'] = version['effective'].replace('-', '')
        data = self.request('lawService.do', **params)
        info = data.get('법령', {}).get('기본정보', {})
        if not isinstance(info, dict) or scalar(info.get('법령ID')) != version['official_id']:
            raise CollectionError('요청한 법령과 응답 본문의 식별자가 일치하지 않습니다.')
        if date_value(info.get('공포일자')) != version['promulgated'] or date_value(info.get('시행일자')) != version['effective']:
            raise CollectionError('요청한 버전과 응답의 날짜가 일치하지 않습니다. 기존 데이터를 유지합니다.')
        return parse_body(data)
