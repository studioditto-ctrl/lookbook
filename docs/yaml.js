/* settings.yaml 을 읽고 쓰는 최소 구현. 브라우저와 테스트가 같은 파일을 쓴다. */
/* ---------- YAML (이 파일의 구조에 한정한 최소 구현) ---------- */
function quote(s){
  s = String(s);
  return /^[\w가-힣ㄱ-ㅎ][\w가-힣ㄱ-ㅎ .·&+-]*$/.test(s) ? s : JSON.stringify(s);
}
function toYaml(d){
  let out = "# 모바일 어드민 페이지가 저장한 파일입니다.\n";
  out += "# 채널 목록은 config*.yaml 에 있습니다.\n\ndigests:\n";
  for (const dg of d.digests){
    out += "  - " + (dg.config ? `config: ${dg.config}\n` : `key: ${quote(dg.key)}\n`);
    if (dg.config && dg.key) out += `    key: ${quote(dg.key)}\n`;
    out += `    label: ${quote(dg.label)}\n`;
    // 주제어. 검색어 앞에 AND 로 붙고, 걸러낼 때도 이 말로 판단한다.
    if ((dg.scope || []).length) out += `    scope: [${dg.scope.join(", ")}]\n`;
    if (dg.lookback_hours) out += `    lookback_hours: ${dg.lookback_hours}\n`;
    // AI 추천으로 채널을 한꺼번에 붙인 주제만 켠다 — 채널 항목도 주제와
    // 맞는지 다시 확인한다 (사람이 하나씩 고른 채널은 그대로 믿는다).
    if (dg.strict) out += `    strict: true\n`;
    out += dg.slots.length ? "    slots:\n" : "    slots: []\n";
    for (const s of dg.slots){
      out += `      - slot: ${s.slot}\n`;
      out += `        title: ${JSON.stringify(s.title)}\n`;
      out += `        send_at: ${JSON.stringify(s.send_at)}\n`;
      out += `        enabled: ${s.enabled ? "true" : "false"}\n`;
      out += `        articles: ${s.articles}\n        videos: ${s.videos}\n`;
    }
    out += "    keywords:\n";
    for (const [k, v] of Object.entries(dg.keywords)) out += `      ${quote(k)}: ${v}\n`;
    if ((dg.queries || []).length){
      out += "    queries:\n";
      for (const q of dg.queries){
        out += `      - name: ${quote(q.name)}\n        query: ${JSON.stringify(q.query)}\n`;
        if (q.tags && q.tags.length) out += `        tags: [${q.tags.join(", ")}]\n`;
      }
    }
    if ((dg.feeds || []).length){
      out += "    feeds:\n";
      for (const f of dg.feeds){
        out += `      - name: ${quote(f.name)}\n        url: ${JSON.stringify(f.url)}\n`;
        if (f.tags && f.tags.length) out += `        tags: [${f.tags.join(", ")}]\n`;
        if (f.region) out += `        region: ${f.region}\n`;
        if (f.reason) out += `        reason: ${JSON.stringify(f.reason)}\n`;
      }
    }
    if ((dg.channels || []).length){
      out += "    channels:\n";
      for (const c of dg.channels){
        out += `      - name: ${quote(c.name)}\n        channel_id: ${c.channel_id}\n`;
        if (c.tags && c.tags.length) out += `        tags: [${c.tags.join(", ")}]\n`;
        if (c.region) out += `        region: ${c.region}\n`;
        if (c.reason) out += `        reason: ${JSON.stringify(c.reason)}\n`;
      }
    }
  }
  // 공통 설정에서 바꾼 필터 기본값 — 없으면(한 번도 안 바꿨으면) 아예 안 쓴다.
  // 그래야 예전 파일과 똑같이 하드코딩된 기본값 그대로 동작한다.
  const f = d.filters || {};
  if (f.lookback_hours || f.min_subscribers != null || f.min_views != null){
    out += "\nfilters:\n";
    if (f.lookback_hours) out += `  lookback_hours: ${f.lookback_hours}\n`;
    if (f.min_subscribers != null) out += `  min_subscribers: ${f.min_subscribers}\n`;
    if (f.min_views != null) out += `  min_views: ${f.min_views}\n`;
  }
  out += "\nexclude:\n";
  for (const w of d.exclude) out += `  - ${quote(w)}\n`;
  return out;
}
/* 우리가 쓰는 모양만 읽는 파서 — 들여쓰기와 키가 위 형식과 같다고 가정한다 */
function fromYaml(text){
  const out = {digests: [], exclude: [], filters: {}};
  let dg = null, slot = null, mode = null;
  for (const raw of text.split("\n")){
    const line = raw.replace(/\t/g, "  ");
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const indent = line.length - line.trimStart().length;
    const t = line.trim();
    if (t === "digests:"){ mode = "digests"; continue; }
    if (t === "exclude:"){ mode = "exclude"; continue; }
    if (t === "filters:"){ mode = "filters"; continue; }
    if (mode === "exclude" && t.startsWith("- ")){ out.exclude.push(unq(t.slice(2))); continue; }
    if (mode === "filters" && indent === 2){
      const i = t.indexOf(":");
      const k = t.slice(0, i).trim(), v = t.slice(i + 1).trim();
      if (k === "lookback_hours" || k === "min_subscribers" || k === "min_views") out.filters[k] = +v;
      continue;
    }
    if (mode !== "digests") continue;

    if (indent === 2 && (t.startsWith("- config:") || t.startsWith("- key:"))){
      dg = {config: "", key: "", label: "", scope: [], slots: [], keywords: {},
            queries: [], channels: [], feeds: []};
      const [k, ...rest] = t.slice(2).split(":");
      dg[k.trim()] = unq(rest.join(":").trim());
      out.digests.push(dg); slot = null; continue;
    }
    if (!dg) continue;
    if (indent === 4 && t.startsWith("key:")){ dg.key = unq(t.slice(4).trim()); continue; }
    if (indent === 4 && t.startsWith("label:")){ dg.label = unq(t.slice(6).trim()); continue; }
    if (indent === 4 && t.startsWith("scope:")){
      dg.scope = flowList(t.slice(6)); continue;
    }
    if (indent === 4 && t.startsWith("lookback_hours:")){
      dg.lookback_hours = +t.slice(15).trim() || 0; continue;
    }
    if (indent === 4 && t.startsWith("strict:")){
      dg.strict = t.slice(7).trim() === "true"; continue;
    }
    if (indent === 4 && t.startsWith("slots:")){ dg._in = "slots"; continue; }
    if (indent === 4 && t === "keywords:"){ dg._in = "keywords"; continue; }
    if (indent === 4 && t === "queries:"){ dg._in = "queries"; continue; }
    if (indent === 4 && t === "channels:"){ dg._in = "channels"; continue; }
    if (indent === 4 && t === "feeds:"){ dg._in = "feeds"; continue; }
    if (dg._in === "queries" || dg._in === "channels" || dg._in === "feeds"){
      const list = dg._in === "queries" ? dg.queries
                 : dg._in === "channels" ? dg.channels : dg.feeds;
      if (t.startsWith("- name:")){ list.push({name: unq(t.slice(7).trim())}); continue; }
      const cur = list[list.length - 1];
      if (!cur) continue;
      const [k, ...rest] = t.split(":");
      const v = rest.join(":").trim();
      if (k === "tags") cur.tags = v.replace(/[\[\]]/g, "").split(",").map(x => x.trim()).filter(Boolean);
      else cur[k.trim()] = unq(v);
      continue;
    }
    if (dg._in === "slots" && t.startsWith("- slot:")){
      slot = {slot: t.slice(7).trim(), title: "", send_at: "08:00", enabled: true, articles: 1, videos: 1};
      dg.slots.push(slot); continue;
    }
    if (dg._in === "slots" && slot){
      const [k, ...rest] = t.split(":");
      const v = rest.join(":").trim();
      if (k === "title") slot.title = unq(v);
      else if (k === "send_at") slot.send_at = unq(v);
      else if (k === "enabled") slot.enabled = v === "true";
      else if (k === "articles") slot.articles = +v;
      else if (k === "videos") slot.videos = +v;
      continue;
    }
    if (dg._in === "keywords" && indent >= 6){
      const i = t.lastIndexOf(":");
      dg.keywords[unq(t.slice(0, i).trim())] = +t.slice(i + 1).trim();
    }
  }
  out.digests.forEach(d => delete d._in);
  return out;
}
/* [가, 나, 다] 한 줄짜리 목록 */
const flowList = s => s.replace(/[\[\]]/g, "").split(",")
                       .map(x => unq(x.trim())).filter(Boolean);

const unq = s => (s.startsWith('"') && s.endsWith('"')) ? JSON.parse(s) : s;

export { toYaml, fromYaml, quote, unq, flowList };
