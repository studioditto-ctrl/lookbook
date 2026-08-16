/* ---------- YAML (이 파일의 구조에 한정한 최소 구현) ---------- */
function quote(s){
  s = String(s);
  return /^[\w가-힣ㄱ-ㅎ][\w가-힣ㄱ-ㅎ .·&+-]*$/.test(s) ? s : JSON.stringify(s);
}
function toYaml(d){
  let out = "# 모바일 어드민 페이지가 저장한 파일입니다.\n";
  out += "# 채널 목록은 config*.yaml 에 있습니다.\n\ndigests:\n";
  for (const dg of d.digests){
    out += `  - config: ${dg.config}\n    label: ${quote(dg.label)}\n    slots:\n`;
    for (const s of dg.slots){
      out += `      - slot: ${s.slot}\n`;
      out += `        title: ${JSON.stringify(s.title)}\n`;
      out += `        send_at: ${JSON.stringify(s.send_at)}\n`;
      out += `        enabled: ${s.enabled ? "true" : "false"}\n`;
      out += `        articles: ${s.articles}\n        videos: ${s.videos}\n`;
    }
    out += "    keywords:\n";
    for (const [k, v] of Object.entries(dg.keywords)) out += `      ${quote(k)}: ${v}\n`;
  }
  out += "\nexclude:\n";
  for (const w of d.exclude) out += `  - ${quote(w)}\n`;
  return out;
}
/* 우리가 쓰는 모양만 읽는 파서 — 들여쓰기와 키가 위 형식과 같다고 가정한다 */
function fromYaml(text){
  const out = {digests: [], exclude: []};
  let dg = null, slot = null, mode = null;
  for (const raw of text.split("\n")){
    const line = raw.replace(/\t/g, "  ");
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const indent = line.length - line.trimStart().length;
    const t = line.trim();
    if (t === "digests:"){ mode = "digests"; continue; }
    if (t === "exclude:"){ mode = "exclude"; continue; }
    if (mode === "exclude" && t.startsWith("- ")){ out.exclude.push(unq(t.slice(2))); continue; }
    if (mode !== "digests") continue;

    if (indent === 2 && t.startsWith("- config:")){
      dg = {config: t.split(":").slice(1).join(":").trim(), label: "", slots: [], keywords: {}};
      out.digests.push(dg); slot = null; continue;
    }
    if (!dg) continue;
    if (indent === 4 && t.startsWith("label:")){ dg.label = unq(t.slice(6).trim()); continue; }
    if (indent === 4 && t === "slots:"){ dg._in = "slots"; continue; }
    if (indent === 4 && t === "keywords:"){ dg._in = "keywords"; continue; }
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
const unq = s => (s.startsWith('"') && s.endsWith('"')) ? JSON.parse(s) : s;


export { toYaml, fromYaml };
