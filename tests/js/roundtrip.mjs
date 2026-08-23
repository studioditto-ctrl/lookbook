import { readFileSync, writeFileSync } from "node:fs";
// 어드민 페이지가 실제로 쓰는 파일을 그대로 불러온다. 복사본을 두지 않는다.
import { toYaml, fromYaml } from "../../docs/yaml.js";

const original = readFileSync("settings.yaml", "utf8");
const parsed = fromYaml(original);
const rewritten = toYaml(parsed);
writeFileSync("/tmp/claude-0/-home-user-lookbook/972dc8cb-c6dd-53d9-b818-7f6067fe31f7/scratchpad/settings.rewritten.yaml", rewritten);

// 편집을 흉내 낸 두 번째 왕복 — 파서가 자기 출력도 읽을 수 있어야 한다
const reparsedData = fromYaml(rewritten);
const reparsed = reparsedData.digests;
writeFileSync("/tmp/claude-0/-home-user-lookbook/972dc8cb-c6dd-53d9-b818-7f6067fe31f7/scratchpad/settings.twice.yaml", toYaml(reparsedData));
console.log("다이제스트", parsed.digests.length, "| 슬롯",
  parsed.digests.map(d => d.slots.length).join(","), "| 제외어", parsed.exclude.length);

// 주제어와 기간은 페이지가 저장할 때 사라지기 쉬운 값이다.
// 파서가 모르는 줄은 조용히 버려지므로 왕복으로 확인한다.
for (const dg of parsed.digests){
  const back = reparsed.find(d => d.label === dg.label);
  const same = JSON.stringify(back.scope) === JSON.stringify(dg.scope)
            && back.lookback_hours === dg.lookback_hours;
  if (!same) throw new Error(`${dg.label} 주제어/기간이 왕복에서 바뀌었습니다`);
}
console.log("주제어 왕복 확인",
  parsed.digests.map(d => `${d.label}:${(d.scope || []).length}`).join(" "));
