// Publish authenticated metadata so archive signatures cannot be replayed as another version.
import { createHash, createPublicKey, verify } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';

const [directory, tag, signer] = process.argv.slice(2);
if (!directory || !/^v\d+\.\d+\.\d+$/.test(tag ?? '') || !signer) {
  throw new Error('Usage: node scripts/sign-cli-updates.mjs <archives> <vX.Y.Z> <sign_update>');
}
const privateKey = process.env.ARABICA_UPDATE_PRIVATE_KEY;
if (!privateKey) throw new Error('ARABICA_UPDATE_PRIVATE_KEY is required');
const rawPublicKey = Buffer.from(readFileSync(new URL('../crates/arabica-cli/src/update-public-key.txt', import.meta.url), 'utf8').trim(), 'base64');
if (rawPublicKey.length !== 32) throw new Error('Invalid update public key');
const publicKey = createPublicKey({
  key: Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), rawPublicKey]),
  format: 'der', type: 'spki',
});
const expected = new Set([
  'structure-linux-aarch64.tar.gz', 'structure-linux-x86_64.tar.gz',
  'structure-macos-aarch64.tar.gz', 'structure-macos-x86_64.tar.gz',
  'structure-windows-x86_64.zip',
]);
for (const name of readdirSync(directory)) {
  if (!expected.has(name)) continue;
  const path = join(directory, name);
  const size = statSync(path).size;
  if (size === 0 || size > 128 * 1024 * 1024) throw new Error(`Invalid archive size: ${name}`);
  const manifest = Buffer.from(JSON.stringify({
    version: tag.slice(1), archive: basename(path), size,
    sha256: createHash('sha256').update(readFileSync(path)).digest('hex'),
  }) + '\n');
  const manifestPath = `${path}.update.json`;
  writeFileSync(manifestPath, manifest);
  const signature = execFileSync(resolve(signer), ['--ed-key-file', '-', '-p', resolve(manifestPath)], {
    input: privateKey + '\n', encoding: 'utf8', stdio: ['pipe', 'pipe', 'inherit'],
  }).trim();
  if (!verify(null, manifest, publicKey, Buffer.from(signature, 'base64'))) {
    throw new Error('Signing key does not match the public key embedded in the updater');
  }
  writeFileSync(`${path}.sig`, signature + '\n');
  expected.delete(name);
  console.log(`Signed update metadata for ${name}`);
}
if (expected.size !== 0) throw new Error(`Missing archives: ${[...expected].join(', ')}`);
