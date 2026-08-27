/* 토큰을 암호로 잠그고 푼다.

   페이지는 서버가 없고 저장소는 공개다. 토큰을 그대로 두면 누구나 가져다
   쓰고, 깃허브가 유출을 감지해 폐기해 버린다. 그래서 암호로 잠근 덩어리만
   저장소에 두고, 푸는 암호는 사람 머릿속에만 둔다.

   잠긴 덩어리는 공개된 자리에 있으므로 공격자가 시간을 들여 암호를 때려
   맞출 수 있다. 그래서 PBKDF2 를 60만 번 돌려 한 번 시도하는 데 드는
   비용을 올린다. 그래도 짧은 암호는 뚫린다 — 그건 코드로 막을 수 없어
   화면에서 길이를 요구한다.

   브라우저에 들어 있는 것만 쓴다. 라이브러리를 붙이면 그 라이브러리를
   믿어야 하는데, 토큰을 다루는 자리에서는 믿을 것을 늘리지 않는다. */

const KDF_ITERATIONS = 600000;
const VERSION = 1;
export const MIN_PASSPHRASE = 12;

const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

async function keyFrom(passphrase, salt, iterations){
  const base = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    {name: "PBKDF2", salt, iterations, hash: "SHA-256"},
    base, {name: "AES-GCM", length: 256}, false, ["encrypt", "decrypt"]);
}

export async function lock(token, passphrase){
  if (!token) throw new Error("잠글 토큰이 없습니다.");
  if ((passphrase || "").length < MIN_PASSPHRASE){
    throw new Error(`암호는 ${MIN_PASSPHRASE}자 이상이어야 합니다.`);
  }
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await keyFrom(passphrase, salt, KDF_ITERATIONS);
  const data = await crypto.subtle.encrypt(
    {name: "AES-GCM", iv}, key, new TextEncoder().encode(token));
  return JSON.stringify({
    note: "GitHub 토큰을 암호로 잠근 것입니다. 암호 없이는 풀 수 없습니다.",
    v: VERSION, kdf: "PBKDF2-SHA256", iterations: KDF_ITERATIONS,
    salt: b64(salt), iv: b64(iv), data: b64(data),
  }, null, 2) + "\n";
}

export async function unlock(text, passphrase){
  let box;
  try{ box = JSON.parse(text); }
  catch{ throw new Error("잠긴 파일을 읽을 수 없습니다."); }
  if (box.v !== VERSION) throw new Error(`모르는 형식입니다 (v${box.v}).`);

  const key = await keyFrom(passphrase, unb64(box.salt), box.iterations || KDF_ITERATIONS);
  let plain;
  try{
    plain = await crypto.subtle.decrypt(
      {name: "AES-GCM", iv: unb64(box.iv)}, key, unb64(box.data));
  }catch{
    // AES-GCM 은 태그가 안 맞으면 실패한다. 암호가 틀린 것과
    // 파일이 상한 것을 구분할 방법이 없으므로 흔한 쪽으로 말한다.
    throw new Error("암호가 맞지 않습니다.");
  }
  const token = new TextDecoder().decode(plain);
  if (!token) throw new Error("푼 값이 비어 있습니다.");
  return token;
}
