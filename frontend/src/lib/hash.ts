/** 内容摘要 (缓存键用, 非安全用途)。
 *
 * crypto.subtle 只在安全上下文 (HTTPS/localhost) 存在 —— 经局域网 IP
 * (http://192.168.x.x) 访问时浏览器视为非安全上下文, crypto.subtle 为
 * undefined 直接崩 (Cannot read properties of undefined reading 'digest')。
 * 这里在不可用时退化为 FNV-1a (加 "fnv-" 前缀避免与 SHA 值混用)。
 */

function toUint8(data: ArrayBuffer | Uint8Array): Uint8Array {
  if (data instanceof Uint8Array) return data
  return new Uint8Array(data)
}

function fnv1aHex(bytes: Uint8Array): string {
  let h = 0x811c9dc5
  for (const b of bytes) {
    h ^= b
    h = Math.imul(h, 0x01000193)
  }
  return "fnv-" + (h >>> 0).toString(16).padStart(8, "0")
}

export async function sha256Bytes(data: ArrayBuffer | Uint8Array): Promise<string> {
  const subtle = (globalThis.crypto as Crypto | undefined)?.subtle
  if (subtle) {
    const bytes = toUint8(data)
    const buf = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer
    const digest = await subtle.digest("SHA-256", buf)
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("")
  }
  return fnv1aHex(toUint8(data))
}

export async function sha256Text(content: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(content))
}
