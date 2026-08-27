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
const BUILD = "2026-08-23d";   // 화면에 찍어 어느 판인지 확인한다

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

function stepper(di, si, key, val){
  return `<div class="step">
    <button onclick="bump(${di},${si},'${key}',-1)" aria-label="줄이기">−</button>
    <span id="v${di}-${si}-${key}">${val}</span>
    <button onclick="bump(${di},${si},'${key}',1)" aria-label="늘리기">+</button>
  </div>`;
}

function render(){
  $("digests").innerHTML = data.digests.map((dg, di) => `
    <div class="card">
      <h2>${esc(dg.label)}</h2>
      ${dg.slots.map((s, si) => `
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
            <div><label>기사</label>${stepper(di, si, "articles", s.articles)}</div>
            <div><label>영상</label>${stepper(di, si, "videos", s.videos)}</div>
          </div>
          <div class="duo">
            <button class="tiny ghost" onclick="testSend(${di},${si})">지금 테스트 발송</button>
            <button class="tiny danger" onclick="delSlot(${di},${si})">이 시간 삭제</button>
          </div>
        </div>`).join("")}
      <button class="tiny wide dashed" onclick="addSlot(${di})">＋ 보낼 시간 추가</button>

      <details data-panel="d${di}" ${open.has("d" + di) ? "open" : ""}
               ontoggle="panel('d${di}',this.open)">
        <summary>키워드 ${Object.keys(dg.keywords).length} · 채널 ${(dg.channels || []).length} · 블로그 ${(dg.feeds || []).length}</summary>

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
        <label style="margin-top:14px">주제어 <span class="sub">(안 넣어도 됩니다)</span></label>
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
        </div>
        <div class="add">
          <button class="tiny ghost" style="flex:1" onclick="suggestFor(${di})">추천 키워드 보기</button>
        </div>
        <div class="chips" id="sg${di}">${suggestionChips(di, dg)}</div>

        <label style="margin-top:16px">블로그 · RSS</label>
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

        <label style="margin-top:16px">유튜브 채널</label>
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

        <button class="tiny wide danger" style="margin-top:16px"
                onclick="delTopic(${di})">'${esc(dg.label)}' 주제 삭제</button>
      </details>
    </div>`).join("");

  $("excludeChips").innerHTML = data.exclude.map(w => `
    <span class="chip">${esc(w)}
      <button onclick="delExclude(${arg(w)})" aria-label="삭제">×</button>
    </span>`).join("");
}

function panel(id, isOpen){ isOpen ? open.add(id) : open.delete(id); }

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
  render(); touch();
  toast(`'${esc(dg.label)}' 주제를 지웠습니다.`
    + (dg.config ? ` (${esc(dg.config)} 파일은 그대로 둡니다)` : ""), "busy");
}
function set(di, si, key, val){ data.digests[di].slots[si][key] = val; touch(); }
function bump(di, si, key, delta){
  const slot = data.digests[di].slots[si];
  slot[key] = Math.max(0, Math.min(10, (slot[key] || 0) + delta));
  $(`v${di}-${si}-${key}`).textContent = slot[key];
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
  return TIERS.map(([weight, label]) => {
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
  }).join("");
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

function addTopic(){
  const label = $("newTopic").value.trim();
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
  $("newTopic").value = "";
  const di = data.digests.length - 1;
  syncQueries(data.digests[di]);
  open.add("d" + di);   // 붙은 채널과 추천을 바로 볼 수 있게
  render(); touch();
  suggestFor(di, true);
  const n = data.digests[data.digests.length - 1].channels.length;
  toast(n
    ? `'${esc(label)}' 주제를 만들고 구독 채널 ${n}개를 붙였습니다. 아래에서 확인하세요.`
    : `'${esc(label)}' 주제를 만들었습니다. 이름이 맞는 구독 채널은 없지만 `
      + `유튜브 검색으로 영상이 들어옵니다.`,
    "busy");
}

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

function renderToken(){
  const has = !!token();
  if (has && authProblem){ $("tokenCard").innerHTML = authProblemCard(); return; }
  // 토큰은 없는데 잠가 둔 것이 있으면, 붙여넣기보다 암호 한 줄이 빠르다
  if (!has && lockedBox && !forceTokenInput){
    $("tokenCard").innerHTML = unlockCard();
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
  load,
  panel,
  pickChannel,
  pickTier,
  reMatchAll,
  save,
  saveClientId,
  saveToken,
  scopedQuery,   // 화면에 미리 보이는 검색어. 테스트에서 직접 부른다
  searchSubs,
  lockToken,
  removeLock,
  showTokenInput,
  unlockToken,
  set,
  setScope,
  suggestFor,
  testSend,
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
