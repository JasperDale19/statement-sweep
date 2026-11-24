// The record of what has been saved, shared through the folder you chose, so
// copies of Statement Sweep on other computers that choose the same folder
// (in iCloud Drive or Google Drive, say) do not save a document again.
//
// Each copy has an id of its own (`copyId`) and writes one file,
// `Statement Sweep History/<copyId>.json`, in the export format (history.js).
// It never writes another copy's file, so two computers syncing the folder at
// once can never write over each other's record, and a site one copy does not
// know stays in the file of the copy that does.
//
// A copy's file holds only the documents it saved into this folder, with
// their dates and which are tax forms. They are kept as each is saved, under
// `shared:<folder token>:<site id>`; choosing a folder (or Downloads) starts a
// new token, so a save that finishes into the old folder is never counted as
// in the new one. Documents a copy saved before, to Downloads or another
// folder, are left out: they are not in this folder, so another copy should
// still save them here. Never the folder and file name settings or the
// switched-off sites.
//
// Each sync reads this copy's own file back first, so going back to a folder
// chosen before keeps what was saved there. A file of this copy's name that
// lists documents this copy never saved was written by another copy with the
// same id (a copied Chrome profile): this copy takes a new id and reads that
// file as the other copy's.
//
// Every other copy's file is read and added to this copy's record with
// importHistory, which only ever adds and never changes a setting. A file
// that cannot be read, or is from a newer version, is reported and left as it
// is; if it is this copy's own, this copy's file is not written until it is
// fixed. `historySharingOff: true` in chrome.storage.local turns it all off.
import { FORMAT, MAX_FILE_BYTES, MAX_IDS, VERSION, importHistory, readHistory } from "./history.js";
import { writeFile } from "./folders.js";

export const SHARED_DIR = "Statement Sweep History";
export const SHARING_OFF = "historySharingOff";
const SHARED = "shared:";
const COPY = "copyId";
const TOKEN = "sharedFolder";
const local = () => chrome.storage.local;
const isObject = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const goodId = (v) => typeof v === "string" && /^[\w-]{1,64}$/.test(v);

// A stored id, or a new one kept from now on.
async function kept(key) {
  const { [key]: id } = await local().get(key);
  if (goodId(id)) return id;
  const made = crypto.randomUUID();
  await local().set({ [key]: made });
  return made;
}

// This copy's id, made the first time it is asked for.
export const copyId = () => kept(COPY);
// The token of the folder chosen now. Asked for before a save, and passed
// back to noteShared after it.
export const folderToken = () => kept(TOKEN);

// Documents just saved into the chosen folder, while `token` was its token.
export async function noteShared(siteId, ids, token) {
  const key = `${SHARED}${token}:${siteId}`;
  const had = (await local().get(key))[key];
  const list = Array.isArray(had) ? had : [];
  const fresh = ids.filter((id) => !list.includes(id));
  if (fresh.length) await local().set({ [key]: [...list, ...fresh] });
}

// A different folder is chosen, or Downloads: nothing saved so far is known
// to be in it. Called once the new folder is stored.
export async function forgetShared() {
  await local().set({ [TOKEN]: crypto.randomUUID() });
  const all = await local().get(null);
  const keys = Object.keys(all).filter((k) => k.startsWith(SHARED));
  if (keys.length) await local().remove(keys);
}

// {site id: [doc ids]} saved into the folder of `token`.
function sharedOf(all, token) {
  const prefix = `${SHARED}${token}:`;
  const sites = {};
  for (const [key, value] of Object.entries(all)) {
    if (!key.startsWith(prefix) || !Array.isArray(value)) continue;
    const ids = [...new Set(value.filter((v) => typeof v === "string" && v))];
    if (ids.length) sites[key.slice(prefix.length)] = ids;
  }
  return sites;
}

// This copy's file: what it saved into the folder, with those documents'
// dates and tax marks, and how many documents that is.
function ownFile(all, token, now) {
  const sites = sharedOf(all, token), dates = {}, tax = {};
  let documents = 0;
  for (const [id, ids] of Object.entries(sites)) {
    documents += ids.length;
    const keptDates = isObject(all[`dates:${id}`]) ? all[`dates:${id}`] : {};
    const dated = Object.fromEntries(ids.filter((doc) => typeof keptDates[doc] === "string").map((doc) => [doc, keptDates[doc]]));
    if (Object.keys(dated).length) dates[id] = dated;
    const taxed = Array.isArray(all[`tax:${id}`]) ? ids.filter((doc) => all[`tax:${id}`].includes(doc)) : [];
    if (taxed.length) tax[id] = taxed;
  }
  const file = { format: FORMAT, version: VERSION, exportedAt: now.toISOString(), sites };
  if (Object.keys(dates).length) file.dates = dates;
  if (Object.keys(tax).length) file.tax = tax;
  return { file, documents };
}

async function readFile(handle) {
  try {
    const got = await handle.getFile();
    return got.size > MAX_FILE_BYTES ? { ok: false, problem: "It is too large to be a Statement Sweep history file." } : readHistory(await got.text());
  } catch (e) {
    return { ok: false, problem: `It could not be read: ${e.name || "Error"}.` };
  }
}

// Whether every document a file lists is in this copy's record.
async function allSeen(history) {
  const ids = Object.keys(history.sites);
  const now = ids.length ? await local().get(ids.map((id) => `seen:${id}`)) : {};
  return ids.every((id) => {
    const seen = new Set(Array.isArray(now[`seen:${id}`]) ? now[`seen:${id}`] : []);
    return history.sites[id].every((doc) => seen.has(doc));
  });
}

class GaveUp extends Error {}

// Reads every other copy's file in `root` (your chosen folder) into this
// copy's record, then writes this copy's own. `stillWanted` is asked before
// each change to the record and before writing, so a sync its caller gave up
// on (a folder that stopped answering) changes nothing after that. Answers
// { off: true } when sharing is off, { gaveUp: true } when given up on, or
// { read, imported, unknown, problems: [{name, problem}], wrote }.
export async function syncShared(root, { stillWanted = () => true, now = new Date() } = {}) {
  const check = () => { if (!stillWanted()) throw new GaveUp(); };
  try {
    if ((await local().get(SHARING_OFF))[SHARING_OFF] === true) return { off: true };
    const token = await folderToken();
    let mine = `${await copyId()}.json`;
    const result = { read: [], imported: 0, unknown: [], problems: [], wrote: false };
    const found = await root.getDirectoryHandle(SHARED_DIR).catch((e) => { if (e.name === "NotFoundError") return null; throw e; });
    const handles = [];
    if (found) {
      for await (const [name, handle] of found.entries()) {
        if (handle.kind === "file" && name.endsWith(".json") && !name.startsWith(".")) handles.push([name, handle]);
      }
    }
    handles.sort(([a], [b]) => a.localeCompare(b));
    const take = async (name, history) => {
      check();
      const done = await importHistory(history, { useSettings: false });
      result.read.push(name);
      result.imported += done.added;
      for (const id of done.unknown) if (!result.unknown.includes(id)) result.unknown.push(id);
    };
    let blocked = false;
    // This copy's own file first.
    const own = handles.find(([name]) => name === mine);
    if (own) {
      const history = await readFile(own[1]);
      if (!history.ok) {
        blocked = true;
        result.problems.push({ name: mine, problem: `${history.problem} This copy's own file is left as it is and not written until it is fixed or removed.` });
      } else if (await allSeen(history)) {
        check();
        for (const [id, ids] of Object.entries(history.sites)) await noteShared(id, ids, token);
      } else {
        // Another copy writes this name: it keeps it, and this copy takes a new one.
        check();
        await local().remove(COPY);
        mine = `${await copyId()}.json`;
        result.problems.push({ name: own[0], problem: "It is written by another copy with this copy's id, such as a copied Chrome profile. This copy now writes a file of its own." });
        await take(own[0], history);
      }
    }
    for (const [name, handle] of handles) {
      if (name === mine || (own && name === own[0])) continue;
      const history = await readFile(handle);
      if (!history.ok) {
        result.problems.push({ name, problem: history.problem });
        continue;
      }
      await take(name, history);
    }
    if (blocked) return result;
    const { file, documents } = ownFile(await local().get(null), token, now);
    if (!documents) return result;
    const text = JSON.stringify(file);
    const largest = Math.max(...Object.values(file.sites).map((ids) => ids.length));
    if (largest > MAX_IDS || text.length > MAX_FILE_BYTES) {
      result.problems.push({ name: mine, problem: "This copy's record is too large for one history file, so it was not written." });
      return result;
    }
    check();
    const dir = found || (await root.getDirectoryHandle(SHARED_DIR, { create: true }));
    await writeFile(dir, mine, new Blob([text], { type: "application/json" }), { replace: true });
    result.wrote = true;
    return result;
  } catch (e) {
    if (e instanceof GaveUp) return { gaveUp: true };
    throw e;
  }
}
