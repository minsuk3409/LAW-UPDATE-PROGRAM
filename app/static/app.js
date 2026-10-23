'use strict';
const $ = id => document.getElementById(id);
const state = {law: null, detail: null, category: 'current', comparison: null, polling: null, demo: false, job: null, drafts: new Map(), detailToken: 0, bodyToken: 0};
function node(tag, text, cls) {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;}
function showNotice(text, error=false) {$('notice').textContent=text; $('notice').className='notice'+(error?' error':''); $('notice').hidden=false;}
function dateTime(value) {return value ? new Intl.DateTimeFormat('ko-KR',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Seoul'}).format(new Date(value)) : '아직 조회하지 않음';}
async function api(path, method='GET', data) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-Law-Monitor':'1'}, ...(data !== undefined ? {body:JSON.stringify(data)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '입력값을 확인하세요.');
  return result;
}
function safe(task) {return async event => {if(event) event.preventDefault(); try {await task(event);} catch(error) {showNotice(error.message || '요청에 실패했습니다.',true);}};}
function option(version) {const labels={previous:'개정 이전',current:'현행',upcoming:'시행예정',unconfirmed:'미확인'};const n=node('option',`${labels[version.category]} · 시행 ${version.effective} · 공포 ${version.promulgated} (${version.revision || '구분 없음'})`);n.value=version.id;return n;}
async function refresh() {
  const data = await api('/api/dashboard');
  state.demo=data.demo;
  $('mode').textContent = data.demo ? '가상 예시 모드' : '법제처 API 모드';
  $('setup').hidden = !data.demo && data.credential_configured;
  $('setup').textContent = data.demo ? '가상 예시 자료입니다. 아래 본문과 날짜는 화면 확인용이며 실제 법령 내용이 아닙니다. 실제 조회는 예시 모드를 종료하고 LAW_OC를 설정한 뒤 실행하세요.' : 'LAW_OC 인증값이 설정되지 않았습니다. 관심 법령은 등록할 수 있지만 실제 검색·수집에는 법제처 인증값과 네트워크 접근 설정이 필요합니다.';
  $('law-count').textContent=data.laws.length;
  $('pending-count').textContent=data.laws.reduce((s,l)=>s+l.pending_count,0);
  $('upcoming-count').textContent=data.laws.reduce((s,l)=>s+l.upcoming_count,0);
  const job=data.jobs[0];
  $('job-status').textContent=job ? job.status : '아직 없음';
  $('job-time').textContent=job ? dateTime(job.finished_at || job.started_at) : '';
  $('check').disabled=job?.status==='진행 중';
  $('check').textContent=job?.status==='진행 중'?'조회 중…':'지금 확인';
  $('query').disabled=data.demo; $('manual').hidden=data.demo;
  $('law-list').replaceChildren();
  for(const law of data.laws) {
    const button=node('button',undefined,'law-card'+(state.law===law.id?' selected':''));
    button.append(node('strong',law.name),node('small',law.last_status==='실패'?'최근 조회 실패 · 기존 자료 유지':`시행예정 ${law.upcoming_count} · 검토할 변경 ${law.pending_count}`));
    button.addEventListener('click',safe(()=>selectLaw(law.id)));
    $('law-list').append(button);
  }
  if(!data.laws.length) $('law-list').append(node('p','관심 법령을 검색하거나 직접 등록하세요.','muted'));
  renderChanges(data.changes);
  $('runs').replaceChildren();
  for(const run of data.runs) {const row=node('tr');for(const value of [run.name,dateTime(run.checked_at),run.status,run.message]) row.append(node('td',value));$('runs').append(row);}
  if(!data.runs.length) {const row=node('tr');const cell=node('td','조회 기록이 없습니다. ‘지금 확인’을 눌러 시작하세요.');cell.colSpan=4;row.append(cell);$('runs').append(row);}
  if(job?.status==='진행 중' && !state.polling) state.polling=setInterval(safe(()=>refresh()),2000);
  if(job?.status!=='진행 중' && state.polling) {clearInterval(state.polling);state.polling=null;if(state.law) await selectLaw(state.law,false);showNotice(job.message + (job.status==='일부 실패'?' · 아래 조회 기록에서 원인을 확인하세요.':''),job.status==='일부 실패');}
  state.job=job?.id;
  return data;
}
async function selectLaw(id, updateDashboard=true) {
  state.law=id;
  state.comparison=null; $('comparison').hidden=true;
  const token=++state.detailToken;
  const detail=await api(`/api/laws/${id}`);
  if(token!==state.detailToken) return;
  state.detail=detail;
  $('law-empty').hidden=true; $('law-detail').hidden=false;
  $('law-name').textContent=detail.law.name;
  $('last-success').textContent='마지막 성공 조회 · '+dateTime(detail.law.last_success);
  $('remove').hidden=state.demo;
  for(const button of document.querySelectorAll('.tabs button')) {
    const cat=button.dataset.category;
    button.querySelector('span').textContent=detail.versions.filter(v=>v.category===cat).length;
  }
  $('unconfirmed').hidden=!detail.versions.some(v=>v.category==='unconfirmed');
  $('unconfirmed').textContent='이전 수집본 중 최신 API 목록에서 확인되지 않은 버전이 있습니다. 비교 목록의 ‘미확인’ 항목으로 보존되어 있으며, 현행 여부를 확정하지 않습니다.';
  $('before').replaceChildren();$('after').replaceChildren();
  for(const v of detail.versions) {$('before').append(option(v));$('after').append(option(v));}
  const current=detail.versions.find(v=>v.category==='current');
  const previous=detail.versions.find(v=>v.category==='previous' && (!current || v.effective<=current.effective));
  const upcoming=[...detail.versions].filter(v=>v.category==='upcoming').sort((a,b)=>a.effective.localeCompare(b.effective))[0];
  if(current) {$('before').value=(upcoming ? current : (previous || current)).id;$('after').value=(upcoming || current).id;}
  $('compare').disabled=detail.versions.length<2;
  await selectCategory(state.category);
  if(updateDashboard) await refresh();
}
async function selectCategory(category) {
  state.category=category;
  for(const button of document.querySelectorAll('.tabs button')) {const active=button.dataset.category===category;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}
  const versions=(state.detail?.versions || []).filter(v=>v.category===category);
  if(category==='upcoming') versions.sort((a,b)=>a.effective.localeCompare(b.effective));
  $('version-select').replaceChildren(...versions.map(option));
  $('version-select').hidden=!versions.length;$('body').hidden=true;
  $('version-empty').hidden=!!versions.length;
  const labels={previous:'개정 이전',current:'현행',upcoming:'시행예정'};
  $('version-empty').textContent=state.detail?.law.last_success ? `${labels[category]} 버전이 수집되지 않았습니다.` : '아직 수집한 자료가 없습니다. ‘지금 확인’을 실행하세요.';
  ++state.bodyToken;
  if(versions.length) await loadBody();
}
async function loadBody() {
  const id=$('version-select').value;
  if(!id) return;
  const token=++state.bodyToken;
  const data=await api(`/api/versions/${id}`);
  if(token!==state.bodyToken) return;
  $('body').hidden=false;
  $('version-meta').replaceChildren(...[`공포 ${data.version.promulgated}`,`시행 ${data.version.effective}`,data.version.revision,`수집 ${dateTime(data.body.collected_at)}`].filter(Boolean).map(x=>node('span',x)));
  $('articles').replaceChildren();
  for(const article of data.body.articles) {
    const item=node('article',undefined,'article');item.append(node('h3',`${article.label} ${article.title ? '('+article.title+')':''}`),node('pre',article.text));
    if(article.effective) item.append(node('small','조문 시행일: '+article.effective));
    $('articles').append(item);
  }
  $('supplementary').textContent=data.body.supplementary || '수집된 부칙 내용이 없습니다. 공식 원문에서 확인하세요.';
  $('official').hidden=true;$('official').removeAttribute('href');
  if(data.version.official_url) {const url=new URL(data.version.official_url);if(url.protocol==='https:' && url.hostname==='www.law.go.kr') {$('official').href=url.href;$('official').hidden=false;}}
}
function renderComparison(data) {
  state.comparison=data;$('comparison').hidden=false;
  $('compare-warning').textContent=data.warning;
  $('before-label').textContent=data.before ? `기준 · 시행 ${data.before.effective} / 공포 ${data.before.promulgated}` : '변경 감지 이전 수집본';
  $('after-label').textContent=data.after ? `대상 · 시행 ${data.after.effective} / 공포 ${data.after.promulgated}` : '변경 감지 이후 수집본';
  renderDiff();
  $('comparison').scrollIntoView({behavior:'smooth',block:'start'});
}
function renderDiff() {
  const data=state.comparison;if(!data) return;
  $('compare-results').replaceChildren();
  const filtered=data.articles.filter(a=>!$('changes-only').checked || a.kind!=='변경 없음');
  for(const article of filtered) {
    const cls={신설:'added',삭제:'deleted',수정:'modified'}[article.kind] || '';
    const item=node('article',undefined,'diff-item '+cls);
    const heading=node('h3');heading.append(node('span',article.kind,'tag'),document.createTextNode(article.label));item.append(heading);
    const columns=node('div',undefined,'compare-row');
    for(const content of [article.before,article.after]) {const column=node('div');if(content) {column.append(node('strong',content.title || content.label),node('pre',content.text));if(content.effective) column.append(node('small','조문 시행일: '+content.effective));}else column.append(node('p','해당 조문 없음','muted'));columns.append(column);}
    item.append(columns);$('compare-results').append(item);
  }
  if(!filtered.length) $('compare-results').append(node('p','선택한 조건에 해당하는 변경 조문이 없습니다.','empty compact'));
  $('compare-supplementary').replaceChildren();
  if(data.supplementary.changed || !$('changes-only').checked) {const box=node('details');box.append(node('summary',data.supplementary.changed?'부칙 변경 있음 — 펼쳐서 확인':'부칙 비교'));const columns=node('div',undefined,'compare-row');for(const text of [data.supplementary.before,data.supplementary.after]) {const col=node('div');col.append(node('pre',text || '부칙 없음'));columns.append(col);}box.append(columns);$('compare-supplementary').append(box);}
}
function renderChanges(changes) {
  $('changes').replaceChildren();
  if(!changes.length) {$('changes').append(node('p','아직 감지된 변경이 없습니다. 최초 수집 후 다음 조회부터 변경 여부를 확인합니다.','empty compact'));return;}
  for(const change of changes) {
    const card=node('article',undefined,'review-card');const heading=node('div',undefined,'review-title');
    const title=node('div');title.append(node('strong',change.name),node('p',`${change.kind} · 감지 ${dateTime(change.detected_at)} · 시행 ${change.effective}`,'muted'));
    const open=node('button','법령 확인');open.addEventListener('click',safe(async()=>{await selectLaw(change.law_id);$('law-detail').scrollIntoView({behavior:'smooth'});}));heading.append(title,open);card.append(heading);
    if(change.before_body_id) {const compareButton=node('button','변경 당시 본문 비교');compareButton.addEventListener('click',safe(async()=>renderComparison(await api(`/api/changes/${change.id}/compare`))));card.append(compareButton);}
    const form=node('form',undefined,'review-form');const statusWrap=node('div'),memoWrap=node('div');
    const draft=state.drafts.get(change.id) || {status:change.review_status,memo:change.memo};
    const select=node('select');select.id=`review-status-${change.id}`;select.setAttribute('aria-label',change.name+' 검토 상태');for(const status of ['미확인','검토 중','검토 완료']) {const op=node('option',status);op.value=status;select.append(op);}select.value=draft.status;
    const statusLabel=node('label','검토 상태');statusLabel.htmlFor=select.id;statusWrap.append(statusLabel,select);
    const memo=node('textarea');memo.id=`memo-${change.id}`;memo.value=draft.memo;memo.maxLength=5000;memo.placeholder='사업장 영향과 후속 조치를 기록하세요.';const memoLabel=node('label','업무 메모');memoLabel.htmlFor=memo.id;memoWrap.append(memoLabel,memo);
    const keepDraft=()=>state.drafts.set(change.id,{status:select.value,memo:memo.value});select.addEventListener('change',keepDraft);memo.addEventListener('input',keepDraft);
    const save=node('button','저장');save.type='submit';form.append(statusWrap,memoWrap,save);
    form.addEventListener('submit',safe(async()=>{save.disabled=true;try {await api(`/api/changes/${change.id}`,'PATCH',{status:select.value,memo:memo.value});state.drafts.delete(change.id);showNotice('검토 내용을 저장했습니다.');await refresh();}finally{save.disabled=false;}}));card.append(form);$('changes').append(card);
  }
}
$('check').addEventListener('click',safe(async()=>{$('check').disabled=true;try {await api('/api/check','POST');showNotice('조회 작업을 시작했습니다. 결과는 아래 조회 기록에 표시됩니다.');if(!state.polling) state.polling=setInterval(safe(()=>refresh()),2000);await refresh();}catch(error){$('check').disabled=false;throw error;}}));
$('search-form').addEventListener('submit',safe(async()=>{const q=$('query').value.trim();if(!q) return;const button=$('search-form').querySelector('button');button.disabled=true;try {const result=await api('/api/search?q='+encodeURIComponent(q));$('search-results').replaceChildren();if(!result.length)$('search-results').append(node('p','검색 결과가 없습니다. 정확한 이름으로 다시 검색하세요.'));for(const law of result) {const add=node('button',law.name+' + 등록');add.addEventListener('click',safe(async()=>{const result=await api('/api/laws','POST',law);$('search-results').replaceChildren();showNotice('관심 법령을 등록했습니다. ‘지금 확인’으로 본문을 수집하세요.');await selectLaw(result.id);}));$('search-results').append(add);}}finally{button.disabled=false;}}));
$('add-form').addEventListener('submit',safe(async()=>{const name=$('manual-name').value.trim();if(!name)return;const result=await api('/api/laws','POST',{name});$('manual-name').value='';showNotice('등록했습니다. 조회할 때 법령명이 정확히 일치하는지 확인합니다.');await selectLaw(result.id);}));
$('remove').addEventListener('click',safe(async()=>{if(!state.law || !confirm('관심 목록에서 제외할까요? 수집·검토 이력은 보존됩니다.')) return;await api(`/api/laws/${state.law}`,'DELETE');state.law=null;state.detail=null;$('law-detail').hidden=true;$('law-empty').hidden=false;$('comparison').hidden=true;await refresh();showNotice('관심 목록에서 제외했습니다.');}));
for(const button of document.querySelectorAll('.tabs button')) button.addEventListener('click',safe(()=>selectCategory(button.dataset.category)));
$('version-select').addEventListener('change',safe(loadBody));
$('compare').addEventListener('click',safe(async()=>renderComparison(await api(`/api/compare?before=${$('before').value}&after=${$('after').value}`))));
$('changes-only').addEventListener('change',renderDiff);
safe(async()=>{const data=await refresh();if(data.laws.length) await selectLaw(data.laws[0].id,false);})();
