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
// A copy's file holds only the documents it saved into this folder
// (`shared:<site id>`, kept beside `seen:` as each is saved there), with their
// dates and which are tax forms. Documents it saved before, to Downloads or
// another folder, are left out: they are not in this folder, so another copy
// should still save them here. Never the folder and file name settings or the
// switched-off sites.
//
// Every other copy's file is read and added to this copy's record with
// importHistory, which only ever adds and never changes a setting. A file
// that cannot be read, or is from a newer version, is reported and left as it
// is. `historySharingOff: true` in chrome.storage.local turns all of it off.
import { FORMAT, MAX_FILE_BYTES, MAX_IDS, VERSION, importHistory, readHistory } from "./history.js";
import { writeFile } from "./folders.js";

export const SHARED_DIR = "Statement Sweep History";
export const SHARING_OFF = "historySharingOff";
const SHARED = "shared:";
const COPY = "copyId";
const local = () => chrome.storage.local;
const isObject = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);

// This copy's id, made the first time it is asked for and kept after.
export async function copyId() {
  const { [COPY]: kept } = await local().get(COPY);
  if (typeof kept === "string" && /^[\w-]{1,64}$/.test(kept)) return kept;
  const made = crypto.randomUUID();
  await local().set({ [COPY]: made });
  return made;
}

// Documents just saved into the chosen folder.
export async function noteShared(siteId, ids) {
  const key = SHARED + siteId;
  const had = (await local().get(key))[key];
  const list = Array.isArray(had) ? had : [];
  const fresh = ids.filter((id) => !list.includes(id));
  if (fresh.length) await local().set({ [key]: [...list, ...fresh] });
}

// A different folder is chosen, or Downloads: nothing saved so far is in it.
export async function forgetShared() {
  const all = await local().get(null);
  const keys = Object.keys(all).filter((k) => k.startsWith(SHARED));
  if (keys.length) await local().remove(keys);
}

// This copy's file: what it saved into the folder, with those documents'
// dates and tax marks, and how many documents that is.
async function ownFile(now) {
  const all = await local().get(null);
  const sites = {}, dates = {}, tax = {};
  let documents = 0;
  for (const [key, value] of Object.entries(all)) {
    if (!key.startsWith(SHARED) || !Array.isArray(value)) continue;
    const id = key.slice(SHARED.length);
    const ids = [...new Set(value.filter((v) => typeof v === "string" && v))];
    if (!ids.length) continue;
    sites[id] = ids;
    documents += ids.length;
    const kept = isObject(all[`dates:${id}`]) ? all[`dates:${id}`] : {};
    const dated = Object.fromEntries(ids.filter((doc) => typeof kept[doc] === "string").map((doc) => [doc, kept[doc]]));
    if (Object.keys(dated).length) dates[id] = dated;
    const taxed = Array.isArray(all[`tax:${id}`]) ? ids.filter((doc) => all[`tax:${id}`].includes(doc)) : [];
    if (taxed.length) tax[id] = taxed;
  }
  const file = { format: FORMAT, version: VERSION, exportedAt: now.toISOString(), sites };
  if (Object.keys(dates).length) file.dates = dates;
  if (Object.keys(tax).length) file.tax = tax;
  return { file, documents };
}

// Reads every other copy's file in `root` (your chosen folder) into this
// copy's record, then writes this copy's own. `stillWanted`, asked just
// before writing, lets a caller that gave up on a slow folder keep a late
// answer from writing an old record. Answers { off: true } when sharing is
// off, or { read, imported, unknown, problems: [{name, problem}], wrote }.
export async function syncShared(root, { stillWanted = () => true, now = new Date() } = {}) {
  if ((await local().get(SHARING_OFF))[SHARING_OFF] === true) return { off: true };
  const mine = `${await copyId()}.json`;
  const { file, documents } = await ownFile(now);
  const dir = documents
    ? await root.getDirectoryHandle(SHARED_DIR, { create: true })
    : await root.getDirectoryHandle(SHARED_DIR).catch((e) => { if (e.name === "NotFoundError") return null; throw e; });
  const result = { read: [], imported: 0, unknown: [], problems: [], wrote: false };
  if (dir) {
    const names = [];
    for await (const [name, handle] of dir.entries()) {
      if (handle.kind === "file" && name.endsWith(".json") && !name.startsWith(".") && name !== mine) names.push([name, handle]);
    }
    names.sort(([a], [b]) => a.localeCompare(b));
    for (const [name, handle] of names) {
      let history;
      try {
        const got = await handle.getFile();
        history = got.size > MAX_FILE_BYTES ? { ok: false, problem: "It is too large to be a Statement Sweep history file." } : readHistory(await got.text());
      } catch (e) {
        history = { ok: false, problem: `It could not be read: ${e.name || "Error"}.` };
      }
      if (!history.ok) {
        result.problems.push({ name, problem: history.problem });
        continue;
      }
      const done = await importHistory(history, { useSettings: false });
      result.read.push(name);
      result.imported += done.added;
      for (const id of done.unknown) if (!result.unknown.includes(id)) result.unknown.push(id);
    }
  }
  if (!documents) return result;
  const text = JSON.stringify(file);
  const largest = Math.max(...Object.values(file.sites).map((ids) => ids.length));
  if (largest > MAX_IDS || text.length > MAX_FILE_BYTES) {
    result.problems.push({ name: mine, problem: "This copy's record is too large for one history file, so it was not written." });
    return result;
  }
  if (!stillWanted()) return result;
  await writeFile(dir, mine, new Blob([text], { type: "application/json" }), { replace: true });
  result.wrote = true;
  return result;
}
