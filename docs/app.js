import { toYaml, fromYaml } from "./yaml.js";
import { lock, unlock, MIN_PASSPHRASE } from "./lock.js";

const REPO = "studioditto-ctrl/lookbook";
const BRANCH = "claude/running-news-telegram-delivery-hgx53y";
const FILE = "settings.yaml";
const RUNS = `https://github.com/${REPO}/actions/workflows/digest.yml`;
// 저장에 실패해도 고친 값을 잃지 않도록 이 기기에 남겨둔다
const DRAFT = "settings_draft";
// 암호로 잠근 토큰. 페이지와 같이 배포되므로 어느 기기에서든 받아올 수 있다.
const LOCK_FILE = "docs/token.enc";
// AI 추천은 repository_dispatch 로 워크플로를 깨우고, 결과 파일이 생길 때까지 이 자리에서 기다린다.
const DISPATCH_URL = `https://api.github.com/repos/${REPO}/dispatches`;
const RECOMMEND_PATH = slug => `state/recommend/${slug}.json`;
const BUILD = "2026-09-11";   // 화면에 찍어 어느 판인지 확인한다

/* 넓은 화면에서는 주제를 한 번에 하나만 편다. 격자로 늘어놓으면 어느 것을
   고치는 중인지 알기 어렵고 카드가 좁아 모바일과 다를 바가 없었다. */
const wide = () => matchMedia("(min-width:900px)").matches;
let selected = 0;
/* 데스크탑 가운데 단(섹션 목록)이 지금 보여주는 것. 모바일은 안 쓴다 —
   모바일은 예전처럼 발송 일정은 늘 보이고 나머지는 한 번에 접어 둔다. */
let section = "schedule";
/* 화면 전체가 지금 무엇을 보여주는지 — 주제 목록 / 새 주제 마법사 / 공통 설정.
   예전에는 토큰·Drive·제외어 카드가 주제 목록 위에 늘 떠 있었다. 자주 안
   쓰는 것들을 공통 설정으로 옮기고, 주제 만들기는 단계별 마법사로 뺐다. */
let mode = "topics";

let data = null, sha = null;
let dirty = false, saving = false, savedAt = null, loadedAt = null, timer = null;

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const hhmm = d => d.toTimeString().slice(0, 5);
/* 열어둔 상세 패널은 다시 그려도 열린 채로 둔다 */
const open = new Set();

let toastTimer = null;
function toast(msg, kind, keep){
  const el = $("toast");
  el.innerHTML = msg;
  el.className = "toast show " + (kind || "");
  clearTimeout(toastTimer);
  if (kind === "ok" && !keep) toastTimer = setTimeout(() => el.className = "toast", 4000);
  if (keep) toastTimer = setTimeout(() => el.className = "toast", 12000);
}

function showState(){
  const el = $("saveState");
  if (saving)      { el.textContent = "저장하는 중…";              el.className = "busy"; }
  else if (dirty)  { el.textContent = "바뀜 — 곧 저장됩니다";      el.className = "dirty"; }
  else if (savedAt){ el.textContent = "저장됨 " + hhmm(savedAt);   el.className = ""; }
  else if (loadedAt){el.textContent = "불러옴 " + hhmm(loadedAt);  el.className = ""; }
  const busy = saving || !data;
  $("saveBtn").disabled = busy;
  $("reloadBtn").disabled = busy;
}

/* 어떤 값이든 바뀌면 곧바로 저장까지 간다 — 저장 버튼을 안 눌러도 반영된다 */
function touch(){
  dirty = true; showState();
  try{ localStorage.setItem(DRAFT, toYaml(data)); }catch(e){ /* 용량 초과는 무시 */ }
  clearTimeout(timer);
  timer = setTimeout(() => save(), 1200);
}
function discardDraft(){
  localStorage.removeItem(DRAFT); dirty = false;
  load();
}

/* ---------- 렌더링 ---------- */
const arg = s => `'${String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;

// scope 는 같은 di/si 로 두 군데(주제 목록 카드와 마법사의 '실행 조건'
// 단계)에 동시에 그려질 때 span id 가 겹치지 않게 갈라 준다 — #digests 는
// 마법사를 보는 중에도 화면 뒤에서 계속 다시 그려지므로, 안 갈라 두면
// bump() 가 숨어 있는 쪽 span 을 고쳐 화면에 반영이 안 된 것처럼 보인다.
function stepper(di, si, key, val, scope = ""){
  return `<div class="step">
    <button onclick="bump(${di},${si},'${key}',-1,'${scope}')" aria-label="줄이기">−</button>
    <span id="v${scope}${di}-${si}-${key}">${val}</span>
    <button onclick="bump(${di},${si},'${key}',1,'${scope}')" aria-label="늘리기">+</button>
  </div>`;
}

function slotCardHTML(di, si, s, scope = ""){
  return `
    <div class="slot">
      <div class="head">
        <input class="title" value="${esc(s.title)}" aria-label="메시지 제목"
               onchange="set(${di},${si},'title',this.value)">
        <label class="sw" style="margin:0">
          <input type="checkbox" ${s.enabled ? "checked" : ""}
                 onchange="set(${di},${si},'enabled',this.checked)" aria-label="발송"><i></i>
        </label>
      </div>
      <div style="margin-top:10px">
        <label>보내는 시각</label>
        <input type="time" value="${s.send_at}" onchange="set(${di},${si},'send_at',this.value)">
      </div>
      <div class="duo">
        <div><label>기사</label>${stepper(di, si, "articles", s.articles, scope)}</div>
        <div><label>영상</label>${stepper(di, si, "videos", s.videos, scope)}</div>
      </div>
      <div class="duo">
        <button class="tiny ghost" onclick="testSend(${di},${si})">지금 테스트 발송</button>
        <button class="tiny danger" onclick="delSlot(${di},${si})">이 시간 삭제</button>
      </div>
    </div>`;
}

/* 아래 다섯 개가 한 주제 안의 실제 내용이다. 모바일과 데스크탑이 이걸
   그대로 나눠 쓴다 — 모바일은 발송 일정만 늘 보이고 나머지 넷을 한 덩어리로
   접고, 데스크탑은 다섯을 각각 가운데 단에서 골라 하나씩 본다. */

function scheduleSectionHTML(di, dg){
  return `<div class="slots">
      ${dg.slots.map((s, si) => slotCardHTML(di, si, s)).join("")}
    </div>
    <button class="tiny wide dashed" onclick="addSlot(${di})">＋ 보낼 시간 추가</button>`;
}

function keywordsSectionHTML(di, dg){
  return `
    <label style="margin-top:6px">키워드 — 이 말로 찾고, 위 순위일수록 먼저 골라집니다</label>
    <div class="sub">칩을 끌어다 다른 순위로 옮길 수 있습니다.</div>
    ${keywordTiers(di, dg)}
    <div class="seg" style="margin-top:10px">
      ${TIERS.map(([w, label]) => `<button aria-pressed="${tierFor(di) === w}"
          onclick="pickTier(${di},${w})">${label}</button>`).join("")}
    </div>
    <div class="add">
      <input id="kw${di}" placeholder="예: 인터벌" enterkeyhint="done"
             autocapitalize="off" autocomplete="off"
             onkeydown="if(event.key==='Enter'){event.preventDefault();addKeyword(${di})}">
      <button class="tiny" onclick="addKeyword(${di})">추가</button>
    </div>
    <div class="add">
      <button class="tiny ghost" style="flex:1" onclick="suggestFor(${di})">추천 키워드 보기</button>
    </div>
    <div class="chips" id="sg${di}">${suggestionChips(di, dg)}</div>`;
}

function scopeSectionHTML(di, dg){
  return `
    <label style="margin-top:6px">주제어 <span class="sub">(안 넣어도 됩니다)</span></label>
    <div class="sub">
      ${scopeWords(dg).length
        ? "이 말이 하나도 없는 글은 버립니다. 쉼표로 구분합니다."
        : "지금은 키워드로만 거릅니다. 무관한 글이 섞이면 그때 넣으세요."}
    </div>
    <div class="add">
      <input id="sc${di}" value="${esc(scopeWords(dg).join(", "))}"
             placeholder="비워두면 키워드로 거릅니다" enterkeyhint="done"
             autocapitalize="off" autocomplete="off"
             onchange="setScope(${di}, this.value)"
             onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}">
    </div>
    <div class="note">
      ${(dg.queries || []).length
        ? `검색에 쓰는 말: <b>${esc(scopedQuery(dg))}</b>`
        : "키워드를 넣으면 그 말로 구글 뉴스와 유튜브를 찾습니다."}
    </div>`;
}

function sourcesSectionHTML(di, dg){
  return `
    <div class="split2">
      <div>
        <label style="margin-top:6px">블로그 · RSS</label>
        <div class="chips">
          ${(dg.feeds || []).map((f, fi) => `
            <span class="chip" title="${esc(f.url)}">${esc(f.name)}
              <button onclick="delFeed(${di},${fi})" aria-label="삭제">×</button></span>`).join("")
            || '<span class="sub">없음</span>'}
        </div>
        <div class="add">
          <input id="bl${di}" placeholder="RSS 주소, 또는 네이버 블로그 아이디" enterkeyhint="done"
                 autocapitalize="off" autocomplete="off"
                 onkeydown="if(event.key==='Enter'){event.preventDefault();addFeed(${di})}">
          <button class="tiny" onclick="addFeed(${di})">추가</button>
        </div>
        <div class="note">
          네이버 블로그는 아이디만, 나머지는 RSS 주소를 그대로 넣으면 됩니다
          (브런치·티스토리·서브스택 등).<br>
          <b>인스타·페이스북·X</b> 는 아이디로 가져올 방법이 없습니다 —
          공식 API 가 남의 공개 계정을 안 열어 줍니다.
          <a href="https://rss.app/rss-feed" target="_blank" rel="noopener"
             style="color:var(--accent)">RSS 주소로 바꿔서 →</a> 넣어주세요.
        </div>
      </div>

      <div>
        <label style="margin-top:6px">유튜브 채널</label>
        <div class="chips">
          ${(dg.channels || []).map((c, ci) => `
            <span class="chip">${esc(c.name)}
              <button onclick="delChannel(${di},${ci})" aria-label="삭제">×</button></span>`).join("")
            || '<span class="sub">없음 (config 파일 목록은 그대로 쓰입니다)</span>'}
        </div>
        <div class="add">
          <input id="cs${di}" placeholder="구독에서 검색 — 예: 커피"
                 oninput="searchSubs(${di})" enterkeyhint="search">
        </div>
        <div class="chips" id="cr${di}"></div>
      </div>
    </div>

    ${((dg.feeds || []).length + (dg.channels || []).length) ? `
    <button class="tiny ghost" style="margin-top:16px" onclick="exportSourcesCSV(${di})">
      ⬇ 소스 목록 CSV로 내보내기</button>` : ""}`;
}

/* 이 주제의 채널·블로그 목록을 CSV 로 내려받는다.
   구글 시트 API 를 새로 붙이면 쓰기 범위가 넓어져야 해서, 대신 엑셀·구글
   시트 어느 쪽에서든 그대로 열리는 CSV 로 내보낸다. */
function csvCell(v){
  const s = String(v == null ? "" : v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function exportSourcesCSV(di){
  const dg = data.digests[di];
  const rows = [["name", "kind", "id_or_url", "region", "reason"]];
  for (const c of dg.channels || []){
    rows.push([c.name, "youtube", c.channel_id, c.region || "", c.reason || ""]);
  }
  for (const f of dg.feeds || []){
    rows.push([f.name, "blog", f.url, f.region || "", f.reason || ""]);
  }
  const csv = rows.map(r => r.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + csv], {type: "text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `${dg.label || "sources"}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function dangerSectionHTML(di, dg){
  return `
    <div class="sub">'${esc(dg.label)}' 주제를 지우면 발송 일정 · 키워드 · 검색 범위 · 소스가
      모두 사라집니다.${dg.config ? ` <b>${esc(dg.config)}</b> 파일은 그대로 두고 발송만 멈춥니다.` : ""}</div>
    <button class="tiny wide danger" style="margin-top:12px"
            onclick="delTopic(${di})">'${esc(dg.label)}' 주제 삭제</button>`;
}

/* 가운데 단 — 고른 주제 안의 항목 목록. 데스크탑에서만 그린다. */
const SECTIONS = [
  ["schedule", "발송 일정", dg => dg.slots.length],
  ["keywords", "키워드", dg => Object.keys(dg.keywords).length],
  ["scope", "검색 범위", dg => scopeWords(dg).length || null],
  ["sources", "소스", dg => (dg.feeds || []).length + (dg.channels || []).length],
];

function renderSectionNav(){
  const dg = data.digests[selected];
  if (!dg){ $("sectionNav").innerHTML = ""; return; }
  $("sectionNav").innerHTML = `
    <div class="sub" style="margin-bottom:8px; font-weight:600; color:var(--ink)">${esc(dg.label)}</div>
    <nav class="navlist">
      ${SECTIONS.map(([name, label, count]) => {
        const n = count(dg);
        return `<button aria-current="${section === name}" onclick="pickSection('${name}')">
          ${label}${n != null ? ` <span class="sub">${n}</span>` : ""}
        </button>`;
      }).join("")}
    </nav>
    <div class="divider"></div>
    <nav class="navlist">
      <button class="danger" aria-current="${section === "danger"}"
              onclick="pickSection('danger')">주제 삭제</button>
    </nav>`;
}

function render(){
  if (!data) return;   // 아직 불러오는 중일 때 눌러도(예: 사이드바) 죽지 않는다
  const desktop = wide();
  if (selected >= data.digests.length) selected = Math.max(0, data.digests.length - 1);

  $("digests").innerHTML = data.digests.map((dg, di) => {
    const sel = di === selected;
    const schedule = scheduleSectionHTML(di, dg);
    const keywords = keywordsSectionHTML(di, dg);
    const scope = scopeSectionHTML(di, dg);
    const sources = sourcesSectionHTML(di, dg);
    const danger = dangerSectionHTML(di, dg);

    if (desktop){
      // 다섯 항목을 각각 감싼다. 고른 것만 CSS 가 보여준다.
      const sec = (name, title, body) => `
        <section class="tsec${sel && section === name ? " sel" : ""}" data-section="${name}">
          <h2>${title}</h2>${body}
        </section>`;
      return `
        <div class="card${sel ? " sel" : ""}" data-di="${di}">
          ${sec("schedule", "발송 일정", schedule)}
          ${sec("keywords", "키워드 우선순위", keywords)}
          ${sec("scope", "검색 범위", scope)}
          ${sec("sources", "콘텐츠 소스", sources)}
          ${sec("danger", "위험 구역", danger)}
        </div>`;
    }

    // 모바일: 발송 일정은 늘 보이고, 나머지 넷은 한 덩어리로 접는다 — 예전과 같다.
    return `
      <div class="card" data-di="${di}">
        <h2>${esc(dg.label)}</h2>
        ${schedule}
        <details data-panel="d${di}" ${open.has("d" + di) ? "open" : ""}
                 ontoggle="panel('d${di}',this.open)">
          <summary>키워드 ${Object.keys(dg.keywords).length} · 채널 ${(dg.channels || []).length} · 블로그 ${(dg.feeds || []).length}</summary>
          ${keywords}
          ${scope}
          ${sources}
          <div style="margin-top:16px">${danger}</div>
        </details>
      </div>`;
  }).join("");

  $("topicNav").innerHTML = data.digests.map((dg, di) => `
    <button aria-current="${mode === "topics" && di === selected}" onclick="pickTopic(${di})">
      ${esc(dg.label)}
      <span class="sub">${dg.slots.filter(s => s.enabled).map(s => s.send_at).join(" · ") || "발송 없음"}</span>
    </button>`).join("");
  document.querySelectorAll(".nav-wizard-btn")
    .forEach(el => el.setAttribute("aria-current", mode === "wizard"));
  document.querySelectorAll(".nav-settings-btn")
    .forEach(el => el.setAttribute("aria-current", mode === "settings"));

  $("topicsView").hidden = mode !== "topics";
  $("wizardView").hidden = mode !== "wizard";
  $("settingsView").hidden = mode !== "settings";
  if (desktop) $("sectionNav").hidden = mode !== "topics";
  else $("sectionNav").innerHTML = "";

  if (desktop && mode === "topics") renderSectionNav();
  if (mode === "wizard") renderWizard();
  if (mode === "settings") renderFilters();

  $("excludeChips").innerHTML = data.exclude.map(w => `
    <span class="chip">${esc(w)}
      <button onclick="delExclude(${arg(w)})" aria-label="삭제">×</button>
    </span>`).join("");
}

function panel(id, isOpen){ isOpen ? open.add(id) : open.delete(id); }

function pickTopic(di){
  selected = di;
  section = "schedule";    // 주제를 바꾸면 처음 항목으로 되돌아간다
  mode = "topics";
  open.add("d" + di);      // 고르자마자 내용이 보여야 한다 (모바일)
  render();
  document.querySelector(`#digests .card.sel`)?.scrollIntoView({block: "start"});
}

/* 데스크탑 가운데 단에서 항목을 고른다. */
function pickSection(name){
  section = name;
  render();
}

/* 주제 목록 / 새 주제 마법사 / 공통 설정 — 화면 전체를 바꾼다. */
function setMode(m){
  mode = m;
  // 세션이 하나라도 남아 있으면(완료된 것 포함) 그대로 보여준다 — 새로
  // 시작하려면 탭의 '+' 나 '새 주제 하나 더 만들기'를 눌러야 한다.
  if (m === "wizard" && !wizard) startWizard();
  render();
  window.scrollTo({top: 0});
}

/* 한 주제에 시간을 몇 개든 둘 수 있다. 회차 이름은 겹치지 않게 만든다. */
function addSlot(di){
  const dg = data.digests[di];
  const used = new Set(dg.slots.map(s => s.slot));
  let name;
  do { name = "s" + Math.random().toString(36).slice(2, 7); } while (used.has(name));

  const last = dg.slots[dg.slots.length - 1];
  dg.slots.push({
    slot: name,
    title: `${dg.label} 브리핑`,
    send_at: nextHour(last && last.send_at),
    enabled: true,
    articles: last ? last.articles : 1,
    videos: last ? last.videos : 3,
  });
  render(); touch();
  toast(`${esc(dg.label)} 에 보낼 시간을 하나 더 넣었습니다. 시각과 제목을 정해주세요.`, "busy");
}
/* 마지막 시각 다음의 정시. 없으면 09:00 */
function nextHour(send_at){
  if (!send_at) return "09:00";
  const h = (parseInt(send_at.slice(0, 2), 10) + 1) % 24;
  return String(h).padStart(2, "0") + ":00";
}
function delSlot(di, si){
  const dg = data.digests[di], s = dg.slots[si];
  // 하나뿐인 시간을 지우면 보낼 일이 없는 주제가 남는다. 그때는 주제째 묻는다.
  if (dg.slots.length === 1){
    if (!confirm(`'${dg.label}' 의 마지막 시간입니다. 주제째 지울까요?`)) return;
    return delTopic(di, true);
  }
  if (!confirm(`'${s.title}' (${s.send_at}) 을(를) 지울까요?`)) return;
  dg.slots.splice(si, 1);
  render(); touch();
  toast(`'${esc(s.title)}' 을(를) 지웠습니다.`, "busy");
}
function delTopic(di, confirmed){
  const dg = data.digests[di];
  if (!confirmed && !confirm(`'${dg.label}' 주제를 통째로 지울까요?`)) return;
  data.digests.splice(di, 1);
  open.delete("d" + di);
  // '위험 구역'에서 지운 채로 두면 다음 주제도 곧바로 그 주제의 삭제
  // 화면으로 열린다 — 놀랄 수 있으니 처음 항목으로 되돌린다.
  section = "schedule";
  render(); touch();
  toast(`'${esc(dg.label)}' 주제를 지웠습니다.`
    + (dg.config ? ` (${esc(dg.config)} 파일은 그대로 둡니다)` : ""), "busy");
}
function set(di, si, key, val){ data.digests[di].slots[si][key] = val; touch(); }
function bump(di, si, key, delta, scope = ""){
  const slot = data.digests[di].slots[si];
  slot[key] = Math.max(0, Math.min(10, (slot[key] || 0) + delta));
  $(`v${scope}${di}-${si}-${key}`).textContent = slot[key];
  touch();
}

/* 키워드 하나가 두 가지 일을 한다 — 찾는 말이자 우선순위다.
   칸을 둘로 나눠 두 번 입력하게 할 이유가 없다.

   검색어는 키워드에서 만들어 낸다. 하나씩 따로 걸면 키워드 수만큼 호출이
   늘어나므로(유튜브 search 는 1회 100유닛) OR 로 묶어 한 번만 부른다.
   너무 넓어지지 않게 가중치 높은 순으로 몇 개만 쓴다. */
const QUERY_WORDS = 6;

/* 화면은 세 순위로 보여주고 저장은 그대로 숫자 가중치다.
   filter._score 가 이 숫자를 더해 점수를 매긴다. */
const TIERS = [[3, "1순위"], [2, "2순위"], [1, "3순위"]];
const newTier = {};   // 주제별로 '추가' 할 때 쓸 순위

function tierOf(weight){
  const n = Number(weight) || 1;
  return n >= 3 ? 3 : (n <= 1 ? 1 : 2);
}
function tierFor(di){ return newTier[di] || 2; }
function pickTier(di, weight){ newTier[di] = weight; render(); }

function keywordTiers(di, dg){
  // 감싸개가 있어야 넓은 화면에서 세 상자를 나란히 놓을 수 있다
  return `<div class="tiers">` + TIERS.map(([weight, label]) => {
    const words = Object.entries(dg.keywords)
      .filter(([, w]) => tierOf(w) === weight)
      .map(([word]) => word);
    return `
      <div class="tier" data-di="${di}" data-tier="${weight}">
        <div class="head"><span>${label}</span><span>${words.length}</span></div>
        <div class="chips">
          ${words.map(word => `<span class="chip kw" data-di="${di}" data-word="${esc(word)}">
              ${esc(word)}
              <button onclick="delKeyword(${di},${arg(word)})" aria-label="삭제">×</button>
            </span>`).join("") || '<span class="sub">비어 있음</span>'}
        </div>
      </div>`;
  }).join("") + `</div>`;
}

/* 낱말이 여럿이면 따옴표로 묶는다.
   "남성 피부 OR 남성 화장품" 은 검색엔진이
   남성 AND (피부 OR 남성) AND (화장품) 으로 읽어 엉뚱한 게 나온다.
   '"남성 피부" OR "남성 화장품"' 이라야 뜻대로 걸린다. */
function asPhrase(word){
  return /\s/.test(word) ? `"${word.replace(/"/g, "")}"` : word;
}

/* 주제어. 검색어 앞에 AND 로 붙고, 들어온 글이 이 주제인지도 이 말로 본다.
   '훈련 OR 루틴' 만으로는 한미연합훈련 기사가 그대로 딸려 왔다. */
function scopeWords(dg){
  // 주제 이름을 기본값으로 쓰지 않는다. '돌파매매' 주제에 강환국·깡토 같은
  // 사람 이름을 키워드로 넣으면 그 이름이 든 글에 '돌파매매' 라는 말이 없어
  // 한 건도 안 남는다. 비어 있으면 키워드로만 거른다.
  return (dg.scope || []).map(w => String(w).trim()).filter(Boolean);
}

/* 주제어와 겹치는 낱말은 뺀다 — 앞에 이미 붙어 있다.
   '남성 피부' 에서 피부를 떼면 '남성' 만 남아 훨씬 넓게 걸린다. */
function scopedQuery(dg){
  const scope = scopeWords(dg);
  const raw = ((dg.queries || [])[0] || {}).query || "";
  if (!scope.length) return raw;
  const lower = new Set(scope.map(w => w.toLowerCase()));
  const terms = [];
  for (const chunk of raw.split(" OR ")){
    const parts = chunk.trim().replace(/"/g, "").split(/\s+/)
                       .filter(w => w && !lower.has(w.toLowerCase()));
    if (!parts.length) continue;
    const term = asPhrase(parts.join(" "));
    if (!terms.includes(term)) terms.push(term);
  }
  let head = scope.map(asPhrase).join(" OR ");
  if (scope.length > 1) head = `(${head})`;
  return terms.length ? `${head} (${terms.join(" OR ")})` : head;
}

function setScope(di, value){
  const dg = data.digests[di];
  dg.scope = String(value).split(",").map(w => w.trim()).filter(Boolean);
  render(); touch();
  toast(dg.scope.length
    ? `'${esc(dg.label)}' 주제어를 바꿨습니다. 저장하는 중…`
    : `'${esc(dg.label)}' 주제어를 비웠습니다. 키워드로만 거릅니다. 저장하는 중…`,
    "busy");
}

function syncQueries(dg){
  const words = Object.entries(dg.keywords)
    .sort((a, b) => b[1] - a[1])
    .slice(0, QUERY_WORDS)
    .map(([word]) => asPhrase(word));
  dg.queries = words.length ? [{name: dg.label, query: words.join(" OR ")}] : [];
}

function addKeyword(di, word){
  const el = $("kw" + di);
  const k = (word !== undefined ? word : el.value).trim();
  if (!k) return;
  const dg = data.digests[di];
  if (dg.keywords[k]){ toast(`'${esc(k)}' 은(는) 이미 있습니다.`, "err"); return; }
  dg.keywords[k] = tierFor(di);
  syncQueries(dg);
  if (el) el.value = "";
  open.add("d" + di);            // 넣은 칩이 바로 보이도록 패널을 연 채로 둔다
  render(); touch();
  toast(`'${esc(k)}' 을(를) ${esc(dg.label)} 에 넣었습니다. 저장하는 중…`, "busy");
}
function delKeyword(di, k){
  const dg = data.digests[di];
  delete dg.keywords[k];
  syncQueries(dg);
  render(); touch();
}

/* ---------- 추천 키워드 ----------
   자동완성 API 는 브라우저에서 막힐 수 있어(CORS) 결과를 보장할 수 없다.
   그래서 네트워크 없이도 항상 나오는 쪽을 먼저 만들고, 원격은 얹기만 한다. */
const SUGGEST_SUFFIX = ["추천", "후기", "입문", "초보", "리뷰", "트렌드", "꿀팁", "순위", "비교"];
const suggestPool = {};   // 주제 이름 -> 후보 전체. 이미 넣은 말은 그릴 때 걸러낸다.

function localSuggestions(label){
  const out = SUGGEST_SUFFIX.map(s => `${label} ${s}`);
  // 불러온 구독 채널 이름에서 같이 쓰이는 말을 뽑는다
  const needle = label.toLowerCase();
  for (const s of subs){
    if (!s.title.toLowerCase().includes(needle)) continue;
    for (const word of s.title.split(/[\s·,\-_/|()\[\]]+/)){
      if (word.length >= 2 && word.toLowerCase() !== needle) out.push(word);
    }
  }
  return out;
}

async function remoteSuggestions(label){
  // 열려 있으면 쓰고, 막히면 조용히 포기한다. 이것 때문에 화면이 멈추면 안 된다.
  const control = new AbortController();
  const timer = setTimeout(() => control.abort(), 4000);
  try{
    const r = await fetch(
      "https://duckduckgo.com/ac/?type=list&q=" + encodeURIComponent(label),
      {signal: control.signal});
    if (!r.ok) return [];
    const body = await r.json();
    // ["러닝", ["러닝화", "러닝 크루", …]] 또는 [{phrase: …}]
    const list = Array.isArray(body) ? (body[1] || []) : (body || []);
    return list.map(x => typeof x === "string" ? x : (x && x.phrase) || "").filter(Boolean);
  }catch(e){
    return [];
  }finally{ clearTimeout(timer); }
}

/* 이미 넣은 말을 뺀 나머지. 넣을 때마다 줄어들되 목록은 남는다. */
function visibleSuggestions(dg){
  const pool = suggestPool[dg.label] || [];
  const taken = new Set(Object.keys(dg.keywords).map(k => k.toLowerCase()));
  const seen = new Set();
  const picks = [];
  for (const word of pool){
    const clean = String(word).trim();
    const key = clean.toLowerCase();
    if (!clean || clean.length > 20 || taken.has(key) || seen.has(key)) continue;
    seen.add(key);
    picks.push(clean);
    if (picks.length >= 9) break;
  }
  return picks;
}

function suggestionChips(di, dg){
  if (!suggestPool[dg.label]) return "";
  const picks = visibleSuggestions(dg);
  if (!picks.length) return '<span class="sub">더 추천할 말이 없습니다.</span>';
  return picks.map(w => `<span class="chip plain"><button
      style="width:auto;padding:0;font-size:14px;color:var(--accent)"
      onclick="addKeyword(${di},${arg(w)})">+ ${esc(w)}</button></span>`).join("")
    + `<span class="chip plain"><button style="width:auto;padding:0;font-size:14px"
      onclick="addAllSuggestions(${di})">모두 추가(${Math.min(5, picks.length)})</button></span>`;
}

async function suggestFor(di, quiet){
  const dg = data.digests[di];
  if (!dg) return;
  const box = $("sg" + di);
  if (box && !suggestPool[dg.label]) box.innerHTML = '<span class="sub">추천을 찾는 중…</span>';

  if (!suggestPool[dg.label]){
    const remote = await remoteSuggestions(dg.label);
    suggestPool[dg.label] = [...remote, ...localSuggestions(dg.label)];
  }
  render();
  const picks = visibleSuggestions(dg);
  if (!quiet && picks.length) toast(`추천 ${picks.length}개 — 넣을 것만 누르세요.`, "ok");
}

function addAllSuggestions(di){
  const dg = data.digests[di];
  let added = 0;
  for (const word of visibleSuggestions(dg).slice(0, 5)){
    if (dg.keywords[word]) continue;
    // 사람이 고른 말이 아니므로 낮은 순위로 넣는다. 끌어 올리면 된다.
    dg.keywords[word] = 1;
    added += 1;
  }
  if (!added) return;
  syncQueries(dg);
  open.add("d" + di);
  render(); touch();
  toast(`${added}개를 ${esc(dg.label)} 에 넣었습니다.`, "busy");
}
/* 필터 기본값 — settings.py 의 DEFAULTS 와 같은 숫자다. 여기 안 적혀 있으면
   (한 번도 안 바꿨으면) 저 하드코딩된 값 그대로 돈다. */
const FILTER_DEFAULTS = {lookback_hours: 48, min_subscribers: 10000, min_views: 5000};

function renderFilters(){
  const f = data.filters || {};
  $("fltLookback").value = f.lookback_hours ?? FILTER_DEFAULTS.lookback_hours;
  $("fltMinSubs").value = f.min_subscribers ?? FILTER_DEFAULTS.min_subscribers;
  $("fltMinViews").value = f.min_views ?? FILTER_DEFAULTS.min_views;
}
function setFilter(key, value){
  const n = Math.max(0, parseInt(value, 10) || 0);
  data.filters = data.filters || {};
  data.filters[key] = n;
  touch();
  toast("필터 기본값을 바꿨습니다. 저장하는 중…", "busy");
}

function addExclude(){
  const w = $("excludeInput").value.trim();
  if (!w || data.exclude.includes(w)) return;
  data.exclude.push(w); $("excludeInput").value = ""; render(); touch();
}
function delExclude(w){ data.exclude = data.exclude.filter(x => x !== w); render(); touch(); }

/* 주제 이름이 채널명에 들어 있으면 붙인다. 대소문자는 무시한다. */
function matchChannels(label){
  const needle = label.toLowerCase();
  return subs.filter(s => s.title.toLowerCase().includes(needle))
             .slice(0, 8)
             .map(s => ({name: s.title, channel_id: s.id}));
}

/* AI 추천 없이 이름만으로 바로 만든다 (마법사 1단계의 '빠르게 만들기'). */
function addTopic(label){
  label = String(label || "").trim();
  if (!label) return;
  const key = "t" + Date.now().toString(36);
  data.digests.push({
    config: "", key, label, scope: [],
    slots: [{slot: "daily", title: `${label} 브리핑`, send_at: "18:00",
             enabled: true, articles: 2, videos: 3}],
    keywords: {[label]: 3},
    queries: [{name: label, query: label}],
    channels: matchChannels(label),
    feeds: [],
  });
  const di = data.digests.length - 1;
  mode = "topics";
  selected = di;
  section = "schedule";
  syncQueries(data.digests[di]);
  open.add("d" + di);   // 붙은 채널과 추천을 바로 볼 수 있게
  render(); touch();
  suggestFor(di, true);
  const n = data.digests[di].channels.length;
  toast(n
    ? `'${esc(label)}' 주제를 만들고 구독 채널 ${n}개를 붙였습니다. 아래에서 확인하세요.`
    : `'${esc(label)}' 주제를 만들었습니다. 이름이 맞는 구독 채널은 없지만 `
      + `유튜브 검색으로 영상이 들어옵니다.`,
    "busy");
}

/* ---------- 새 주제 만들기 마법사 ----------
   테마 입력 → AI 추천 → 채널/키워드 선택 → 자동세팅 완료 → 실행 조건 →
   테스트 → 실행. 키를 브라우저에 둘 수 없어 추천은 repository_dispatch 로
   워크플로를 깨우고, 결과 파일(state/recommend/<slug>.json)이 저장소에
   생길 때까지 이 화면에서 기다린다 — testSend() 의 .trigger 방식과 같은
   '요청하고 기다리는' 구조다. */

const WIZARD_STEPS = [
  ["theme", "테마 입력"], ["recommend", "AI 추천"], ["pick", "채널/키워드 선택"],
  ["setup", "자동세팅 완료"], ["condition", "실행 조건"], ["test", "테스트"], ["done", "실행"],
];

let wizard = null;
let wizardSessions = [];

function stepIndex(step){ return WIZARD_STEPS.findIndex(([id]) => id === step); }

// 세션 하나가 지금까지 도달한 가장 앞선 단계(maxStep)를 기록해 둔다 —
// 진행 표시줄에서 이미 지나온 단계만 눌러 되돌아갈 수 있게 하려면 필요하다.
function advanceStep(session, step){
  session.step = step;
  const idx = stepIndex(step);
  if (idx > session.maxStep) session.maxStep = idx;
}

function startWizard(){
  const session = {
    id: "w" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    step: "theme", maxStep: 0, theme: "", slug: "", result: null,
    picked: new Set(), di: null, busy: false, searching: false, error: null,
  };
  wizardSessions.push(session);
  wizard = session;
}

// 지금 보고 있는 세션을 바꾼다 — 다른 테마를 추천받는 동안에도(폴링은
// 그 세션 객체에서 계속 돌고 있다) 다른 세션을 만들거나 보러 갈 수 있다.
function switchWizard(id){
  const s = wizardSessions.find(s => s.id === id);
  if (s) wizard = s;
  render();
}

function closeWizard(id){
  const i = wizardSessions.findIndex(s => s.id === id);
  if (i === -1) return;
  wizardSessions.splice(i, 1);
  if (wizard && wizard.id === id){
    wizard = wizardSessions[wizardSessions.length - 1] || null;
  }
  render();
}

async function dispatchRecommend(theme, slug, excludeNames){
  const client_payload = {theme, slug};
  // '추가 검색' 은 이미 나온 이름을 다시 추천받지 않으려고 같이 보낸다.
  if (excludeNames && excludeNames.length) client_payload.exclude = excludeNames.join(", ");
  const r = await fetch(DISPATCH_URL, {
    method: "POST", headers: headers(),
    body: JSON.stringify({event_type: "recommend_sources", client_payload}),
  });
  if (!r.ok){
    const j = await r.json().catch(() => ({}));
    const err = new Error(`${r.status} ${j.message || r.statusText}`);
    err.status = r.status;
    throw err;
  }
}

/* 결과 파일이 커밋될 때까지 기다린다. 워크플로 기동 + 실행 + 커밋까지
   최대 50개 후보를 만들고, 유튜브 구독자·매체 방문자 수 확인·RSS 자동
   발견까지 하나씩 열어 확인하느라 보통 2~8분 걸린다. 못 받으면 시간
   초과로 알리고, 다시 시도할 수 있다. */
async function pollRecommend(slug, {intervalMs = 5000, timeoutMs = 600000} = {}){
  const path = RECOMMEND_PATH(slug);
  const start = Date.now();
  while (Date.now() - start < timeoutMs){
    const j = await getFile(path).catch(() => null);
    if (j) return JSON.parse(dec(j.content));
    await new Promise(r => setTimeout(r, intervalMs));
  }
  throw new Error("추천 결과를 받지 못했습니다 (시간 초과). 워크플로 실행 기록을 확인해 주세요.");
}

async function wizardStartRecommend(){
  if (!wizard) startWizard();
  const theme = ($("wzTheme") || {}).value?.trim();
  if (!theme){ toast("테마를 입력해 주세요.", "err"); return; }
  if (!token()){ toast("먼저 공통 설정에서 GitHub 토큰을 넣어주세요.", "err"); setMode("settings"); return; }

  // 이 세션 객체를 잡아 두고 계속 그것만 건드린다 — 기다리는 동안
  // 사용자가 다른 세션으로 옮겨가도(wizard 가 바뀌어도) 결과는 원래
  // 요청한 세션에 그대로 쌓여야 한다.
  const session = wizard;
  session.theme = theme;
  session.slug = "r" + Date.now().toString(36);
  advanceStep(session, "recommend");
  session.busy = true; session.error = null; session.result = null;
  render();

  try{
    await dispatchRecommend(theme, session.slug);
    const result = await pollRecommend(session.slug);
    session.result = result;
    if (result.error){
      session.error = result.error;
    }else{
      // 확인된 것만 기본으로 체크한다 — RSS 확인 안 된 후보까지 그냥
      // 다 켜놓으면 죽은 피드가 그대로 딸려 들어간다.
      session.picked = new Set(
        (result.candidates || []).map((c, i) => c.verified ? i : null).filter(i => i !== null)
      );
      advanceStep(session, "pick");
    }
  }catch(e){
    session.error = e.message;
  }
  session.busy = false;
  render();
}

function wizardRetry(){
  wizard.step = "theme";
  wizard.error = null;
  render();
}

/* '추가 검색' — 지금까지 나온 이름은 빼고 새로 찾아 목록에 더한다.
   유튜브 순위는 병합 후 다시 계산해서 보여주므로(candTable 참고) 여기서는
   그냥 이어 붙이기만 하면 된다. */
async function wizardSearchMore(){
  if (!wizard || !wizard.result || wizard.searching) return;
  const session = wizard;
  const existingNames = session.result.candidates.map(c => c.name);
  session.searching = true;
  render();
  try{
    const slug = "r" + Date.now().toString(36);
    await dispatchRecommend(session.theme, slug, existingNames);
    const more = await pollRecommend(slug);
    if (more.error){
      toast("추가 검색에 실패했습니다: " + esc(more.error), "err", true);
    }else{
      const have = new Set(existingNames);
      const added = (more.candidates || []).filter(c => !have.has(c.name));
      const startIdx = session.result.candidates.length;
      session.result.candidates = session.result.candidates.concat(added);
      added.forEach((c, k) => { if (c.verified) session.picked.add(startIdx + k); });

      const kwSet = new Set(session.result.keywords || []);
      (more.keywords || []).forEach(k => kwSet.add(k));
      session.result.keywords = [...kwSet];
      const scSet = new Set(session.result.scope || []);
      (more.scope || []).forEach(s => scSet.add(s));
      session.result.scope = [...scSet];

      toast(added.length ? `${added.length}개를 더 찾았습니다.` : "새로 나온 것이 없습니다.", "ok");
    }
  }catch(e){
    toast("추가 검색을 요청하지 못했습니다: " + esc(e.message), "err", true);
  }
  session.searching = false;
  render();
}

function wizardTogglePick(i){
  if (wizard.picked.has(i)) wizard.picked.delete(i); else wizard.picked.add(i);
  render();
}

function wizardApply(){
  const chosen = (wizard.result.candidates || []).filter((_, i) => wizard.picked.has(i));
  if (!chosen.length){ toast("최소 한 개는 선택해 주세요.", "err"); return; }

  const channels = chosen
    .filter(c => c.kind === "youtube" && c.channel_id)
    .map(c => ({name: c.name, channel_id: c.channel_id, region: c.region, reason: c.reason}));
  const feeds = chosen
    .filter(c => (c.kind === "blog" || c.kind === "media") && c.url)
    .map(c => ({name: c.name, url: c.url, region: c.region, reason: c.reason}));

  const keywords = {};
  (wizard.result.keywords || []).forEach((w, i) => { keywords[w] = i < 3 ? 3 : (i < 6 ? 2 : 1); });
  if (!Object.keys(keywords).length) keywords[wizard.theme] = 3;

  let di = wizard.di;
  if (di == null){
    const key = "t" + Date.now().toString(36);
    data.digests.push({
      config: "", key, label: wizard.theme, scope: wizard.result.scope || [],
      slots: [{slot: "daily", title: `${wizard.theme} 브리핑`, send_at: "18:00",
               enabled: true, articles: 2, videos: 3}],
      keywords, queries: [], channels, feeds,
      // AI 로 한꺼번에 붙인 채널이라 사람이 하나씩 고른 게 아니다 — 발송 전에
      // 다시 한 번 주제와 맞는지 확인한다 (filter.select 의 strict).
      strict: true,
    });
    di = data.digests.length - 1;
  }else{
    // 이미 만든 주제로 '채널/키워드 선택' 단계로 되돌아와 다시 적용한
    // 경우 — 새로 만들지 않고 덮어쓴다. slots(실행 조건)는 이 단계가
    // 건드리는 값이 아니니 그대로 둔다.
    const dg = data.digests[di];
    dg.label = wizard.theme;
    dg.scope = wizard.result.scope || [];
    dg.keywords = keywords;
    dg.channels = channels;
    dg.feeds = feeds;
    dg.strict = true;
  }
  syncQueries(data.digests[di]);
  wizard.di = di;
  advanceStep(wizard, "setup");
  render(); touch();
}

function wizardGoto(step){
  advanceStep(wizard, step);
  render();
}

async function wizardTestSend(){
  if (wizard.di == null) return;
  await testSend(wizard.di, 0);
}

function wizardFinish(){
  const di = wizard.di;
  closeWizard(wizard.id);
  mode = "topics";
  if (di != null) pickTopic(di); else render();
}

function wizardStepsHTML(){
  const at = stepIndex(wizard.step);
  return `<div class="wzsteps">${WIZARD_STEPS.map(([id, label], i) => {
    const cls = i === at ? "now" : (i < at ? "done" : "");
    // '추천' 단계는 로딩/에러만 보여주는 임시 화면이라 되돌아가 다시
    // 설정할 게 없다 — 눌러도 아무 의미가 없으니 버튼으로 만들지 않는다.
    const clickable = id !== "recommend" && i !== at && i <= wizard.maxStep;
    return clickable
      ? `<button type="button" class="${cls}" onclick="wizardGoto('${id}')">${i + 1}. ${label}</button>`
      : `<span class="${cls}">${i + 1}. ${label}</span>`;
  }).join("")}</div>`;
}

function wizardStatusIcon(s){
  if (s.busy || s.searching) return '<span class="spin"></span>';
  if (s.error) return '⚠ ';
  if (s.step === "done") return '✓ ';
  return '';
}

// 여러 세션을 동시에 진행 중일 때 위에 탭으로 보여준다 — 하나가 AI 추천을
// 기다리는 동안(2~8분) 다른 테마를 새로 시작하거나 옮겨 다닐 수 있다.
function wizardTabsHTML(){
  const tabs = wizardSessions.map(s => `
    <button type="button" class="wztab ${s === wizard ? "on" : ""}" onclick="switchWizard('${s.id}')">
      ${wizardStatusIcon(s)}${esc(s.theme || "새 주제")}
      ${wizardSessions.length > 1
        ? `<span class="x" onclick="event.stopPropagation();closeWizard('${s.id}')" aria-label="닫기">×</span>`
        : ""}
    </button>`).join("");
  return `<div class="wztabs">${tabs}
    <button type="button" class="wztab plus" onclick="wizardRestart()" aria-label="새 주제 하나 더 만들기">＋</button>
  </div>`;
}

/* 채널 선택 표. 유튜브/매체/블로그 세 종류를 따로 보여준다 — 매체마다
   확인 방식이 달라서(유튜브는 구독자 수, 매체는 방문자 수, 블로그는 RSS
   살아있는지) 한 표에 욱여넣으면 뭘 보고 판단해야 할지 헷갈린다.

   유튜브 순위는 저장된 rank 를 그대로 믿지 않고 매번 다시 계산한다 —
   '추가 검색'으로 다른 요청의 결과를 이어 붙이면 rank 번호가 배치마다
   따로 매겨져 있어 그대로 쓰면 중복되거나 뒤섞인다. */
function regionLabel(r){ return r === "domestic" ? "국내" : "해외"; }

function candRowHTML(c, cols){
  const i = wizard.result.candidates.indexOf(c);
  const picked = wizard.picked.has(i);
  return `
    <tr class="${picked ? "sel" : ""}">
      <td><input type="checkbox" ${picked ? "checked" : ""} onchange="wizardTogglePick(${i})"></td>
      ${cols.map(col => `<td class="${col.cls || ""}">${col.render(c)}</td>`).join("")}
    </tr>`;
}

function candTableHTML(list, cols){
  if (!list.length) return '<div class="sub" style="margin:8px 0">해당하는 후보가 없습니다.</div>';
  return `<div class="table-wrap">
    <table class="cand-table">
      <thead><tr><th></th>${cols.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>
      <tbody>${list.map(c => candRowHTML(c, cols)).join("")}</tbody>
    </table>
  </div>`;
}

function youtubeTableHTML(cands){
  const bySubs = (a, b) => (b.subscribers || 0) - (a.subscribers || 0);
  const ordered = [
    ...cands.filter(c => c.region === "domestic").sort(bySubs),
    ...cands.filter(c => c.region === "international").sort(bySubs),
  ];
  // 순위는 지역 안에서 다시 매긴다 (병합돼도 항상 맞게).
  const ranks = new Map();
  for (const region of ["domestic", "international"]){
    cands.filter(c => c.region === region).sort(bySubs)
      .forEach((c, i) => ranks.set(c, i + 1));
  }
  const cols = [
    {label: "순위", render: c => `${regionLabel(c.region)} ${ranks.get(c)}위`},
    {label: "이름", cls: "name", render: c => esc(c.name)},
    {label: "구독자", render: c => c.subscribers != null
      ? `${c.subscribers.toLocaleString("ko-KR")}명` : "확인 안 됨"},
    {label: "사유", cls: "reason", render: c => esc(c.reason || "")},
  ];
  return candTableHTML(ordered, cols);
}

function mediaTableHTML(cands){
  const cols = [
    {label: "지역", render: c => regionLabel(c.region)},
    {label: "이름", cls: "name", render: c => esc(c.name)},
    {label: "월간 방문(추정)", render: c => c.monthly_visits != null
      ? `${c.monthly_visits.toLocaleString("ko-KR")}회` : "확인 안 됨"},
    {label: "RSS", render: c => c.verified
      ? "✓ 확인됨" : '<span class="tag unverified">확인 필요</span>'},
    {label: "사유", cls: "reason", render: c => esc(c.reason || "")},
  ];
  return candTableHTML(cands, cols);
}

function blogTableHTML(cands){
  const cols = [
    {label: "지역", render: c => regionLabel(c.region)},
    {label: "이름", cls: "name", render: c => esc(c.name)},
    {label: "RSS", render: c => c.verified
      ? "✓ 확인됨" : '<span class="tag unverified">확인 필요</span>'},
    {label: "사유", cls: "reason", render: c => esc(c.reason || "")},
  ];
  return candTableHTML(cands, cols);
}

function wizardSelectAll(){ wizard.picked = new Set((wizard.result.candidates || []).map((_, i) => i)); render(); }
function wizardSelectNone(){ wizard.picked = new Set(); render(); }
function wizardSelectVerifiedOnly(){
  wizard.picked = new Set(
    (wizard.result.candidates || []).map((c, i) => c.verified ? i : null).filter(i => i !== null)
  );
  render();
}

function renderWizard(){
  const el = $("wizardView");
  if (!wizard) startWizard();
  const w = wizard;
  const tabs = wizardTabsHTML();

  if (w.step === "theme"){
    el.innerHTML = tabs + `
      <div class="card">
        <h2>새 주제 만들기</h2>
        ${wizardStepsHTML()}
        <div class="sub">테마를 입력하면 AI 가 어울리는 유튜브 채널·매체·블로그를
          국내/해외 절반씩 찾아 추천합니다. 고른 것만 자동으로 채널·키워드로
          등록됩니다.</div>
        <div class="add" style="margin-top:10px">
          <input id="wzTheme" placeholder="예: 러닝, 홈베이킹, 스타트업 투자" enterkeyhint="done"
                 autocapitalize="off" autocomplete="off" value="${esc(w.theme)}"
                 onkeydown="if(event.key==='Enter'){event.preventDefault();wizardStartRecommend()}">
          <button class="primary" onclick="wizardStartRecommend()">AI 추천 받기</button>
        </div>
        <div class="note">
          <a href="#" onclick="event.preventDefault();
            addTopic((document.getElementById('wzTheme')||{}).value)">
            AI 추천 없이 이름만으로 빠르게 만들기 →</a>
        </div>
      </div>`;
    return;
  }

  if (w.step === "recommend"){
    el.innerHTML = tabs + `
      <div class="card">
        <h2>'${esc(w.theme)}' 추천 소스를 찾는 중</h2>
        ${wizardStepsHTML()}
        ${w.busy ? `
          <div class="sub"><span class="spin"></span>AI 가 최대 50개 후보를 만들고, 유튜브는 실제
            채널·구독자 수를, 매체·블로그는 RSS 가 살아있는지와 방문자 수를
            하나씩 확인하고 있습니다. 보통 2~8분 걸립니다.</div>` : ""}
        ${w.error ? `
          <div class="note" style="color:var(--danger)">찾지 못했습니다: ${esc(w.error)}</div>
          <div class="duo" style="margin-top:10px">
            <button class="tiny" onclick="wizardRetry()">다시 시도</button>
            <button class="tiny ghost" onclick="addTopic(${arg(w.theme)})">그냥 이름으로 만들기</button>
          </div>` : ""}
      </div>`;
    return;
  }

  if (w.step === "pick"){
    const cands = w.result.candidates || [];
    const youtube = cands.filter(c => c.kind === "youtube");
    const media = cands.filter(c => c.kind === "media");
    const blog = cands.filter(c => c.kind === "blog");
    el.innerHTML = tabs + `
      <div class="card">
        <h2>'${esc(w.theme)}' 추천 결과 ${cands.length}개</h2>
        ${wizardStepsHTML()}
        <div class="sub">유튜브는 구독자 많은 순으로 국내/해외 각각 순위를 매겼습니다. 이유를 보고
          넣을 것만 고르세요. 체크한 것만 채널·키워드로 자동 등록됩니다.</div>
        ${cands.length ? `
        <div class="row" style="gap:8px; margin-top:10px; flex-wrap:wrap">
          <button class="tiny ghost" onclick="wizardSelectAll()">전체 선택</button>
          <button class="tiny ghost" onclick="wizardSelectNone()">전체 해제</button>
          <button class="tiny ghost" onclick="wizardSelectVerifiedOnly()">확인된 것만 선택</button>
          <button class="tiny" ${w.searching ? "disabled" : ""} onclick="wizardSearchMore()">
            ${w.searching ? '<span class="spin"></span>추가 검색하는 중…' : "🔍 추가 검색"}</button>
        </div>

        <h3>유튜브 채널 (${youtube.length})</h3>
        ${youtubeTableHTML(youtube)}

        <h3>매체 (${media.length})</h3>
        ${mediaTableHTML(media)}

        <h3>블로그 (${blog.length})</h3>
        ${blogTableHTML(blog)}
        `
          : '<div class="note">실제로 확인되는 채널을 찾지 못했습니다. 이름만으로 만들거나 다시 시도해 주세요.</div>'}
        ${(w.result.keywords || []).length ? `
          <label style="margin-top:16px">같이 등록될 키워드 (${w.result.keywords.length}개)</label>
          <div class="chips">${w.result.keywords.map(k => `<span class="chip plain">${esc(k)}</span>`).join("")}</div>
        ` : ""}
        <div class="wzcount">
          <span class="sub">${w.picked.size}개 선택됨</span>
          <button class="primary" onclick="wizardApply()">선택한 것만 추가</button>
        </div>
        <div class="note" style="margin-top:10px">
          <a href="#" onclick="event.preventDefault();wizardRetry()">테마 바꿔 다시 찾기</a> ·
          <a href="#" onclick="event.preventDefault();addTopic(${arg(w.theme)})">AI 추천 없이 이름만으로 만들기</a>
        </div>
      </div>`;
    return;
  }

  if (w.step === "setup"){
    const dg = data.digests[w.di];
    el.innerHTML = tabs + `
      <div class="card">
        <h2>자동세팅 완료</h2>
        ${wizardStepsHTML()}
        <div class="note" style="color:var(--ok)">
          ✓ '${esc(dg.label)}' 주제를 만들고 채널 ${(dg.channels || []).length}개 ·
          블로그/매체 ${(dg.feeds || []).length}개 · 키워드 ${Object.keys(dg.keywords).length}개를
          등록했습니다. AI 로 붙인 채널이라, 발송 전 주제와 맞는 내용인지 한 번 더
          확인하도록 켜 두었습니다.
        </div>
        <button class="primary wide" style="margin-top:12px"
                onclick="wizardGoto('condition')">다음: 보낼 시간 정하기</button>
      </div>`;
    return;
  }

  if (w.step === "condition"){
    const dg = data.digests[w.di];
    el.innerHTML = tabs + `
      <div class="card">
        <h2>실행 조건 — 언제, 몇 건씩 보낼지</h2>
        ${wizardStepsHTML()}
        ${slotCardHTML(w.di, 0, dg.slots[0], "wz")}
        <button class="primary wide" style="margin-top:14px"
                onclick="wizardGoto('test')">다음: 테스트 발송</button>
      </div>`;
    return;
  }

  if (w.step === "test"){
    el.innerHTML = tabs + `
      <div class="card">
        <h2>테스트 발송</h2>
        ${wizardStepsHTML()}
        <div class="sub">지금 한 번 보내서 텔레그램에 어떻게 오는지 확인해 보세요.
          이미 보낸 것으로 치지 않으니 정식 발송 시각에도 그대로 나갑니다.</div>
        <button class="primary wide" style="margin-top:10px" onclick="wizardTestSend()">지금 테스트 발송</button>
        <button class="tiny wide ghost" style="margin-top:10px" onclick="wizardGoto('done')">건너뛰고 완료</button>
      </div>`;
    return;
  }

  // done
  const dg = data.digests[w.di];
  el.innerHTML = tabs + `
    <div class="card">
      <h2>설정 완료</h2>
      ${wizardStepsHTML()}
      <div class="note" style="color:var(--ok)">
        ✓ '${esc(dg.label)}' 주제가 앞으로 지정한 시각에 자동으로 발송됩니다.
      </div>
      <div class="duo" style="margin-top:12px">
        <button class="primary" onclick="wizardFinish()">주제 목록에서 보기</button>
        <button class="ghost" onclick="exportSourcesCSV(${w.di})">CSV로 내보내기</button>
      </div>
      <button class="tiny wide dashed" style="margin-top:10px" onclick="wizardRestart()">
        ＋ 새 주제 하나 더 만들기</button>
    </div>`;
}

function wizardRestart(){ startWizard(); render(); }

/* ---------- 구독 채널 — Google Drive 에서 읽어 이 브라우저에만 보관 ---------- */
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly";
let subs = JSON.parse(localStorage.getItem("subs") || "[]");
let subsMeta = JSON.parse(localStorage.getItem("subs_meta") || "null");
let gToken = null, gTokenAt = 0;

function subsInfo(){
  const el = $("subsInfo");
  if (!el) return;
  el.textContent = subs.length
    ? `${subs.length}개 채널 · ${subsMeta ? subsMeta.name : "출처 미상"}`
      + (subsMeta ? ` (${new Date(subsMeta.at).toLocaleString("ko-KR")})` : "")
    : "아직 불러오지 않았습니다.";
}

function renderDrive(){
  const cid = localStorage.getItem("g_client_id");

  // 연결 전에는 맨 위에 띄운다. 고급 설정 안에 두면 찾지 못한다.
  $("driveSetup").innerHTML = cid ? "" : `
    <div class="card" style="border-color:var(--accent)">
      <h2>구독 채널 불러오기</h2>
      <div class="sub">Google Drive 에 있는 Takeout 파일을 읽어옵니다.
        주제를 만들 때 채널이 자동으로 붙습니다.</div>
      <div class="note">
        한 번만 준비하면 됩니다. 구글 OAuth <b>클라이언트 ID</b> 는 비밀값이
        아니고 이 기기에만 둡니다.
        <ol style="margin:8px 0 0; padding-left:18px">
          <li><a href="https://console.cloud.google.com/apis/library/drive.googleapis.com"
                 target="_blank" rel="noopener" style="color:var(--accent)">Drive API 사용 설정</a></li>
          <li><a href="https://console.cloud.google.com/auth/overview"
                 target="_blank" rel="noopener" style="color:var(--accent)">OAuth 동의 화면</a>
              → 대상 <b>외부</b>, <b>테스트 사용자에 본인 계정 추가</b>
              <span style="color:var(--danger)">(빠뜨리면 access_denied)</span></li>
          <li><a href="https://console.cloud.google.com/apis/credentials/oauthclient"
                 target="_blank" rel="noopener" style="color:var(--accent)">OAuth 클라이언트 ID 만들기</a>
              → 유형 <b>웹 애플리케이션</b></li>
          <li>승인된 자바스크립트 출처에 <b>${location.origin}</b><br>
              (뒤에 <b>/</b> 나 경로를 붙이지 마세요)</li>
          <li><b>여기</b>에 만들어진 ID 붙여넣기 ↓</li>
        </ol>
      </div>
      <div class="add">
        <input id="gcid" placeholder="....apps.googleusercontent.com" enterkeyhint="done"
               autocapitalize="off" autocomplete="off"
               onkeydown="if(event.key==='Enter'){event.preventDefault();saveClientId()}">
        <button class="primary" onclick="saveClientId()">저장</button>
      </div>
      <button class="tiny wide ghost" onclick="driveCheck()">연결 점검</button>
    </div>`;

  $("driveBox").innerHTML = !cid ? `
    <div class="note">맨 위 <b>구독 채널 불러오기</b> 카드에서 먼저 연결해 주세요.</div>` : `
    <div class="duo" style="margin-top:10px">
      <button class="tiny primary" onclick="driveOpen()">Drive 에서 고르기</button>
      <button class="tiny" onclick="driveResync()" ${subsMeta ? "" : "disabled"}>다시 동기화</button>
    </div>
    <div class="chips" id="driveList"></div>
    <div class="duo" style="margin-top:10px">
      <button class="tiny ghost" onclick="clearSubs()">목록 지우기</button>
      <button class="tiny ghost" onclick="clearClientId()">클라이언트 ID 바꾸기</button>
    </div>
    <button class="tiny wide ghost" onclick="driveCheck()">연결 점검</button>
    <div class="note">
      읽기 전용으로 연결하고, 받은 목록은 이 브라우저에만 둡니다.
      저장소는 공개라 주제에 실제로 넣은 채널만 커밋됩니다.
    </div>`;
  subsInfo();
}
function saveClientId(){
  const v = ($("gcid") || {}).value?.trim();
  if (!v){ toast("클라이언트 ID 를 붙여넣어 주세요.", "err"); return; }
  localStorage.setItem("g_client_id", v); renderDrive();
  $("advanced").open = true;
  toast("연결했습니다. Drive 에서 파일을 고르는 중…", "busy");
  driveOpen();
}
function clearClientId(){
  localStorage.removeItem("g_client_id"); localStorage.removeItem("g_granted");
  gToken = null; renderDrive();
}

/* 구글 토큰. 한 번 허락하면 다음부터는 창 없이 조용히 받아온다. */
/* 구글이 돌려주는 코드를 그대로 두면 뭘 고쳐야 할지 알 수 없다. 짚어준다. */
const G_HELP = {
  popup_failed_to_open: "브라우저가 팝업을 막았습니다. 팝업 차단을 풀고 다시 눌러주세요.",
  popup_closed: "창을 닫으셨습니다. 다시 눌러주세요.",
  access_denied: "구글이 거부했습니다. Cloud Console → OAuth 동의 화면 → "
    + "<b>대상(테스트 사용자)</b> 에 지금 로그인한 계정을 추가했는지 확인해 주세요.",
  admin_policy_enforced: "조직 정책이 막았습니다. 개인 구글 계정으로 해보세요.",
  invalid_client: "클라이언트 ID 가 맞지 않습니다. 다시 복사해 넣어주세요.",
  unregistered_origin: `승인된 자바스크립트 출처에 <b>${location.origin}</b> 이 없습니다.`,
  idpiframe_initialization_failed: `승인된 자바스크립트 출처에 <b>${location.origin}</b> 을 넣어주세요.`,
};
function gError(code){
  const key = String(code || "unknown");
  const e = new Error((G_HELP[key] || "권한을 받지 못했습니다.") + ` (코드: ${esc(key)})`);
  e.code = key;
  return e;
}

function requestToken(prompt){
  return new Promise((resolve, reject) => {
    const oauth = window.google && google.accounts && google.accounts.oauth2;
    if (!oauth) return reject(new Error("구글 스크립트를 불러오지 못했습니다. 새로고침해 주세요."));
    const client_id = localStorage.getItem("g_client_id");
    if (!client_id) return reject(new Error("먼저 클라이언트 ID 를 넣어주세요."));
    oauth.initTokenClient({
      client_id, scope: DRIVE_SCOPE,
      callback: r => r && r.access_token
        ? resolve(r.access_token)
        : reject(gError(r && (r.error || r.type))),
      error_callback: e => reject(gError(e && (e.type || e.message))),
    }).requestAccessToken({prompt});
  });
}

/* 처음에는 반드시 동의 화면을 띄워야 한다.
   prompt:"" 는 '아무것도 묻지 마라' 라서, 동의한 적이 없으면 구글이 묻지 않고
   그냥 거부한다. 한 번 허락받은 뒤에만 조용히 받는다. */
async function googleToken(){
  if (gToken && Date.now() - gTokenAt < 50 * 60 * 1000) return gToken;
  const granted = localStorage.getItem("g_granted") === "1";
  try{
    gToken = await requestToken(granted ? "" : "consent");
  }catch(e){
    if (!granted) throw e;
    // 조용히 받기가 안 되면 허락이 풀린 것이다. 다음 누름에서 동의 화면을 띄운다.
    localStorage.removeItem("g_granted");
    throw new Error("구글 권한이 풀렸습니다. 한 번 더 눌러주세요.");
  }
  gTokenAt = Date.now();
  localStorage.setItem("g_granted", "1");
  return gToken;
}

/* 무엇이 준비됐고 무엇이 아닌지 화면에 적어준다 */
function driveCheck(){
  const cid = localStorage.getItem("g_client_id") || "";
  const rows = [
    [!!(window.google && google.accounts && google.accounts.oauth2),
     "구글 로그인 스크립트", "차단됐거나 아직 안 왔습니다 — 새로고침"],
    [/\.apps\.googleusercontent\.com$/.test(cid),
     "클라이언트 ID 형식", "…apps.googleusercontent.com 으로 끝나야 합니다"],
    [location.protocol === "https:",
     "https 로 열림", "http 로는 구글 로그인이 안 됩니다"],
  ];
  const lines = rows.map(([ok, label, bad]) =>
    `${ok ? "✓" : "✗"} ${label}${ok ? "" : " — " + bad}`);
  lines.push(`이 페이지 출처: <b>${location.origin}</b><br>`
    + "→ Cloud Console 의 <b>승인된 자바스크립트 출처</b> 에 이 값이 그대로 있어야 합니다"
    + " (뒤에 / 나 경로를 붙이면 안 됩니다)");
  toast(lines.join("<br>"), rows.every(r => r[0]) ? "ok" : "err", true);
}

async function drive(path){
  const r = await fetch("https://www.googleapis.com/drive/v3/" + path,
                        {headers: {Authorization: "Bearer " + gToken}});
  if (!r.ok){
    const j = await r.json().catch(() => ({}));
    throw new Error(`${r.status} ${(j.error && j.error.message) || r.statusText}`);
  }
  return r;
}

/* Takeout 은 zip 으로도, 풀어놓은 csv 로도 있을 수 있어 둘 다 받는다 */
const DRIVE_Q = "trashed=false and (mimeType='text/csv' or mimeType='application/zip'"
  + " or mimeType='application/x-zip-compressed'"
  + " or mimeType='application/vnd.google-apps.spreadsheet')";

async function driveOpen(){
  try{
    toast("구글 계정에 연결하는 중…", "busy");
    await googleToken();
    toast("Drive 를 찾는 중…", "busy");
    const r = await drive("files?" + new URLSearchParams({
      q: DRIVE_Q, orderBy: "modifiedTime desc", pageSize: "25",
      fields: "files(id,name,mimeType,modifiedTime)",
    }));
    const files = (await r.json()).files || [];
    if (!files.length){ toast("Drive 에서 csv·zip 파일을 찾지 못했습니다.", "err"); return; }
    $("driveList").innerHTML = files.map(f => `
      <span class="chip plain"><button style="width:auto;padding:0;font-size:14px;color:var(--accent)"
        onclick="drivePick('${f.id}','${esc(f.name).replace(/'/g, "&#39;")}','${f.mimeType}')"
        >${esc(f.name)}</button></span>`).join("");
    toast("불러올 파일을 골라주세요.", "ok");
  }catch(e){ toast("Drive 를 열지 못했습니다: " + esc(e.message), "err", true); }
}

async function drivePick(id, name, mimeType){
  try{
    toast(`${esc(name)} 을(를) 읽는 중…`, "busy");
    const text = await driveText(id, name, mimeType);
    useSubs(text, {id, name, mimeType});
    $("driveList").innerHTML = "";
  }catch(e){ toast("읽지 못했습니다: " + esc(e.message), "err", true); }
}
async function driveResync(){
  if (!subsMeta) return;
  try{
    toast("다시 동기화하는 중…", "busy");
    await googleToken();
    const text = await driveText(subsMeta.id, subsMeta.name, subsMeta.mimeType);
    useSubs(text, subsMeta);
  }catch(e){ toast("동기화하지 못했습니다: " + esc(e.message), "err", true); }
}

async function driveText(id, name, mimeType){
  if (mimeType === "application/vnd.google-apps.spreadsheet"){
    const r = await drive(`files/${id}/export?mimeType=text/csv`);
    return r.text();
  }
  const r = await drive(`files/${id}?alt=media`);
  const buf = await r.arrayBuffer();
  const zip = /\.zip$/i.test(name) || mimeType.includes("zip");
  return zip ? unzipSubscriptions(buf) : new TextDecoder("utf-8").decode(buf);
}

function useSubs(text, meta){
  const parsed = parseSubs(text);
  if (!parsed.length){
    toast("채널을 찾지 못했습니다. 유튜브 구독정보가 든 파일이 맞는지 확인해 주세요.", "err", true);
    return;
  }
  subs = parsed;
  subsMeta = {id: meta.id, name: meta.name, mimeType: meta.mimeType, at: Date.now()};
  localStorage.setItem("subs", JSON.stringify(subs));
  localStorage.setItem("subs_meta", JSON.stringify(subsMeta));
  renderDrive();

  // 목록이 없던 때 만든 주제는 채널이 비어 있다. 이제 붙일 수 있다고 알린다.
  const empty = (data && data.digests || []).filter(d => !d.config && !(d.channels || []).length);
  if (empty.length){
    toast(`${subs.length}개 채널을 불러왔습니다.
           <div style="margin-top:6px">채널이 비어 있는 주제 ${empty.length}개가 있습니다 —
           <a href="#" onclick="reMatchAll();return false">이름으로 찾아 붙이기</a></div>`, "ok", true);
    return;
  }
  toast(`${subs.length}개 채널을 불러왔습니다. 이 기기에만 저장됩니다.`, "ok");
}

/* 채널이 비어 있는 주제에만 붙인다. config 파일이 있는 주제는 거기에 목록이 있다. */
function reMatchAll(){
  const hit = [];
  data.digests.forEach((dg, di) => {
    if (dg.config || (dg.channels || []).length) return;
    const found = matchChannels(dg.label);
    if (!found.length) return;
    dg.channels = found;
    open.add("d" + di);
    hit.push(`${dg.label} ${found.length}개`);
  });
  if (!hit.length){
    toast("이름이 맞는 구독 채널을 찾지 못했습니다. 검색어로도 영상은 들어옵니다.", "err", true);
    return;
  }
  render(); touch();
  toast(`${esc(hit.join(", "))} 붙였습니다.`, "busy");
}

/* 채널 ID 열의 자리가 언어판마다 달라 값으로 찾는다 */
function parseSubs(text){
  const out = [], seen = new Set();
  for (const line of text.split(/\r?\n/)){
    if (!line.trim()) continue;
    const cols = line.split(",").map(c => c.trim().replace(/^"|"$/g, ""));
    const id = cols.find(c => /^UC[\w-]{22}$/.test(c));
    if (!id || seen.has(id)) continue;
    const title = cols.filter(c => c !== id && c && !/^https?:/i.test(c)).pop();
    if (!title) continue;
    seen.add(id); out.push({id, title});
  }
  return out;
}

/* zip 안의 구독정보 csv 하나만 꺼낸다. 라이브러리 없이 중앙 디렉터리를 읽는다. */
async function unzipSubscriptions(buf){
  const view = new DataView(buf), u8 = new Uint8Array(buf);
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 66000); i--){
    if (view.getUint32(i, true) === 0x06054b50){ eocd = i; break; }
  }
  if (eocd < 0) throw new Error("zip 을 읽지 못했습니다.");
  const count = view.getUint16(eocd + 10, true);
  let off = view.getUint32(eocd + 16, true);
  if (off === 0xffffffff) throw new Error("4GB 가 넘는 zip 은 못 읽습니다. csv 만 따로 올려주세요.");

  const dec = new TextDecoder("utf-8");
  for (let n = 0; n < count; n++){
    if (view.getUint32(off, true) !== 0x02014b50) break;
    const method = view.getUint16(off + 10, true);
    const csize  = view.getUint32(off + 20, true);
    const nameLen = view.getUint16(off + 28, true);
    const extraLen = view.getUint16(off + 30, true);
    const cmtLen = view.getUint16(off + 32, true);
    const local = view.getUint32(off + 42, true);
    const name = dec.decode(u8.subarray(off + 46, off + 46 + nameLen));

    if (/\.csv$/i.test(name) && /(subscriptions|구독)/i.test(name)){
      const lNameLen = view.getUint16(local + 26, true);
      const lExtraLen = view.getUint16(local + 28, true);
      const start = local + 30 + lNameLen + lExtraLen;
      const data = u8.subarray(start, start + csize);
      if (method === 0) return dec.decode(data);
      if (method !== 8) throw new Error("이 zip 의 압축 방식은 못 풉니다.");
      const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
      return new Response(stream).text();
    }
    off += 46 + nameLen + extraLen + cmtLen;
  }
  throw new Error("zip 안에서 구독정보 csv 를 찾지 못했습니다.");
}

function clearSubs(){
  subs = []; subsMeta = null;
  localStorage.removeItem("subs"); localStorage.removeItem("subs_meta");
  renderDrive();
  toast("구독 목록을 지웠습니다.", "ok");
}
function searchSubs(di){
  const needle = $("cs" + di).value.trim().toLowerCase();
  const box = $("cr" + di);
  if (!needle){ box.innerHTML = ""; return; }
  if (!subs.length){ box.innerHTML = '<span class="sub">먼저 고급 설정에서 Drive 구독 목록을 불러오세요.</span>'; return; }
  const taken = new Set((data.digests[di].channels || []).map(c => c.channel_id));
  const hits = subs.filter(s => s.title.toLowerCase().includes(needle) && !taken.has(s.id)).slice(0, 12);
  box.innerHTML = hits.length
    ? hits.map(h => `<span class="chip plain"><button onclick="pickChannel(${di},'${h.id}')"
        style="width:auto;padding:0;font-size:14px;color:var(--accent)">+ ${esc(h.title)}</button></span>`).join("")
    : '<span class="sub">일치하는 채널이 없습니다.</span>';
}
function pickChannel(di, id){
  const hit = subs.find(s => s.id === id);
  if (!hit) return;
  (data.digests[di].channels ||= []).push({name: hit.title, channel_id: hit.id});
  open.add("d" + di);
  render(); touch();
}
function delChannel(di, ci){ data.digests[di].channels.splice(ci, 1); render(); touch(); }

/* 네이버 블로그는 주소 모양이 여러 가지다. 아이디만 뽑아 RSS 주소로 만든다.
   검색 API 는 NAVER API HUB 로 옮겨가 키가 따로 필요하지만, 블로그별 RSS 는
   키 없이 그대로 된다. */
function blogId(text){
  let s = String(text).trim();
  if (!s) return "";
  s = s.replace(/^@/, "");
  // https://blog.naver.com/아이디/223... · m.blog.naver.com/아이디 · rss.blog.naver.com/아이디.xml
  const m = s.match(/(?:^|\/\/)(?:m\.|rss\.)?blog\.naver\.com\/([A-Za-z0-9_-]+)/);
  if (m) return m[1].replace(/\.xml$/i, "");
  if (/^[A-Za-z0-9_-]+$/.test(s)) return s;
  return "";
}

/* 아이디만으로는 못 가져오는 곳들.
   인스타는 Basic Display API 가 2024-12-04 에 닫혔고, Graph API 는 내가
   소유·인증한 비즈니스 계정만 읽는다. 남의 공개 계정을 부를 방법이 공식으로
   없다. 페이스북도 같다. 주소만 받아 두면 눌러도 아무것도 안 들어오는
   칸이 되므로, 받지 않고 무엇을 넣어야 하는지 알려준다. */
const NEEDS_BRIDGE = [
  [/(^|\/\/|\.)instagram\.com/i, "인스타그램"],
  [/(^|\/\/|\.)(facebook|fb)\.com/i, "페이스북"],
  [/(^|\/\/|\.)(x|twitter)\.com/i, "X(트위터)"],
];
function needsBridge(text){
  const hit = NEEDS_BRIDGE.find(([re]) => re.test(String(text)));
  return hit ? hit[1] : "";
}

/* 피드 이름. 주소만 늘어놓으면 칩에서 구분이 안 된다. */
function feedName(url){
  try{
    const u = new URL(url);
    const path = u.pathname.replace(/\/(rss|feed|atom)(\.xml)?\/?$/i, "")
                           .replace(/\.(xml|rss|atom)$/i, "").replace(/^\/|\/$/g, "");
    const host = u.hostname.replace(/^(www|rss|feeds?)\./, "");
    return path ? `${host}/${path.split("/").pop()}` : host;
  }catch{ return url; }
}

function addFeed(di){
  const el = $("bl" + di);
  const raw = (el.value || "").trim();
  if (!raw) return;

  const blocked = needsBridge(raw);
  if (blocked){
    toast(`${esc(blocked)} 은(는) 주소만으로는 가져올 수 없습니다. `
        + `RSS 주소로 바꾼 뒤 그 주소를 넣어주세요. `
        + `<a href="https://rss.app/rss-feed" target="_blank" rel="noopener"
             style="color:var(--accent)">RSS 주소 만들기 →</a>`, "err", true);
    return;
  }

  // 네이버를 먼저 본다. blog.naver.com/아이디/223456789 은 글 주소라
  // 그대로 쓰면 안 되고 RSS 주소로 바꿔야 한다.
  let url, name;
  const naver = /blog\.naver\.com/i.test(raw) || /^@?[A-Za-z0-9_-]+$/.test(raw);
  if (naver){
    const id = blogId(raw);
    if (!id){ toast("네이버 블로그 아이디를 읽을 수 없습니다.", "err"); return; }
    url = `https://rss.blog.naver.com/${id}.xml`; name = id;
  }else if (/^https?:\/\//i.test(raw)){
    // 그 밖의 주소는 그대로 쓴다 — 인스타·페이스북을 바꾼 RSS 주소,
    // 브런치·티스토리·서브스택 등 무엇이든 들어온다.
    url = raw; name = feedName(raw);
  }else{
    toast("RSS 주소, 또는 네이버 블로그 아이디를 넣어주세요.", "err");
    return;
  }

  const feeds = (data.digests[di].feeds ||= []);
  if (feeds.some(f => f.url === url)){ toast(`'${esc(name)}' 은(는) 이미 있습니다.`, "err"); return; }
  feeds.push({name, url});
  el.value = "";
  open.add("d" + di);
  render(); touch();
  toast(`'${esc(name)}' 을(를) ${esc(data.digests[di].label)} 에 넣었습니다.`, "busy");
}
function delFeed(di, fi){ data.digests[di].feeds.splice(fi, 1); render(); touch(); }

/* ---------- GitHub ---------- */
const token = () => localStorage.getItem("gh_token") || "";
const api = path => `https://api.github.com/repos/${REPO}/contents/${path}`;
const headers = () => ({Authorization: "Bearer " + token(), Accept: "application/vnd.github+json"});
const enc = t => btoa(unescape(encodeURIComponent(t)));
const dec = c => decodeURIComponent(escape(atob(c.replace(/\n/g, ""))));

async function getFile(path){
  const h = {Accept: "application/vnd.github+json"};
  if (token()) h.Authorization = "Bearer " + token();
  const r = await fetch(`${api(path)}?ref=${encodeURIComponent(BRANCH)}`, {headers: h, cache: "no-store"});
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
async function deleteFile(path, shaRef, message){
  const r = await fetch(api(path), {
    method: "DELETE", headers: headers(),
    body: JSON.stringify({message, sha: shaRef, branch: BRANCH}),
  });
  if (!r.ok){
    const j = await r.json().catch(() => ({}));
    const err = new Error(`${r.status} ${j.message || r.statusText}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}
async function putFile(path, text, shaRef, message){
  const body = {message, content: enc(text), branch: BRANCH};
  if (shaRef) body.sha = shaRef;
  const r = await fetch(api(path), {method: "PUT", headers: headers(), body: JSON.stringify(body)});
  if (!r.ok){
    const j = await r.json().catch(() => ({}));
    const err = new Error(`${r.status} ${j.message || r.statusText}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

/* GET /repos 의 permissions 는 '내가' 이 저장소에 갖는 권한이라 토큰이 무엇을
   받았는지는 알려주지 않는다. 공개 저장소라 읽기는 누구나 되므로, 토큰이
   무엇을 못 하는지 확인할 수 있는 건 계정뿐이다. 나머지는 짚어만 준다. */
const OWNER = REPO.split("/")[0];
let authProblem = null;

async function tokenOwner(){
  try{
    const r = await fetch("https://api.github.com/user", {headers: headers()});
    if (r.status === 401) return {bad: "토큰이 만료됐거나 잘못됐습니다. 새로 발급해 넣어주세요."};
    if (!r.ok) return {bad: `계정을 확인하지 못했습니다 (${r.status}).`};
    return {login: (await r.json()).login};
  }catch(e){ return {bad: "계정을 확인하지 못했습니다: " + e.message}; }
}

/* 403 은 fine-grained 토큰이 이 저장소에 대해 Contents 쓰기를 못 받았다는 뜻이다.
   원인이 두 가지뿐이라 둘 다 짚어주고, 계정만 실제로 확인해 준다. */
async function showAuthProblem(detail){
  const who = await tokenOwner();
  authProblem = {
    detail,
    account: who.bad
      ? {bad: true, text: who.bad}
      : who.login === OWNER
        ? {bad: false, text: `토큰 계정 ${who.login} — 저장소 주인과 같습니다.`}
        : {bad: true, text: `토큰이 <b>${esc(who.login)}</b> 계정 것입니다. `
            + `${esc(OWNER)} 계정으로 다시 발급해 주세요.`},
  };
  renderToken();
}
function clearAuthProblem(){ if (authProblem){ authProblem = null; renderToken(); } }

async function checkToken(){
  if (!token()){ toast("먼저 토큰을 넣어주세요.", "err"); return; }
  toast("토큰을 확인하는 중…", "busy");
  const who = await tokenOwner();
  if (who.bad){ await showAuthProblem(who.bad); toast(who.bad, "err", true); return; }
  if (who.login !== OWNER){
    await showAuthProblem("토큰 계정이 저장소 주인과 다릅니다.");
    toast(`토큰이 ${esc(who.login)} 계정 것입니다.`, "err", true);
    return;
  }
  // 쓰기 권한은 실제로 써 봐야만 알 수 있다 — 내용을 그대로 다시 올려 확인한다.
  try{
    const cur = await getFile(FILE);
    const r = await putFile(FILE, dec(cur.content), cur.sha, "설정 확인 (모바일 어드민)");
    sha = r.content.sha;
    clearAuthProblem();
    toast("토큰 정상 — 방금 실제로 써 봤고 됩니다.", "ok");
  }catch(e){
    await showAuthProblem(`${e.status || ""} ${e.message}`.trim());
    toast("쓰지 못했습니다. 아래 안내를 확인해 주세요.", "err");
  }
}

function authProblemCard(){
  const a = authProblem;
  return `
    <div class="card" style="border-color:var(--danger)">
      <h2 style="color:var(--danger)">저장 권한이 없습니다</h2>
      <div class="sub">${esc(a.detail)}</div>
      <div class="note" style="color:${a.account.bad ? "var(--danger)" : "var(--ok)"}">
        ${a.account.bad ? "⚠" : "✓"} ${a.account.text}
      </div>
      <div class="note">
        읽기는 공개 저장소라 토큰 없이도 됩니다. 그래서 화면은 멀쩡히 보이다가
        저장할 때 처음 막힙니다. 토큰 설정에서 <b>둘 다</b> 확인해 주세요.
        <ol style="margin:8px 0 0; padding-left:18px">
          <li><b>Repository access</b> → Only select repositories 에
              <b>${esc(REPO)}</b> 가 들어 있는지</li>
          <li><b>Repository permissions → Contents</b> 가
              <b>Read and write</b> 인지 (Read-only 면 저장되지 않습니다)</li>
        </ol>
        고친 뒤 <b>Update</b> 를 눌러야 적용됩니다. 새 토큰을 만들 필요는 없습니다.
      </div>
      <a class="note" style="display:block;color:var(--accent)"
         href="https://github.com/settings/personal-access-tokens" target="_blank"
         rel="noopener">내 토큰 목록 열기 →</a>
      <div class="duo" style="margin-top:12px">
        <button class="tiny" onclick="checkToken()">고쳤어요, 다시 확인</button>
        <button class="tiny ghost" onclick="clearToken()">다른 토큰 넣기</button>
      </div>
    </div>`;
}

// 암호를 잊었을 때 토큰을 직접 넣는 칸으로 돌아가는 길
let forceTokenInput = false;
function showTokenInput(){ forceTokenInput = true; renderToken(); }

/* 공통 설정 화면에 넣기 전에는 토큰·잠금 카드가 늘 눈에 띄었다. 이제는
   화면 맨 위에 막힌 상태만 알약 하나로 알리고, 눌러야 공통 설정이 열린다. */
function updateStatusPill(){
  const pill = $("statusPill");
  if (!pill) return;
  if (authProblem){ pill.hidden = false; pill.textContent = "⚠ 저장 권한 문제"; return; }
  if (!token() && lockedBox){ pill.hidden = false; pill.textContent = "🔒 잠금 풀기 필요"; return; }
  if (!token()){ pill.hidden = false; pill.textContent = "⚠ 토큰 필요"; return; }
  pill.hidden = true;
}

function renderToken(){
  const has = !!token();
  if (has && authProblem){ $("tokenCard").innerHTML = authProblemCard(); updateStatusPill(); return; }
  // 토큰은 없는데 잠가 둔 것이 있으면, 붙여넣기보다 암호 한 줄이 빠르다
  if (!has && lockedBox && !forceTokenInput){
    $("tokenCard").innerHTML = unlockCard();
    updateStatusPill();
    return;
  }
  $("tokenCard").innerHTML = (has ? `
    <div class="card" style="padding:10px 14px">
      <div class="row" style="justify-content:space-between">
        <span class="sub" style="color:var(--ok)">✓ GitHub 토큰 등록됨</span>
        <span class="row" style="gap:6px">
          <button class="tiny ghost" onclick="checkToken()">점검</button>
          <button class="tiny ghost" onclick="clearToken()">지우기</button>
        </span>
      </div>
    </div>` + lockCard() : `
    <div class="card" style="border-color:var(--accent)">
      <h2>먼저 GitHub 토큰을 넣어주세요</h2>
      <div class="sub">이걸 넣어야 바꾼 값이 저장됩니다. 이 기기에만 저장됩니다.</div>
      <div class="row" style="margin-top:10px">
        <input id="token" type="password" placeholder="github_pat_..."
               name="gh-token" autocomplete="current-password" enterkeyhint="done"
               onkeydown="if(event.key==='Enter'){event.preventDefault();saveToken()}">
      </div>
      <button class="primary wide" onclick="saveToken()">토큰 저장</button>
      <div class="note">
        <a href="https://github.com/settings/personal-access-tokens/new" target="_blank"
           rel="noopener" style="color:var(--accent)">토큰 만들러 가기 →</a><br>
        Repository access 는 <b>Only select repositories → ${REPO}</b>,<br>
        Repository permissions 의 <b>Contents</b> 를 <b>Read and write</b> 로.
        (Read-only 면 저장할 때 403 이 납니다)
      </div>
    </div>`);
  updateStatusPill();
}

function saveToken(){
  const v = ($("token") || {}).value?.trim();
  if (!v){ toast("토큰을 붙여넣어 주세요.", "err"); return; }
  localStorage.setItem("gh_token", v);
  renderToken();
  toast("토큰을 이 기기에 저장했습니다.", "ok");
  if (dirty) save();
}
function clearToken(){
  localStorage.removeItem("gh_token"); authProblem = null; renderToken();
  toast("토큰을 지웠습니다.", "ok");
}

/* ---------- 잠가서 어디서든 쓰기 ----------
   토큰은 이 기기에만 남는다. 폰에서 넣은 것이 회사 PC 로 따라가지 않아
   기기를 옮길 때마다 다시 붙여넣어야 했다.

   서버가 없고 저장소가 공개라 토큰을 그대로 둘 수는 없다. 암호로 잠근
   덩어리만 페이지 옆에 두고, 어느 기기에서든 암호를 쳐서 푼다. */

let lockedBox = null;      // 저장소에 잠긴 토큰이 있으면 그 내용
let lockChecked = false;

/* 페이지와 같은 자리에서 받는다. 깃허브 API 는 토큰 없이 부르면 IP 당
   시간당 60회라, 회사처럼 여럿이 한 주소를 쓰면 금세 바닥난다. */
async function fetchLocked(){
  if (lockChecked) return lockedBox;
  lockChecked = true;
  try{
    const r = await fetch("./token.enc", {cache: "no-store"});
    if (r.ok) lockedBox = await r.text();
  }catch{ /* 없으면 없는 대로 둔다 */ }
  return lockedBox;
}

async function unlockToken(){
  const el = $("pass");
  const pass = (el || {}).value || "";
  if (!pass){ toast("암호를 넣어주세요.", "err"); return; }
  toast("푸는 중…", "busy");
  try{
    const value = await unlock(await fetchLocked(), pass);
    localStorage.setItem("gh_token", value);
    if (el) el.value = "";
    authProblem = null; renderToken();
    toast("토큰을 풀었습니다. 이 기기에 저장해 두었습니다.", "ok");
    load();
  }catch(e){ toast(esc(e.message), "err"); }
}

async function lockToken(){
  if (!token()){ toast("먼저 토큰을 넣어주세요.", "err"); return; }
  const pass = ($("newPass") || {}).value || "";
  const again = ($("newPass2") || {}).value || "";
  if (pass !== again){ toast("두 암호가 다릅니다.", "err"); return; }
  if (pass.length < MIN_PASSPHRASE){
    toast(`암호는 ${MIN_PASSPHRASE}자 이상이어야 합니다.`, "err"); return;
  }
  toast("잠그는 중…", "busy");
  try{
    const box = await lock(token(), pass);
    const cur = await getFile(LOCK_FILE);
    await putFile(LOCK_FILE, box, cur && cur.sha, "토큰 잠금 갱신 (어드민)");
    lockedBox = box; lockChecked = true;
    renderToken();
    toast("잠갔습니다. 1분쯤 뒤부터 다른 기기에서 암호로 열 수 있습니다.", "ok", true);
  }catch(e){
    if (e.status === 401 || e.status === 403) await showAuthProblem(`${e.status} ${e.message}`);
    toast("잠그지 못했습니다: " + esc(e.message), "err");
  }
}

async function removeLock(){
  if (!lockedBox){ toast("잠긴 토큰이 없습니다.", "err"); return; }
  toast("지우는 중…", "busy");
  try{
    const cur = await getFile(LOCK_FILE);
    if (cur) await deleteFile(LOCK_FILE, cur.sha, "토큰 잠금 삭제 (어드민)");
    lockedBox = null;
    renderToken();
    toast("잠긴 토큰을 지웠습니다.", "ok");
  }catch(e){ toast("지우지 못했습니다: " + esc(e.message), "err"); }
}

function lockCard(){
  const has = !!lockedBox;
  return `
    <details class="card" id="lockCard"${has ? "" : ""}>
      <summary>다른 기기에서도 쓰기 ${has ? "· 잠금 있음" : "· 없음"}</summary>
      <div class="note">
        토큰은 이 기기에만 남습니다. 암호로 잠가 두면 어느 기기에서든
        페이지를 열고 암호만 쳐서 꺼내 쓸 수 있습니다.
        <b>잠긴 덩어리는 공개된 자리에 놓입니다</b> — 암호가 짧으면
        시간을 들여 풀 수 있으니 ${MIN_PASSPHRASE}자 이상으로, 다른 곳에
        쓰지 않는 것으로 정하세요.
      </div>
      <label style="margin-top:10px">암호</label>
      <input id="newPass" type="password" autocomplete="new-password"
             placeholder="${MIN_PASSPHRASE}자 이상" enterkeyhint="next">
      <label style="margin-top:8px">암호 확인</label>
      <input id="newPass2" type="password" autocomplete="new-password"
             placeholder="한 번 더" enterkeyhint="done"
             onkeydown="if(event.key==='Enter'){event.preventDefault();lockToken()}">
      <button class="primary wide" style="margin-top:10px" onclick="lockToken()">
        ${has ? "암호 바꿔 다시 잠그기" : "잠가서 저장소에 두기"}</button>
      ${has ? `<button class="tiny wide ghost danger" style="margin-top:8px"
                 onclick="removeLock()">잠긴 토큰 지우기</button>` : ""}
    </details>`;
}

function unlockCard(){
  return `
    <div class="card" style="border-color:var(--accent)">
      <h2>암호를 넣어주세요</h2>
      <div class="sub">이 기기에는 토큰이 없지만, 잠가 둔 것이 있습니다.</div>
      <input id="pass" type="password" autocomplete="current-password"
             placeholder="잠글 때 정한 암호" enterkeyhint="done"
             onkeydown="if(event.key==='Enter'){event.preventDefault();unlockToken()}">
      <button class="primary wide" style="margin-top:10px" onclick="unlockToken()">열기</button>
      <div class="note">
        암호가 기억나지 않으면 토큰을 직접 넣어도 됩니다.
        <a href="#" onclick="showTokenInput();return false" style="color:var(--accent)">토큰 직접 넣기 →</a>
      </div>
    </div>`;
}

async function load(){
  clearTimeout(timer); timer = null;
  toast("불러오는 중…", "busy");
  try{
    const j = await getFile(FILE);
    if (!j) throw new Error("settings.yaml 이 없습니다.");
    sha = j.sha;
    data = fromYaml(dec(j.content));
    loadedAt = new Date(); savedAt = null;

    const draft = localStorage.getItem(DRAFT);
    if (draft && draft !== toYaml(data)){
      data = fromYaml(draft);
      dirty = true;
      render(); showState();
      toast(`저장하지 못한 변경이 남아 있어 되살렸습니다.
             <a href="#" onclick="save();return false">지금 저장</a> ·
             <a href="#" onclick="discardDraft();return false">버리고 서버 값 쓰기</a>`, "err", true);
      return;
    }
    dirty = false;
    if (wide() && data.digests.length) open.add("d" + selected);
    render(); showState();

    // 키워드와 검색어를 합치기 전에 만든 주제는 검색어가 비어 있다.
    // 화면에 적힌 것과 실제로 도는 것이 달라지지 않게 여기서 맞춘다.
    const before = data.digests.map(d => JSON.stringify(d.queries || []));
    data.digests.forEach(syncQueries);
    if (data.digests.some((d, i) => JSON.stringify(d.queries || []) !== before[i])){
      render(); touch();
      toast("키워드로 검색어를 맞췄습니다. 저장하는 중…", "busy");
      return;
    }
    toast("최신 설정을 불러왔습니다.", "ok");
  }catch(e){ toast("불러오지 못했습니다: " + esc(e.message), "err"); showState(); }
}

async function save(){
  clearTimeout(timer); timer = null;
  if (!data || !dirty) return true;
  if (!token()){ toast("먼저 맨 위에 GitHub 토큰을 넣어주세요. 넣으면 바로 저장됩니다.", "err"); return false; }
  if (saving) return false;

  saving = true; showState();
  const text = toYaml(data);
  try{
    let r;
    try{
      r = await putFile(FILE, text, sha, "설정 변경 (모바일 어드민)");
    }catch(e){
      // 다른 곳에서 먼저 바뀌면 sha 가 어긋난다. 최신 sha 로 한 번 다시 민다.
      if (e.status !== 409 && e.status !== 422) throw e;
      const cur = await getFile(FILE);
      sha = cur && cur.sha;
      r = await putFile(FILE, text, sha, "설정 변경 (모바일 어드민)");
    }
    sha = r.content.sha;
    dirty = false; savedAt = new Date();
    localStorage.removeItem(DRAFT);
    clearAuthProblem();
    toast("저장했습니다. 다음 발송부터 반영됩니다.", "ok");
    return true;
  }catch(e){
    // 고친 값은 DRAFT 에 남아 있다. 새로고침해도 되살아난다.
    if (e.status === 401 || e.status === 403){
      await showAuthProblem(e.message);
      toast(`저장하지 못했습니다 — 맨 위 안내를 봐주세요.
             고친 값은 이 기기에 남아 있으니 없어지지 않습니다.`, "err", true);
    } else {
      toast(`저장하지 못했습니다 (${esc(e.message)})
             <div style="margin-top:6px"><a href="#" onclick="save();return false">다시 시도</a>
             — 고친 값은 이 기기에 남아 있으니 없어지지 않습니다.</div>`, "err", true);
    }
    return false;
  }finally{ saving = false; showState(); }
}

async function testSend(di, si){
  const dg = data.digests[di], s = dg.slots[si];
  if (!token()){ toast("먼저 맨 위에 GitHub 토큰을 넣어주세요.", "err"); return; }
  if (dirty && !(await save())) return;

  toast(`'${esc(s.title)}' 발송을 요청하는 중…`, "busy");
  try{
    const cur = await getFile(".trigger");
    // 첫 줄이 회차, 둘째 줄 force 는 이미 보낸 항목도 다시 보내라는 뜻이다.
    const line = `${dg.config || dg.key}:${s.slot}`;
    const body = `${line}\nforce\n${new Date().toISOString()}\n`;
    await putFile(".trigger", body, cur && cur.sha, `테스트 발송 ${line}`);
    toast(`보내는 중입니다. 1~2분 뒤 텔레그램을 확인하세요.
           <a href="${RUNS}" target="_blank" rel="noopener">실행 기록 보기 →</a>`, "ok", true);
  }catch(e){ toast("발송을 요청하지 못했습니다: " + esc(e.message), "err"); }
}

/* 저장이 끝나기 전에 화면을 닫으면 알려준다 */
addEventListener("beforeunload", e => { if (dirty){ e.preventDefault(); e.returnValue = ""; } });

/* ---------- 순위 사이로 칩 끌어 옮기기 ----------
   HTML5 draggable 은 터치에서 동작하지 않는다. 폰에서도 써야 하므로
   포인터 이벤트로 만든다 — 마우스·터치·펜이 같은 경로를 탄다. */
let drag = null;

function zoneUnder(x, y){
  const el = document.elementFromPoint(x, y);
  return el && el.closest ? el.closest(".tier") : null;
}
function highlight(zone){
  if (drag.zone === zone) return;
  if (drag.zone) drag.zone.classList.remove("over");
  drag.zone = zone;
  if (zone) zone.classList.add("over");
}

addEventListener("pointerdown", e => {
  const chip = e.target.closest && e.target.closest(".chip.kw");
  // 삭제 버튼을 누른 것이면 끌기가 아니다
  if (!chip || e.target.closest("button")) return;
  e.preventDefault();

  const box = chip.getBoundingClientRect();
  const ghost = chip.cloneNode(true);
  ghost.classList.add("ghost");
  ghost.style.width = box.width + "px";
  document.body.appendChild(ghost);

  drag = {
    chip, ghost, zone: null,
    di: Number(chip.dataset.di),
    word: chip.dataset.word,
    from: Number(chip.closest(".tier").dataset.tier),
    dx: e.clientX - box.left,
    dy: e.clientY - box.top,
  };
  chip.classList.add("dragging");
  place(e.clientX, e.clientY);
});

function place(x, y){
  drag.ghost.style.left = (x - drag.dx) + "px";
  drag.ghost.style.top = (y - drag.dy) + "px";
}

addEventListener("pointermove", e => {
  if (!drag) return;
  e.preventDefault();
  place(e.clientX, e.clientY);
  const zone = zoneUnder(e.clientX, e.clientY);
  // 다른 주제의 상자에는 떨어뜨리지 않는다
  highlight(zone && Number(zone.dataset.di) === drag.di ? zone : null);
}, {passive: false});

addEventListener("pointerup", () => {
  if (!drag) return;
  const {zone, di, word, from} = drag;
  drag.ghost.remove();
  drag.chip.classList.remove("dragging");
  if (zone) zone.classList.remove("over");
  drag = null;

  const to = zone ? Number(zone.dataset.tier) : null;
  if (!to || to === from) { render(); return; }
  const dg = data.digests[di];
  if (!dg || !dg.keywords[word]) { render(); return; }
  dg.keywords[word] = to;
  syncQueries(dg);
  render(); touch();
  const label = (TIERS.find(([w]) => w === to) || [])[1];
  toast(`'${esc(word)}' 을(를) ${label} 로 옮겼습니다.`, "busy");
});

addEventListener("pointercancel", () => {
  if (!drag) return;
  drag.ghost.remove();
  drag.chip.classList.remove("dragging");
  if (drag.zone) drag.zone.classList.remove("over");
  drag = null;
});

/* 데스크탑에서 손이 키보드에 있을 때 */
addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s"){
    e.preventDefault();
    save();
  }
});

function bookmarkHelp(){
  const mac = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent);
  const mod = mac ? "⌘" : "Ctrl";
  $("bookmarkHelp").innerHTML = `
    이 페이지 주소: <b>${location.origin + location.pathname}</b><br>
    <kbd>${mod}</kbd>+<kbd>D</kbd> 로 즐겨찾기에 넣거나, 주소창의 자물쇠를
    즐겨찾기 바로 끌어다 놓으면 됩니다. 즐겨찾기 바가 안 보이면
    <kbd>${mod}</kbd>+<kbd>Shift</kbd>+<kbd>B</kbd>.<br>
    저장은 <kbd>${mod}</kbd>+<kbd>S</kbd> 로도 됩니다.`;
}

/* 인라인 onclick 에서 부르는 함수들.
   모듈은 제 스코프를 가지므로 window 에 얹어야 속성 핸들러가 찾는다. */
Object.assign(window, {
  blogId,   // 주소 파싱은 테스트에서 직접 부른다
  addAllSuggestions,
  addExclude,
  addFeed,
  addKeyword,
  addSlot,
  addTopic,
  bump,
  checkToken,
  clearClientId,
  clearSubs,
  clearToken,
  closeWizard,
  delChannel,
  delExclude,
  delFeed,
  delKeyword,
  delSlot,
  delTopic,
  discardDraft,
  driveCheck,
  driveOpen,
  drivePick,
  driveResync,
  exportSourcesCSV,
  load,
  panel,
  pickChannel,
  pickSection,
  pickTier,
  pickTopic,
  reMatchAll,
  save,
  saveClientId,
  saveToken,
  scopedQuery,   // 화면에 미리 보이는 검색어. 테스트에서 직접 부른다
  searchSubs,
  lockToken,
  removeLock,
  setFilter,
  setMode,
  showTokenInput,
  startWizard,
  unlockToken,
  set,
  setScope,
  suggestFor,
  switchWizard,
  testSend,
  wizardApply,
  wizardFinish,
  wizardGoto,
  wizardRestart,
  wizardRetry,
  wizardSelectAll,
  wizardSelectNone,
  wizardSelectVerifiedOnly,
  wizardSearchMore,
  wizardStartRecommend,
  wizardTestSend,
  wizardTogglePick,
});

/* 데스크탑과 모바일은 이제 같은 CSS 가 아니라 서로 다른 HTML 을 그린다
   (가운데 단, 항목별 <section>). 폭을 가로지르며 창을 늘이거나 줄이면
   다시 그려야 한다 — 데이터가 안 바뀌면 render() 가 저절로 불리지 않는다. */
let wasWide = wide();
addEventListener("resize", () => {
  const nowWide = wide();
  if (nowWide !== wasWide){ wasWide = nowWide; if (data) render(); }
});

$("build").textContent = BUILD;
bookmarkHelp();
renderToken();
// 잠가 둔 토큰이 있으면 붙여넣기 대신 암호 칸을 띄운다. 페이지와 같은
// 자리에서 받아오므로 한 번 더 그리면 된다.
fetchLocked().then(box => { if (box) renderToken(); });
renderDrive();
showState();
load();
