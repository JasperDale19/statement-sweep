// A folder you chose, outside Chrome's Downloads. Chrome keeps the access in a
// handle, which is stored here and used by the Settings page and the engine.
//
// `destination` is your folder. `inbox` is a folder inside Downloads: a site's
// own download can only land in Downloads, so it lands there under a random
// name and is moved to your folder from there.

export const INBOX = "Statement Sweep Inbox";
const STORE = "handles";

function database() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("statement-sweep", 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function withStore(mode, work) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = work(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    // Out of room, say: without this an aborted write would never answer.
    transaction.onabort = () => reject(transaction.error || new Error("The browser's storage did not take it."));
  });
}

export const getFolder = (name) => withStore("readonly", (store) => store.get(name));
export const setFolder = (name, handle) => withStore("readwrite", (store) => store.put(handle, name));
export const forgetFolders = () => withStore("readwrite", (store) => store.clear());

export const isGranted = async (handle) => (await handle.queryPermission({ mode: "readwrite" })) === "granted";
// Needs a press of yours, so it works from a page and not from the engine.
export const askFor = async (handle) => (await isGranted(handle)) || (await handle.requestPermission({ mode: "readwrite" })) === "granted";

// The main folder is made for you inside the one you chose. If you chose one
// with that same name, it is used as is rather than nested inside itself.
export function withinChosen(chosenName, path) {
  const [first, ...rest] = path.split("/");
  return rest.length && first.toLowerCase() === chosenName.toLowerCase() ? rest.join("/") : path;
}

async function freeName(dir, name) {
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let n = 0; n < 1000; n += 1) {
    const candidate = n ? `${stem} (${n})${ext}` : name;
    try {
      await dir.getFileHandle(candidate);
    } catch (e) {
      if (e.name === "NotFoundError") return candidate;
      throw e;
    }
  }
  throw new Error("Too many files share that name.");
}

// Writes under `path`, making its folders, never over a file already there,
// and only returns once what landed is as long as what was sent. `replace`
// writes over the file of that name instead: only a run's own log (log.js),
// which is written again as the run goes, ever asks for it.
//
// Choosing a free name and making the file are one step, taken one at a time
// (`claim`): two documents saved at once under one name would otherwise both
// find it free, and the second would be written over the first. The file is
// made empty in that step, so the name is taken before the slow write starts.
// A folder that does not answer is given CLAIM_WAIT_MS, then the save fails
// and the next one goes on: a save given up on never writes.
const CLAIM_WAIT_MS = 30000;
let claims = Promise.resolve();
function claim(work) {
  const next = claims.then(() => new Promise((resolve, reject) => {
    // A claim given up on makes no file afterwards: `live()` is asked first.
    let live = true;
    const timer = setTimeout(() => { live = false; reject(new Error("The chosen folder did not answer within 30 seconds.")); }, CLAIM_WAIT_MS);
    work(() => live).then(resolve, reject).finally(() => clearTimeout(timer));
  }));
  claims = next.catch(() => {});
  return next;
}

//
// `reuseSame`, for documents: a file already there under the name, or one of
// its "(1)", "(2)" names, with exactly these bytes is the same document, saved
// before by this computer or another. So is a file of the same folder under
// any other name. It is left as it is and its path answered, so saving a
// document twice never makes a second copy. A save
// begun under the name and never finished left it empty; that name is used.
async function sameBytes(file, blob) {
  if (file.size !== blob.size) return false;
  const [a, b] = await Promise.all([file.arrayBuffer(), blob.arrayBuffer()]);
  const x = new Uint8Array(a), y = new Uint8Array(b);
  for (let i = 0; i < x.length; i += 1) if (x[i] !== y[i]) return false;
  return true;
}

// Paths this copy is writing now, and how many bytes each is to hold. An
// empty file at one of them is a save under way, not one given up on. A save
// of as many bytes under the same name is taken to be the same document
// tried again while its first save has not ended: it is refused, to be tried
// another time, rather than saved beside it. A write that never ends is
// forgotten when the browser next starts the extension, and its empty file
// is then taken over.
const underWay = new Map();
// The bytes of each, for a save of the same bytes under another name begun
// meanwhile (sameUnderWay).
const underWayBytes = new Map();
// Looking for the same bytes under another name and then claiming a name are
// one step for the saves that look, taken one at a time: two of the same
// bytes begun together would otherwise each find nothing and both be saved.
let lookings = Promise.resolve();
function looking(work) {
  const next = lookings.then(work);
  lookings = next.catch(() => {});
  return next;
}
async function sameUnderWay(parts, blob) {
  const prefix = parts.length ? `${parts.join("/")}/` : "";
  for (const [key, other] of underWayBytes) {
    if (key.startsWith(prefix) && !key.slice(prefix.length).includes("/") && (await sameBytes(other, blob))) return true;
  }
  return false;
}
export class StillSaving extends Error {}

// A file of this folder with exactly these bytes under any other name: the
// same document as another computer, or an older file name pattern, named it.
// Only files of the same length are read. Looked for among the files beside
// the wanted name, not in other folders.
async function sameElsewhere(dir, name, blob) {
  if (!blob.size) return null;
  const names = [];
  for await (const [leaf, handle] of dir.entries()) if (handle.kind === "file" && leaf !== name) names.push([leaf, handle]);
  names.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  for (const [leaf, handle] of names) {
    let file;
    try {
      file = await handle.getFile();
    } catch (e) {
      if (e.name === "NotFoundError") continue;
      throw e;
    }
    if (await sameBytes(file, blob)) return leaf;
  }
  return null;
}

async function nameOrSame(dir, name, blob, keyOf) {
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let n = 0; n < 1000; n += 1) {
    const candidate = n ? `${stem} (${n})${ext}` : name;
    let there;
    try {
      there = await dir.getFileHandle(candidate);
    } catch (e) {
      if (e.name === "NotFoundError") return { name: candidate, same: false };
      throw e;
    }
    const file = await there.getFile();
    // An empty file under the name is a save that was begun and not finished
    // (given up on, or cut short): its name is taken over, so trying again
    // does not put a "(1)" beside it. No document is empty.
    if (file.size === 0 && blob.size > 0) {
      if (!underWay.has(keyOf(candidate))) return { name: candidate, same: false };
      if (underWay.get(keyOf(candidate)) === blob.size) throw new StillSaving("This document is still being saved from an earlier try. It is tried again next time.");
      continue;
    }
    if (await sameBytes(file, blob)) return { name: candidate, same: true };
  }
  throw new Error("Too many files share that name.");
}

export async function writeFile(root, path, blob, { replace = false, reuseSame = false, anyName = true } = {}) {
  const parts = path.split("/");
  const fileName = parts.pop();
  let dir = root;
  for (const part of parts) dir = await dir.getDirectoryHandle(part, { create: true });
  const keyOf = (leaf) => [...parts, leaf].join("/");
  const gone = () => new Error("The chosen folder did not answer within 30 seconds.");
  const take = (leaf) => { underWay.set(keyOf(leaf), blob.size); underWayBytes.set(keyOf(leaf), blob); };
  const claimed = () => claim(async (live) => {
    if (reuseSame && !replace) {
      const found = await nameOrSame(dir, fileName, blob, keyOf);
      if (found.same) return [found.name, null];
      if (!live()) throw gone();
      take(found.name);
      return [found.name, await dir.getFileHandle(found.name, { create: true })];
    }
    const chosen = replace ? fileName : await freeName(dir, fileName);
    if (!live()) throw gone();
    if (!replace) take(chosen);
    return [chosen, await dir.getFileHandle(chosen, { create: true })];
  });
  // The same bytes under another name of the folder: looked for before the
  // name is claimed, so a large folder does not use up the time a claim is
  // given, and given the same time of its own. `anyName: false`, for a file
  // being renamed: its old name is beside it.
  const [name, handle] = !(reuseSame && !replace && anyName) ? await claimed() : await looking(async () => {
    let timer;
    const elsewhere = await Promise.race([
      sameElsewhere(dir, fileName, blob),
      new Promise((resolve, reject) => { timer = setTimeout(() => reject(gone()), CLAIM_WAIT_MS); }),
    ]).finally(() => clearTimeout(timer));
    if (elsewhere) return [elsewhere, null];
    // Being saved in this folder right now, under this name or another: not
    // saved beside it.
    if (await sameUnderWay(parts, blob)) throw new StillSaving("This document is still being saved from an earlier try. It is tried again next time.");
    return claimed();
  });
  if (!handle) return keyOf(name);
  try {
    const out = await handle.createWritable();
    await out.write(blob);
    await out.close();
    if ((await handle.getFile()).size !== blob.size) throw new Error("The saved copy is incomplete.");
  } finally {
    underWay.delete(keyOf(name));
    underWayBytes.delete(keyOf(name));
  }
  return keyOf(name);
}

export async function moveFromInbox(inbox, stageName, root, path, options = {}) {
  const file = await (await inbox.getFileHandle(stageName)).getFile();
  // A statement must be a PDF by its bytes, whatever the site labelled it.
  if (/\.pdf$/i.test(path)) {
    const head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    if (String.fromCharCode(...head) !== "%PDF-") throw new Error("The site sent something that is not a PDF.");
  }
  return writeFile(root, path, file, options);
}
