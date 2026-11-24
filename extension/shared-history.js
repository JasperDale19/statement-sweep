// What other copies of Statement Sweep saved into the folder you chose, so a
// copy on another computer that chooses the same folder (in iCloud Drive or
// Google Drive, say) does not save those documents again.
//
// Each site's sweep that saves documents into the folder writes one new
// file, an entry, in the folder's `Statement Sweep History` folder, in the
// export format (history.js): the ids of the documents it saved there, their
// dates and tax marks, and for each where it was saved in the folder, its
// size and its SHA-256 (`files`). Never the folder and file name settings or
// the switched-off sites. An entry is never written over or removed, so two
// computers syncing the folder at once can never undo each other's record.
//
// An entry is only a claim. A document another copy saved is skipped only
// once its file is found in the folder this sweep saves to, at the path the
// entry gives, with the same size and SHA-256. So a document whose file has
// not synced down yet, failed to upload, was removed, or is in a different
// folder from the one chosen now, is saved again here. What other copies
// saved is never added to this copy's own record (`seen:`): a file that
// checked out is remembered only with the folder it was found in (VERIFIED),
// so it is not read again each run.
//
// Whatever goes wrong, the worst that follows is a document saved twice,
// never one left unsaved. `historySharingOff: true` in chrome.storage.local
// turns it all off.
import { FORMAT, MAX_FILE_BYTES, MAX_IDS, VERSION, readHistory } from "./history.js";
import { getFolder, setFolder, writeFile } from "./folders.js";

export const SHARED_DIR = "Statement Sweep History";
export const SHARING_OFF = "historySharingOff";
const VERIFIED = "verified";
const MAX_VERIFIED = 200000;
const local = () => chrome.storage.local;
const isObject = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);

export const sharingOn = async () => (await local().get(SHARING_OFF))[SHARING_OFF] !== true;

async function fileAt(root, path) {
  const parts = path.split("/");
  const name = parts.pop();
  let dir = root;
  for (const part of parts) dir = await dir.getDirectoryHandle(part);
  return (await dir.getFileHandle(name)).getFile();
}

const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (file) => hex(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));

// What other copies check a saved document by: where it was saved in the
// folder, and the size and SHA-256 of the bytes this copy saved there, taken
// from those bytes rather than read back, so a file another save put at the
// same path at the same moment cannot be taken for it.
export async function factsOf(path, blob) {
  return { path, size: blob.size, sha256: await sha256(blob) };
}

async function readFile(handle) {
  try {
    const got = await handle.getFile();
    return got.size > MAX_FILE_BYTES ? { ok: false, problem: "It is too large to be a Statement Sweep history file." } : readHistory(await got.text());
  } catch (e) {
    return { ok: false, problem: `It could not be read: ${e.name || "Error"}.` };
  }
}

// Every entry in `root` (your chosen folder): what it claims was saved
// there. Answers { off: true } when sharing is off, or { index: {site id:
// {doc id: [facts]}}, read: [names], listed, unknown: [site ids], problems:
// [{name, problem}] }. Changes nothing.
export async function readShared(root) {
  if (!(await sharingOn())) return { off: true };
  const result = { index: {}, read: [], listed: 0, unknown: [], problems: [] };
  const dir = await root.getDirectoryHandle(SHARED_DIR).catch((e) => { if (e.name === "NotFoundError") return null; throw e; });
  if (!dir) return result;
  const handles = [];
  for await (const [name, handle] of dir.entries()) {
    if (handle.kind === "file" && name.endsWith(".json") && !name.startsWith(".")) handles.push([name, handle]);
  }
  handles.sort(([a], [b]) => a.localeCompare(b));
  for (const [name, handle] of handles) {
    const history = await readFile(handle);
    if (!history.ok) {
      result.problems.push({ name, problem: history.problem });
      continue;
    }
    result.read.push(name);
    for (const id of history.unknown) if (!result.unknown.includes(id)) result.unknown.push(id);
    for (const [site, byDoc] of Object.entries(history.files)) {
      const docs = (result.index[site] = result.index[site] || {});
      for (const [doc, facts] of Object.entries(byDoc)) {
        if (!docs[doc]) { docs[doc] = []; result.listed += 1; }
        docs[doc].push(facts);
      }
    }
  }
  return result;
}

// Those of `ids`, documents of site `siteId` this copy has not saved, whose
// file is in `root` as an entry in `index` (readShared) says. Answers a Set.
export async function inFolderAlready(root, siteId, ids, index) {
  const listed = (index && index[siteId]) || {};
  const asked = ids.filter((id) => listed[id]);
  if (!asked.length) return new Set();
  const stored = await getFolder(VERIFIED);
  const same = isObject(stored) && stored.handle ? await stored.handle.isSameEntry(root).catch(() => false) : false;
  const known = new Set(same && Array.isArray(stored.keys) ? stored.keys : []);
  const key = (id) => `${siteId}\n${id}`;
  const there = new Set();
  let added = false;
  for (const id of asked) {
    if (known.has(key(id))) { there.add(id); continue; }
    for (const facts of listed[id]) {
      const file = await fileAt(root, facts.path).catch(() => null);
      if (!file || file.size !== facts.size || (await sha256(file)) !== facts.sha256) continue;
      there.add(id);
      known.add(key(id));
      added = true;
      break;
    }
  }
  if (added || !same) await setFolder(VERIFIED, { handle: root, keys: [...known].slice(-MAX_VERIFIED) });
  return there;
}

const pad = (n) => String(n).padStart(2, "0");
const stamp = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;

// A new entry in `root` for `saved`, [{id, facts}] of site `siteId`, just
// saved into it. Answers the entry's name, or null when sharing is off or
// there is nothing to write. Throws when the entry cannot be written.
export async function writeEntry(root, siteId, saved, now = new Date()) {
  if (!(await sharingOn())) return null;
  const byDoc = new Map();
  for (const s of saved) if (s && typeof s.id === "string" && s.id && s.facts) byDoc.set(s.id, s.facts);
  const docs = [...byDoc.keys()];
  if (!docs.length) return null;
  if (docs.length > MAX_IDS) throw new Error(`${docs.length} documents are more than one history file can hold.`);
  const kept = await local().get([`dates:${siteId}`, `tax:${siteId}`]);
  const dates = isObject(kept[`dates:${siteId}`]) ? kept[`dates:${siteId}`] : {};
  const tax = Array.isArray(kept[`tax:${siteId}`]) ? kept[`tax:${siteId}`] : [];
  const file = { format: FORMAT, version: VERSION, exportedAt: now.toISOString(), sites: { [siteId]: docs } };
  const dated = Object.fromEntries(docs.filter((doc) => typeof dates[doc] === "string").map((doc) => [doc, dates[doc]]));
  if (Object.keys(dated).length) file.dates = { [siteId]: dated };
  const taxed = docs.filter((doc) => tax.includes(doc));
  if (taxed.length) file.tax = { [siteId]: taxed };
  file.files = { [siteId]: Object.fromEntries(byDoc) };
  const dir = await root.getDirectoryHandle(SHARED_DIR, { create: true });
  // A name of its own: the time, and a random part, so no entry is written over.
  const name = `${stamp(now)} ${crypto.randomUUID()}.json`;
  return writeFile(dir, name, new Blob([JSON.stringify(file)], { type: "application/json" }));
}
