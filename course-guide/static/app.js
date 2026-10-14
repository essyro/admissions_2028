const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const labels = {vector:'VectorRAG',graph:'GraphRAG',sql:'Text-to-SQL'};
const state = {mode:'vector',view:'chat',busy:false,result:null,documents:[],page:1,total:0,catalogRequest:0,history:[]};
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const icon = (name) => `<i data-lucide="${name}"></i>`;
const icons = () => lucide.createIcons();
const number = (value) => Number(value).toLocaleString('ko-KR');

async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = '요청을 처리하지 못했습니다.';
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : detail; } catch {}
    throw new Error(detail);
  }
  return response.json();
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  window.setTimeout(() => { $('#toast').hidden = true; }, 2600);
}

function showError(message) {
  $('#global-error').textContent = message;
  $('#global-error').hidden = false;
}

function setView(view) {
  state.view = view;
  $$('.view').forEach(element => { element.hidden = element.id !== `view-${view}`; });
  $$('.nav-item').forEach(element => element.classList.toggle('active', element.dataset.view === view));
  $('#current-view').textContent = {chat:'질문하기',catalog:'과목 데이터',documents:'원문 자료'}[view];
  if (view === 'catalog') loadCatalog();
  if (view === 'documents') renderDocuments();
}

function setMode(mode) {
  if (state.busy) return;
  state.mode = mode;
  $$('.mode-button').forEach(element => {
    element.classList.toggle('selected', element.dataset.mode === mode);
    element.setAttribute('aria-pressed', String(element.dataset.mode === mode));
  });
  $('#top-k').disabled = mode !== 'vector';
}

async function loadStatus() {
  try {
    const info = await api('/api/status');
    $('#metric-universities').innerHTML = `${info.universities.length}<small>개</small>`;
    $('#metric-departments').textContent = number(info.departments);
    $('#metric-records').textContent = number(info.recommendations);
    $('#metric-documents').innerHTML = `${info.documents}<small>개</small>`;
    $('#nav-count').textContent = number(info.recommendations);
    $('#connection-dot').className = `status-dot ${info.ollama_online ? 'online' : 'offline'}`;
    $('#connection-label').textContent = info.ollama_online ? 'Ollama 연결됨' : 'Ollama 연결 끊김';
    const model = $('#model').value;
    $('#model').innerHTML = info.models.map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join('') || '<option value="">연결 확인 필요</option>';
    $('#model').value = info.models.includes(model) ? model : info.default_model;
    for (const selector of ['#university','#catalog-university']) {
      const chosen = $(selector).value;
      $(selector).innerHTML = '<option value="">전체 대학</option>' + info.universities.map(name => `<option>${escapeHTML(name)}</option>`).join('');
      $(selector).value = chosen;
    }
    const colors = [['#eaf3eb','#47734e'],['#f6ecec','#a55a58'],['#edf2f8','#567b9d'],['#f0edf6','#79678b'],['#edf2f9','#547495'],['#f5f1e7','#948552']];
    $('#university-grid').innerHTML = info.universities.map((name,index) => `<button class="university-tile" data-university="${escapeHTML(name)}" style="--tile-bg:${colors[index][0]};--tile-ink:${colors[index][1]}"><span class="initial">${['K','K','P','S','Y','C'][index]}</span><span>${escapeHTML(name)}<small>2028학년도</small></span></button>`).join('');
    $('#index-info').textContent = `${number(info.chunks)} 청크 · ${number(info.graph_edges)} 그래프 관계`;
    $('#global-error').hidden = true;
  } catch (error) {
    $('#connection-label').textContent = '연결 실패';
    $('#connection-dot').className = 'status-dot offline';
    showError(error.message);
  }
  icons();
}

function documentCard(document, page = 1, evidence = null) {
  const title = document.filename.replace(/^\([^)]*\)/,'').replace(/\.pdf$/i,'');
  const url = `/api/documents/${encodeURIComponent(document.id || document.document_id)}/pdf#page=${page}`;
  const image = `/api/documents/${encodeURIComponent(document.id || document.document_id)}/pages/${page}.png`;
  return `<article class="source-card" ${evidence ? `id="evidence-${evidence.number}"` : ''}>
    <a class="doc-preview" href="${url}" target="_blank" rel="noopener" title="${escapeHTML(title)} 원문 열기"><img src="${image}" alt="${escapeHTML(document.university)} PDF ${page}페이지" loading="lazy"></a>
    <div class="source-info"><div class="source-label"><span>${evidence ? `[${evidence.number}] ` : ''}${escapeHTML(document.university)}</span><small>PDF ${page}p</small></div><h3>${escapeHTML(title)}</h3>
    ${evidence ? `<details><summary class="muted">근거 내용${evidence.score != null ? ` · 유사도 ${evidence.score.toFixed(3)}` : ''}</summary><p class="source-excerpt">${escapeHTML(evidence.text)}</p></details>` : `<small>${document.pages}페이지 · 2028학년도</small>`}
    <a class="source-link" href="${url}" target="_blank" rel="noopener">원문 보기 ${icon('arrow-up-right')}</a></div></article>`;
}

function renderSources(evidence) {
  if (evidence) {
    $('#source-count').textContent = evidence.length;
    $('#evidence-list').innerHTML = evidence.length ? evidence.map(item => documentCard(item,item.page,item)).join('') : '<div class="empty-state">검색된 근거가 없습니다.</div>';
  } else {
    const guides = ['연세대','경희대'].map(name => state.documents.find(doc => doc.university === name && doc.pages <= 4)).filter(Boolean);
    $('#source-count').textContent = state.documents.length;
    $('#evidence-list').innerHTML = guides.map(doc => documentCard(doc,3)).join('');
  }
  icons();
}

function renderDocuments() {
  $('#document-grid').innerHTML = [...state.documents].sort((first,second) => first.university.localeCompare(second.university,'ko')).map(doc => documentCard(doc,1)).join('');
  icons();
}

function renderTable(rows, compact = false) {
  if (!rows.length) return '<div class="empty-state">조회된 데이터가 없습니다.</div>';
  if (!('course' in rows[0])) {
    const keys = Object.keys(rows[0]);
    return `<div class="table-scroll"><table><thead><tr>${keys.map(key => `<th>${escapeHTML(key)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${keys.map(key => `<td>${escapeHTML(row[key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  return `<div class="table-scroll"><table><thead><tr><th>대학교 · 계열</th><th>모집단위</th><th>선택유형</th><th>과목</th><th>이수 조건 · 원문</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHTML(row.university)}<span class="cell-secondary">${escapeHTML(row.field)}</span></td><td class="department-cell">${escapeHTML(row.department)}</td><td><span class="category-badge ${row.category === '진로선택' ? 'career' : ''}">${escapeHTML(row.category)}</span></td><td>${escapeHTML(row.course)}<span class="cell-secondary">${escapeHTML(row.level)}</span></td><td class="condition-cell">${escapeHTML(row.condition)}${row.note ? `<span class="cell-secondary">${escapeHTML(row.note)}</span>` : ''}${row.document_id && row.page ? `<a class="source-table-link" href="/api/documents/${encodeURIComponent(row.document_id)}/pdf#page=${Number(row.page)}" target="_blank" rel="noopener">원문 ${Number(row.page)}p ↗</a>` : ''}${!compact ? `<span class="cell-secondary">${escapeHTML(row.review_status)} · ${escapeHTML(row.extraction)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
}

function selectResult(tab) {
  $$('.result-pane').forEach(element => { element.hidden = element.id !== `result-${tab}`; });
  $$('[data-result]').forEach(element => {
    element.classList.toggle('active',element.dataset.result === tab);
    element.setAttribute('aria-selected',String(element.dataset.result === tab));
  });
}

function renderResult(result) {
  state.result = result;
  $('#answer-section').hidden = false;
  $('#suggestions').hidden = true;
  $('#result-content').hidden = false;
  $('#answer-error').hidden = true;
  $('#loading').hidden = true;
  $('#answer-meta').textContent = `${labels[result.mode]} · ${result.elapsed ?? '—'}초`;
  const markdown = result.answer.replace(/\[(\d+)\]/g,(_,reference) => `[${reference}](#evidence-${reference})`);
  $('#result-answer').innerHTML = DOMPurify.sanitize(marked.parse(markdown));
  $('#result-rows').innerHTML = result.mode === 'vector' ? '<div class="empty-state">문서 청크 기반 검색 결과입니다. 참고 원문에서 근거를 확인할 수 있습니다.</div>' : renderTable(result.records,true);
  $('#row-count').textContent = result.records.length || '';
  $('#truncated-note').hidden = !result.truncated;
  const trace = result.trace;
  $('#result-trace').innerHTML = `<div class="trace-header">${escapeHTML(trace.engine)}</div>` + (trace.sql ? `<pre class="trace-code">${escapeHTML(trace.sql)}</pre>` : '') + (trace.paths ? trace.paths.map(path => `<div class="graph-path"><span>${escapeHTML(path.university)}</span>${icon('chevron-right')}<span>${escapeHTML(path.field)}</span>${icon('chevron-right')}<span>${escapeHTML(path.department)}</span>${icon('chevron-right')}<span class="path-course">${escapeHTML(path.course)}</span><span class="muted">${escapeHTML(path.category)}</span></div>`).join('') : '') + `<details><summary class="muted">실행 상세</summary><pre class="trace-code">${escapeHTML(JSON.stringify(trace,null,2))}</pre></details>`;
  selectResult('answer');
  renderSources(result.evidence);
  icons();
}

function renderHistory() {
  $('#history-list').innerHTML = state.history.length ? state.history.map((item,index) => `<button class="history-item" data-history="${index}">${escapeHTML(item.question.slice(0,55))}<small>${labels[item.mode]} · ${escapeHTML(item.university || '전체 대학')}</small></button>`).join('') : '<span class="muted">아직 질문이 없습니다.</span>';
}

function setBusy(busy) {
  state.busy = busy;
  $$('#question-form button, #question-form select, #question-form textarea').forEach(element => { element.disabled = busy; });
  if (!busy) $('#top-k').disabled = state.mode !== 'vector';
  $('#submit-question').innerHTML = busy ? '<span class="spinner"></span><span>생성 중</span>' : `<span>질문하기</span>${icon('arrow-up')}`;
  icons();
}

async function submitQuestion(event) {
  event.preventDefault();
  const question = $('#question').value.trim();
  if (state.busy || question.length < 2) { if (!state.busy) toast('질문을 두 글자 이상 입력해 주세요.'); return; }
  const payload = {question,mode:state.mode,university:$('#university').value,model:$('#model').value,top_k:Number($('#top-k').value)};
  setBusy(true);
  $('#answer-section').hidden = false;
  $('#loading').hidden = false;
  $('#result-content').hidden = true;
  $('#answer-error').hidden = true;
  $('#suggestions').hidden = true;
  $('#answer-meta').textContent = labels[payload.mode];
  $('#loading-time').textContent = '0초';
  const started = Date.now();
  const timer = setInterval(() => { $('#loading-time').textContent = `${Math.floor((Date.now()-started)/1000)}초`; },1000);
  try {
    const result = await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    renderResult(result);
    state.history.unshift({...payload,result});
    state.history = state.history.slice(0,8);
    try { localStorage.setItem('course-note-history',JSON.stringify(state.history)); } catch {}
    renderHistory();
  } catch (error) {
    $('#answer-error').textContent = error.message;
    $('#answer-error').hidden = false;
  } finally {
    clearInterval(timer);
    $('#loading').hidden = true;
    setBusy(false);
  }
}

async function loadCatalog() {
  const request = ++state.catalogRequest;
  const params = new URLSearchParams({university:$('#catalog-university').value,field:$('#catalog-field').value,category:$('#catalog-category').value,search:$('#catalog-search').value,page:state.page});
  try {
    const result = await api(`/api/catalog?${params}`);
    if (request !== state.catalogRequest) return;
    state.total = result.total;
    $('#catalog-table').innerHTML = renderTable(result.rows);
    $('#catalog-count').textContent = `${number(result.total)}건`;
    for (const [selector,values,label] of [['#catalog-field',result.fields,'전체 계열'],['#catalog-category',result.categories,'전체 선택유형']]) {
      const value = $(selector).value;
      $(selector).innerHTML = `<option value="">${label}</option>` + values.map(item => `<option>${escapeHTML(item)}</option>`).join('');
      $(selector).value = value;
    }
    $('#page-label').textContent = `${state.page} / ${Math.max(1,Math.ceil(result.total/40))}`;
    $('#prev-page').disabled = state.page <= 1;
    $('#next-page').disabled = state.page * 40 >= result.total;
  } catch (error) { showError(error.message); }
}

document.addEventListener('click',event => {
  const nav = event.target.closest('[data-view]'); if (nav) setView(nav.dataset.view);
  const mode = event.target.closest('[data-mode]'); if (mode) setMode(mode.dataset.mode);
  const tab = event.target.closest('[data-result]'); if (tab) selectResult(tab.dataset.result);
  const suggestion = event.target.closest('[data-question]');
  if (suggestion && !state.busy) { $('#question').value = suggestion.dataset.question; $('#university').value = suggestion.dataset.university; $('#question').dispatchEvent(new Event('input')); $('#question').focus(); }
  const university = event.target.closest('.university-tile');
  if (university) { $('#catalog-university').value = university.dataset.university; $('#catalog-field').value = ''; state.page = 1; setView('catalog'); }
  const history = event.target.closest('[data-history]');
  if (history && !state.busy) { const item = state.history[Number(history.dataset.history)]; setView('chat'); setMode(item.mode); $('#question').value = item.question; $('#university').value = item.university; $('#question').dispatchEvent(new Event('input')); renderResult(item.result); }
});
$('#question-form').addEventListener('submit',submitQuestion);
$('#question').addEventListener('input',() => { $('#char-count').textContent = `${number($('#question').value.length)} / 1,200`; });
$('#question').addEventListener('keydown',event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); $('#question-form').requestSubmit(); } });
$('#refresh-status').addEventListener('click',loadStatus);
$('#new-question').addEventListener('click',() => { if (state.busy) return; $('#question').value = ''; $('#question').dispatchEvent(new Event('input')); $('#answer-section').hidden = true; $('#suggestions').hidden = false; state.result = null; renderSources(); $('#question').focus(); });
$('#clear-history').addEventListener('click',() => { state.history=[]; try { localStorage.removeItem('course-note-history'); } catch {} renderHistory(); });
$('#copy-answer').addEventListener('click',async () => { if (!state.result) return; try { await navigator.clipboard.writeText(state.result.answer); toast('답변을 복사했습니다.'); } catch { toast('클립보드에 접근할 수 없습니다.'); } });
for (const selector of ['#catalog-university','#catalog-field','#catalog-category']) $(selector).addEventListener('change',() => { state.page=1; if (selector === '#catalog-university') $('#catalog-field').value=''; loadCatalog(); });
let searchTimer;
$('#catalog-search').addEventListener('input',() => { clearTimeout(searchTimer); searchTimer=setTimeout(() => { state.page=1; loadCatalog(); },250); });
$('#prev-page').addEventListener('click',() => { if (state.page>1) { state.page--; loadCatalog(); } });
$('#next-page').addEventListener('click',() => { if (state.page*40<state.total) { state.page++; loadCatalog(); } });

async function initialize() {
  icons();
  try { state.history = JSON.parse(localStorage.getItem('course-note-history') || '[]').filter(item => item.result && labels[item.mode]).slice(0,8); } catch { state.history=[]; }
  renderHistory();
  await loadStatus();
  try { state.documents=await api('/api/documents'); renderSources(); } catch (error) { showError(error.message); }
}
initialize();