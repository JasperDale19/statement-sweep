// The folder you chose as the one record of what has been saved, so copies of
// Statement Sweep on other computers that choose the same folder (in iCloud
// Drive or Google Drive, say) know what is saved, each site's schedule and
// what is due, and never save a document again.
//
// What is in the folder. Each site's sweep that saves documents into it
// writes one new file, an entry, in its `Statement Sweep History` folder, in
// the export format (history.js): the ids of the documents saved, their dates
// and tax marks, and for each where it was saved, its size and its SHA-256
// (`files`). A run also writes an entry of the sites it looked at with
// nothing left to save (`checked`). Never the folder and file name settings
// or the switched-off sites. An entry is never written over or removed, so
// two computers syncing the folder at once can never undo each other's.
//
// How it is read (`reconcile`). An entry is only a claim: a document counts
// as in the folder once its file is found at the path the entry gives, with
// the same size and SHA-256. A file is read and hashed once; after that its
// size and time of last change are enough, until either changes. The answer
// is the folder's record: for each site the documents in the folder, their
// dates and tax marks, where each is, and the day the site was last checked.
// Before a document is skipped its file is looked at once more (`stillThere`).
//
// A folder that cannot be read is never taken for an empty one: reading
// stops with FolderUnavailable, and nothing is saved until it can be read.
// Only a file that is plainly not there counts as not there.
import { FORMAT, MAX_FILE_BYTES, MAX_IDS, VERSION, readHistory } from "./history.js";
import { writeFile } from "./folders.js";

export const SHARED_DIR = "Statement Sweep History";
export const SHARING_OFF = "historySharingOff";
const STALL_MS = 60 * 1000;
const HASH_AT_ONCE = 4;
const local = () => chrome.storage.local;
const isObject = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);

export const sharingOn = async () => (await local().get(SHARING_OFF))[SHARING_OFF] !== true;

// The folder did not answer, or would not be read. Its message is for you.
export class FolderUnavailable extends Error {}
// Stop was pressed while the folder was being read.
export class Stopped extends Error {}

const notThere = (e) => Boolean(e) && (e.name === "NotFoundError" || e.name === "TypeMismatchError");
const unavailable = (what, e) => new FolderUnavailable(`Your folder could not be read (${what}${e && e.name ? `: ${e.name}` : ""}). It may still be downloading. Nothing was saved; run it again in a moment.`);

// `work` within `ms`, or FolderUnavailable; any failure but "not there" too.
function reading(what, ms, work) {
  let timer;
  const limit = new Promise((resolve, reject) => { timer = setTimeout(() => reject(unavailable(`${what} did not answer`)), ms); });
  const done = Promise.resolve().then(work).catch((e) => { throw e instanceof FolderUnavailable || notThere(e) ? e : unavailable(what, e); });
  return Promise.race([done, limit]).finally(() => clearTimeout(timer));
}

// Files of `root` by path, each folder on the way opened once. Answers the
// File, or null when it, or a folder on its way, is not there.
function filesOf(root, ms) {
  const dirs = new Map([["", Promise.resolve(root)]]);
  const dirOf = (parts) => {
    const key = parts.join("/");
    if (!dirs.has(key)) {
      dirs.set(key, dirOf(parts.slice(0, -1)).then((dir) => dir && reading(key, ms, () => dir.getDirectoryHandle(parts[parts.length - 1])).catch((e) => { if (notThere(e)) return null; throw e; })));
    }
    return dirs.get(key);
  };
  return async (path) => {
    const parts = path.split("/");
    const name = parts.pop();
    const dir = await dirOf(parts);
    if (!dir) return null;
    return reading(path, ms, async () => (await dir.getFileHandle(name)).getFile()).catch((e) => { if (notThere(e)) return null; throw e; });
  };
}

const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (blob) => hex(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));

// What other copies check a saved document by: where it was saved in the
// folder, and the size and SHA-256 of the bytes this copy saved there, taken
// from those bytes rather than read back, so a file another save put at the
// same path at the same moment cannot be taken for it.
export async function factsOf(path, blob) {
  return { path, size: blob.size, sha256: await sha256(blob) };
}

// Every entry in `root`: what they claim. { claims: {site: {doc: {candidates:
// [facts], date, tax}}}, checked: {site: day}, read: [names], unknown: [site
// ids], problems: [{name, problem}] }. An entry whose words are wrong is a
// problem and left out; one that cannot be read, or is from a newer
// Statement Sweep, stops the reading.
async function readClaims(root, { stopped, ms }) {
  const result = { claims: {}, checked: {}, read: [], unknown: [], problems: [] };
  const dir = await reading(SHARED_DIR, ms, () => root.getDirectoryHandle(SHARED_DIR)).catch((e) => { if (notThere(e)) return null; throw e; });
  if (!dir) return result;
  const handles = await reading(SHARED_DIR, ms, async () => {
    const found = [];
    for await (const [name, handle] of dir.entries()) {
      if (handle.kind === "file" && name.endsWith(".json") && !name.startsWith(".")) found.push([name, handle]);
    }
    return found;
  });
  handles.sort(([a], [b]) => a.localeCompare(b));
  for (const [name, handle] of handles) {
    if (stopped()) throw new Stopped();
    const file = await reading(name, ms, () => handle.getFile()).catch((e) => { throw notThere(e) ? unavailable(name, e) : e; });
    if (file.size > MAX_FILE_BYTES) {
      result.problems.push({ name, problem: "It is too large to be a Statement Sweep history file." });
      continue;
    }
    const history = readHistory(await reading(name, ms, () => file.text()));
    if (!history.ok && history.newer) {
      throw new FolderUnavailable("Your folder holds history from a newer Statement Sweep. Update Statement Sweep on this computer, then run it again. Nothing was saved.");
    }
    if (!history.ok) {
      result.problems.push({ name, problem: history.problem });
      continue;
    }
    result.read.push(name);
    for (const id of history.unknown) if (!result.unknown.includes(id)) result.unknown.push(id);
    for (const [site, byDoc] of Object.entries(history.files)) {
      const docs = (result.claims[site] = result.claims[site] || {});
      const dates = history.dates[site] || {}, tax = history.tax[site] || [];
      for (const [doc, facts] of Object.entries(byDoc)) {
        const claim = (docs[doc] = docs[doc] || { candidates: [], date: "", tax: false });
        claim.candidates.push(facts);
        if (!claim.date && typeof dates[doc] === "string") claim.date = dates[doc];
        if (tax.includes(doc)) claim.tax = true;
      }
    }
    for (const [site, day] of Object.entries(history.checked)) {
      if (!result.checked[site] || day > result.checked[site]) result.checked[site] = day;
    }
  }
  return result;
}

const emptySite = () => ({ ids: [], dates: {}, tax: [], paths: {}, checked: "" });

// The folder's record, from its entries and its files. `known` is the files
// hashed before, {path: {size, mtime, sha256}}; a file unchanged since is not
// read again. `progress(done, total)` is called as documents are settled, and
// `stopped()` asked between them. Answers { record: {site: {ids, dates, tax,
// paths: {id: path}, checked}}, files, documents, entries, hashed, unknown,
// problems }, or throws FolderUnavailable or Stopped. Changes nothing.
export async function reconcile(root, { known = {}, progress = () => {}, stopped = () => false, stallMs = STALL_MS } = {}) {
  const claims = await readClaims(root, { stopped, ms: stallMs });
  const fileAt = filesOf(root, stallMs);
  const record = {}, files = {};
  const settle = (site, doc, claim, facts) => {
    const r = (record[site] = record[site] || emptySite());
    r.ids.push(doc);
    r.paths[doc] = facts.path;
    if (claim.date) r.dates[doc] = claim.date;
    if (claim.tax) r.tax.push(doc);
  };
  const all = Object.entries(claims.claims).flatMap(([site, docs]) => Object.entries(docs).map(([doc, claim]) => ({ site, doc, claim })));
  let done = 0;
  const toHash = [];
  for (const item of all) {
    if (stopped()) throw new Stopped();
    const unread = [];
    let settled = false;
    for (const facts of item.claim.candidates) {
      const file = await fileAt(facts.path);
      if (!file || file.size !== facts.size) continue;
      const k = known[facts.path];
      if (k && k.size === file.size && k.mtime === file.lastModified) {
        // Hashed before and not changed since: its SHA-256 is known.
        files[facts.path] = k;
        if (k.sha256 !== facts.sha256) continue;
        settle(item.site, item.doc, item.claim, facts);
        settled = true;
        break;
      }
      unread.push({ facts, file });
    }
    if (!settled && unread.length) toHash.push({ ...item, unread });
    else progress((done += 1), all.length);
  }
  let hashed = 0;
  const hashOne = async (item) => {
    for (const { facts, file } of item.unread) {
      if (stopped()) throw new Stopped();
      const sum = files[facts.path] && files[facts.path].size === file.size && files[facts.path].mtime === file.lastModified
        ? files[facts.path].sha256
        : await reading(facts.path, stallMs, () => sha256(file));
      hashed += 1;
      files[facts.path] = { size: file.size, mtime: file.lastModified, sha256: sum };
      if (sum !== facts.sha256) continue;
      settle(item.site, item.doc, item.claim, facts);
      break;
    }
    progress((done += 1), all.length);
  };
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(HASH_AT_ONCE, toHash.length) }, async () => {
    while (next < toHash.length) await hashOne(toHash[next++]);
  }));
  for (const [site, day] of Object.entries(claims.checked)) (record[site] = record[site] || emptySite()).checked = day;
  const documents = Object.values(record).reduce((sum, r) => sum + r.ids.length, 0);
  return { record, files, documents, entries: claims.read.length, hashed, unknown: claims.unknown, problems: claims.problems };
}

// Of `ids`, documents of site `siteId` in the folder's record, those whose
// file is still in `root` as it was when the record was made. Asked just
// before they are skipped. One whose file is gone or changed is not among
// them, and is saved. Throws FolderUnavailable if the folder cannot be read.
export async function stillThere(root, folder, siteId, ids, { stallMs = STALL_MS } = {}) {
  const site = (folder.record || {})[siteId];
  const there = new Set();
  if (!site) return there;
  const fileAt = filesOf(root, stallMs);
  for (const id of ids) {
    const path = site.paths[id];
    const k = path && folder.files[path];
    if (!k) continue;
    const file = await fileAt(path);
    if (file && file.size === k.size && file.lastModified === k.mtime) there.add(id);
  }
  return there;
}

// The file `facts` names, read now: its size, time of last change and
// SHA-256 if it is in `root` with those bytes, or null when it is not there
// or holds other bytes. What is kept of a file is only ever what was read
// from it. Throws FolderUnavailable if the folder cannot be read.
export async function verified(root, facts, { stallMs = STALL_MS } = {}) {
  const file = await filesOf(root, stallMs)(facts.path);
  if (!file || file.size !== facts.size) return null;
  const sum = await reading(facts.path, stallMs, () => sha256(file));
  return sum === facts.sha256 ? { size: file.size, mtime: file.lastModified, sha256: sum } : null;
}

const pad = (n) => String(n).padStart(2, "0");
const stamp = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
// A name of its own: the time, and a random part, so no entry is written over.
async function newEntry(root, file, now) {
  const dir = await root.getDirectoryHandle(SHARED_DIR, { create: true });
  return writeFile(dir, `${stamp(now)} ${crypto.randomUUID()}.json`, new Blob([JSON.stringify(file)], { type: "application/json" }));
}

// A new entry in `root` for `saved`, [{id, facts}] of site `siteId`, just
// saved into it. Answers the entry's name and what it holds for the folder's
// record, { name, dates, tax }, or null with nothing to write. Throws when
// the entry cannot be written.
export async function writeEntry(root, siteId, saved, now = new Date()) {
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
  return { name: await newEntry(root, file, now), dates: dated, tax: taxed };
}

// A new entry in `root` of the sites looked at with nothing left to save,
// {site id: "YYYY-MM-DD"}. Answers its name, or null with nothing to write.
export async function writeChecked(root, days, now = new Date()) {
  if (!Object.keys(days).length) return null;
  return newEntry(root, { format: FORMAT, version: VERSION, exportedAt: now.toISOString(), sites: {}, checked: days }, now);
}
