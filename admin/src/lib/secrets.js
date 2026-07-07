/**
 * Client-side generators for the "random value" inputs: signing keys
 * (handshake secrets) and admin passwords. Values come from the browser CSPRNG
 * (crypto.getRandomValues) — never Math.random.
 */

/** Hex signing key (default 32 bytes → 64 hex chars), like `openssl rand -hex 32`. */
export const generateSecret = (bytes = 32) => {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
};

// No look-alike characters (0/O, 1/l/I) — these passwords get read out and
// retyped by humans; every class present so common policies pass.
const LOWER = 'abcdefghijkmnopqrstuvwxyz';
const UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
const DIGIT = '23456789';
const SYMBOL = '!@#$%^&*-_=+';
const ALL = LOWER + UPPER + DIGIT + SYMBOL;

const pick = (alphabet) => {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return alphabet[buf[0] % alphabet.length];
};

/** Readable random password (default 16 chars) with all character classes. */
export const generatePassword = (length = 16) => {
  const chars = [pick(LOWER), pick(UPPER), pick(DIGIT), pick(SYMBOL)];
  while (chars.length < length) chars.push(pick(ALL));
  // Fisher–Yates so the guaranteed-class characters aren't always up front.
  for (let i = chars.length - 1; i > 0; i -= 1) {
    const buf = new Uint32Array(1);
    crypto.getRandomValues(buf);
    const j = buf[0] % (i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join('');
};
