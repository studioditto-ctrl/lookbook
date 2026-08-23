import { readFileSync, writeFileSync } from "node:fs";
// 어드민 페이지가 실제로 쓰는 파일을 그대로 불러온다. 복사본을 두지 않는다.
import { toYaml, fromYaml } from "../../docs/yaml.js";

const original = readFileSync("settings.yaml", "utf8");
const parsed = fromYaml(original);
const rewritten = toYaml(parsed);
writeFileSync("/tmp/claude-0/-home-user-lookbook/972dc8cb-c6dd-53d9-b818-7f6067fe31f7/scratchpad/settings.rewritten.yaml", rewritten);

// 편집을 흉내 낸 두 번째 왕복 — 파서가 자기 출력도 읽을 수 있어야 한다
const reparsed = fromYaml(rewritten);
writeFileSync("/tmp/claude-0/-home-user-lookbook/972dc8cb-c6dd-53d9-b818-7f6067fe31f7/scratchpad/settings.twice.yaml", toYaml(reparsed));
console.log("다이제스트", parsed.digests.length, "| 슬롯",
  parsed.digests.map(d => d.slots.length).join(","), "| 제외어", parsed.exclude.length);
