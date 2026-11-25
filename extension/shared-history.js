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
import { fromOwnName, pathFor } from "./naming.js";

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
  // Only a history folder that is not there holds nothing. Something else of
  // its name is a folder that cannot be read.
  const dir = await reading(SHARED_DIR, ms, () => root.getDirectoryHandle(SHARED_DIR)).catch((e) => {
    if (e && e.name === "NotFoundError") return null;
    throw e instanceof FolderUnavailable ? e : unavailable(SHARED_DIR, e);
  });
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
        if (claim.title === undefined && typeof facts.title === "string") claim.title = facts.title;
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

const emptySite = () => ({ ids: [], dates: {}, tax: [], paths: {}, sums: {}, checked: "" });

// The folder's record, from its entries and its files. `known` is the files
// hashed before, {path: {size, mtime, sha256}}; a file unchanged since is not
// read again. `progress(done, total)` is called as documents are settled, and
// `stopped()` asked between them. Answers { record: {site: {ids, dates, tax,
// paths: {id: path}, sums: {id: SHA-256}, checked}}, files, documents, entries, hashed, unknown,
// problems }, or throws FolderUnavailable or Stopped. Changes nothing.
export async function reconcile(root, { known = {}, progress = () => {}, stopped = () => false, stallMs = STALL_MS } = {}) {
  const claims = await readClaims(root, { stopped, ms: stallMs });
  const fileAt = filesOf(root, stallMs);
  const record = {}, files = {};
  const settle = (site, doc, claim, facts) => {
    const r = (record[site] = record[site] || emptySite());
    r.ids.push(doc);
    r.paths[doc] = facts.path;
    // The SHA-256 this document was found by, so the file at its path is
    // only ever taken for it while it holds these bytes (stillThere).
    r.sums[doc] = facts.sha256;
    if (claim.date) r.dates[doc] = claim.date;
    if (claim.tax) r.tax.push(doc);
    // Its title, when an entry gives it: what renaming its file goes by.
    if (typeof claim.title === "string") (r.titles = r.titles || {})[doc] = claim.title;
    // Other places entries give for it: where it was before it was renamed,
    // or where another computer saved it too. A copy left there can go
    // (renamePlan).
    const others = [...new Set(claim.candidates.filter((c) => c.sha256 === facts.sha256 && c.path !== facts.path).map((c) => c.path))];
    if (others.length) (r.others = r.others || {})[doc] = others;
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
    // What was last read at the path must be this document's own bytes: two
    // entries can name one path for different documents.
    if (!k || k.sha256 !== site.sums[id]) continue;
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
export async function writeEntry(root, siteId, saved, now = new Date(), known = {}) {
  const byDoc = new Map();
  for (const s of saved) if (s && typeof s.id === "string" && s.id && s.facts) byDoc.set(s.id, s.facts);
  const docs = [...byDoc.keys()];
  if (!docs.length) return null;
  if (docs.length > MAX_IDS) throw new Error(`${docs.length} documents are more than one history file can hold.`);
  const kept = await local().get([`dates:${siteId}`, `tax:${siteId}`]);
  // `known`, { dates, tax }: what the folder's record says of them, for
  // documents another computer saved, which this copy keeps no dates of.
  const dates = { ...(known.dates || {}), ...(isObject(kept[`dates:${siteId}`]) ? kept[`dates:${siteId}`] : {}) };
  const tax = [...(known.tax || []), ...(Array.isArray(kept[`tax:${siteId}`]) ? kept[`tax:${siteId}`] : [])];
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

// -- renaming what is saved to the file name settings of now -------------------
//
// Asked for in Settings, never done by itself. A document's new name is made
// from its date and its title: the title an entry gives, or the one its file
// name says when the site's own pattern made that name. A document whose
// title cannot be known is left as it is.

// `to` itself, or `to` with the " (n)" a second file of its name is given.
const isOrNumbered = (to, from) => {
  if (to === from) return true;
  const dot = to.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [to.slice(0, dot), to.slice(dot)] : [to, ""];
  return from.startsWith(`${stem} (`) && from.endsWith(`)${ext}`) && /^\d+$/.test(from.slice(stem.length + 2, from.length - ext.length - 1));
};

// What renaming would do, from the folder's `record`: { moves: [{site, id,
// from, to, title, date}], alone } where `alone` counts the documents whose
// title or date cannot be known. Changes nothing.
export function renamePlan(record, providers, naming) {
  const moves = [];
  let alone = 0;
  for (const provider of providers) {
    const site = record[provider.id];
    if (!site || provider.added) continue;
    for (const id of site.ids) {
      const from = site.paths[id];
      const parsed = fromOwnName(provider, from.split("/").pop());
      const title = site.titles && typeof site.titles[id] === "string" ? site.titles[id] : parsed ? parsed.title : undefined;
      const date = site.dates[id] || (parsed ? parsed.date : "");
      if (title === undefined || !date) { alone += 1; continue; }
      const to = pathFor(naming, provider, { id, date, title });
      if (!isOrNumbered(to, from)) moves.push({ site: provider.id, id, from, to, title, date });
    }
  }
  // Copies that may be left where a document was before: checked, and
  // removed only if they hold its very bytes, when the plan is carried out.
  const found = new Set(Object.values(record).flatMap((r) => Object.values(r.paths)));
  const old = [];
  for (const provider of providers) {
    const site = record[provider.id];
    if (!site || provider.added || !site.others) continue;
    for (const [id, paths] of Object.entries(site.others)) {
      for (const path of paths) if (!found.has(path)) old.push({ site: provider.id, id, path });
    }
  }
  return { moves, alone, old };
}

// Of a plan's `old`, those with a file still there, of the size of the
// document's own: the copies carrying the plan out would look at. Throws
// FolderUnavailable if the folder cannot be read.
export async function oldCopies(root, folder, old, { stallMs = STALL_MS } = {}) {
  const fileAt = filesOf(root, stallMs);
  const left = [];
  for (const o of old) {
    const site = folder.record[o.site];
    const k = site && folder.files[site.paths[o.id]];
    const file = k ? await fileAt(o.path) : null;
    if (file && file.size === k.size) left.push(o);
  }
  return left;
}

// Writes and removals begun and not yet ended, a time limit or not: nothing
// that changes the folder starts while one is still going (mutationsSettled).
const going = new Set();
export const mutationsSettled = () => Promise.allSettled([...going]);
const writing = (what, ms, work) => {
  let timer;
  const failed = (e) => new FolderUnavailable(`Your folder could not be written (${what}${e && e.name ? `: ${e.name}` : " did not answer"}). The files renamed so far are kept. Rename again to go on.`);
  const limit = new Promise((resolve, reject) => { timer = setTimeout(() => reject(failed()), ms); });
  const done = Promise.resolve().then(work).catch((e) => { throw e instanceof FolderUnavailable ? e : failed(e); });
  going.add(done);
  done.catch(() => {}).finally(() => going.delete(done));
  return Promise.race([done, limit]).finally(() => clearTimeout(timer));
};

// Carries out the `moves` of one site (renamePlan), against `folder`, the
// read of `root` they were planned from ({record, files}). Each file is read
// and must still hold the bytes it was found by; it is written under its new
// name, never over another document, and read back. Then one new entry lists
// the new places, and only then are the old files removed. A file that
// changed since the read, or is claimed by a document not being moved, is
// left where it is. If the folder stops answering part way, what was
// written so far is still listed and its old files removed, and
// FolderUnavailable is thrown. `old`, [{id, path}] of the plan: copies left
// where documents were before, removed if they hold the document's bytes
// and its own file is there. `taken`, one Set for all the sites of one
// asking: the places files were renamed to, none of which is removed.
// Answers { renamed, skipped, removed, entry }.
export async function renameSite(root, folder, siteId, moves, { now = new Date(), stopped = () => false, progress = () => {}, stallMs = STALL_MS, old = [], taken = new Set() } = {}) {
  const site = (folder.record || {})[siteId];
  const fileAt = filesOf(root, stallMs);
  const landed = [];
  let skipped = 0, failure = null;
  for (const move of moves) {
    if (stopped()) break;
    try {
      const sum = site && site.paths[move.id] === move.from ? site.sums[move.id] : null;
      const file = sum ? await fileAt(move.from) : null;
      if (!file || (await reading(move.from, stallMs, () => sha256(file))) !== sum) { skipped += 1; continue; }
      const to = await writing(move.to, stallMs, () => writeFile(root, move.to, file, { reuseSame: true, anyName: false }));
      if (to === move.from) continue;
      if (!(await verified(root, { path: to, size: file.size, sha256: sum }, { stallMs }))) throw new FolderUnavailable(`Your folder did not keep ${to} as it was written. Its old file is left as it is. Rename again to go on.`);
      landed.push({ id: move.id, from: move.from, date: move.date, seen: file.lastModified, facts: { path: to, size: file.size, sha256: sum, title: move.title } });
    } catch (e) {
      failure = e instanceof FolderUnavailable ? e : new FolderUnavailable(`Renaming stopped at ${move.from}: ${String(e && e.message)}`);
      break;
    } finally {
      progress();
    }
  }
  // A file is removed only if no document is found by it: not one of the
  // record, and not one just renamed to it.
  const moved = new Set(landed.map((l) => l.id));
  const held = new Set([
    ...Object.entries(folder.record).flatMap(([other, r]) => Object.entries(r.paths).filter(([id]) => other !== siteId || !moved.has(id)).map(([, path]) => path)),
    ...landed.map((l) => l.facts.path),
    // And by every site renamed before this one in the same asking (`taken`).
    ...taken,
  ]);
  for (const l of landed) taken.add(l.facts.path);
  // Removes the file at `path` if it is still as it was (`size`, and `seen`
  // its time of last change when given, or else its SHA-256 `sum`), and the
  // document's own file, `kept`, read now, still holds its bytes.
  const remove = async (path, { size, seen, sum }, kept) => {
    if (held.has(path)) return false;
    // Its folder is found first, so nothing is waited for between reading
    // the document's own file and removing this one.
    const parts = path.split("/");
    const leaf = parts.pop();
    let dir = root;
    for (const part of parts) dir = await reading(path, stallMs, () => dir.getDirectoryHandle(part));
    const there = await reading(path, stallMs, async () => (await dir.getFileHandle(leaf)).getFile());
    if (there.size !== size) return false;
    if (seen !== undefined ? there.lastModified !== seen : (await reading(path, stallMs, () => sha256(there))) !== sum) return false;
    if (!(await verified(root, kept, { stallMs }))) return false;
    await writing(path, stallMs, () => dir.removeEntry(leaf));
    return true;
  };
  const cleanup = async (path, facts, kept) => {
    try {
      return await remove(path, facts, kept);
    } catch (e) {
      if (!notThere(e)) failure = failure || (e instanceof FolderUnavailable ? e : new FolderUnavailable(`An old file could not be removed (${path}). Rename again to go on.`));
      return false;
    }
  };
  let entry = null, removed = 0;
  if (landed.length) {
    const ids = landed.map((l) => l.id);
    const dated = Object.fromEntries(landed.map((l) => [l.id, site.dates[l.id] || l.date]).filter(([, date]) => date));
    try {
      entry = (await writing(SHARED_DIR, stallMs, () => writeEntry(root, siteId, landed.map(({ id, facts }) => ({ id, facts })), now, { dates: dated, tax: site.tax.filter((id) => ids.includes(id)) }))).name;
    } catch (e) {
      // Not listed: every old file stays, and the copies are found by name next time.
      throw failure || e;
    }
    for (const l of landed) await cleanup(l.from, { size: l.facts.size, seen: l.seen }, l.facts);
  }
  // Copies left where documents were before, by an earlier rename that could
  // not remove them or by another computer.
  for (const o of old) {
    if (stopped() || failure) break;
    const path = site && site.paths[o.id], sum = site && site.sums[o.id];
    const k = path && folder.files[path];
    if (k && k.sha256 === sum && (await cleanup(o.path, { size: k.size, sum }, { path, size: k.size, sha256: sum }))) removed += 1;
    progress();
  }
  if (failure) throw failure;
  return { renamed: landed.length, skipped, removed, entry };
}
