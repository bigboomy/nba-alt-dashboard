/**
 * NBA dashboard — "Took it / Pass" choices (Google Apps Script web app).
 * Paste into the Sheet: Extensions → Apps Script → replace Code.gs → set PIN below → Deploy → New deployment
 * → type "Web app", Execute as "Me", Who has access "Anyone" → Deploy → copy the Web app URL.
 *
 * Reads are public (the dashboard shows everyone your choices). Writes need the PIN.
 * Rules: a PASS can only be set or changed before tip (no hindsight passes). TOOK can be set any time.
 * Writes only the `choices` tab (created on first use). Never touches cohort / stats / totals.
 */
const PIN = 'CHANGE_ME';                                   // <- set your PIN (4+ digits) before deploying
const SHEET_ID = '1OfVQgtxsqubqwPgXdnjxndwm4TnAPfOnpPwvPbKmBJA';
const HEAD = ['pick_id', 'choice', 'set_at', 'tip_utc'];

function out(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }

function tab() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  let s = ss.getSheetByName('choices');
  if (!s) {
    s = ss.insertSheet('choices');
    s.getRange(1, 1, 1, HEAD.length).setValues([HEAD]);
    s.getRange('A:D').setNumberFormat('@');
    s.setFrozenRows(1);
  }
  return s;
}

function doGet() {
  const v = tab().getDataRange().getValues().slice(1).filter(r => r[0] && r[1]);
  return out({ ok: true, choices: v.map(r => ({ pick_id: String(r[0]), choice: String(r[1]), set_at: String(r[2]) })) });
}

function tipOf(pickId) {
  const c = SpreadsheetApp.openById(SHEET_ID).getSheetByName('cohort');
  const v = c.getRange(2, 1, Math.max(c.getLastRow() - 1, 1), 3).getValues();
  for (let i = 0; i < v.length; i++) if (String(v[i][0]) === pickId) return String(v[i][2] || '');
  return null;
}

function doPost(e) {
  const cache = CacheService.getScriptCache();
  const fails = Number(cache.get('fails') || 0);
  if (fails >= 10) return out({ ok: false, error: 'Too many wrong PINs — try again in an hour' });
  let b;
  try { b = JSON.parse(e.postData.contents); } catch (x) { return out({ ok: false, error: 'bad request' }); }
  if (PIN === 'CHANGE_ME' || String(b.pin || '') !== PIN) {
    cache.put('fails', String(fails + 1), 3600);
    return out({ ok: false, error: PIN === 'CHANGE_ME' ? 'PIN not set in the script' : 'Wrong PIN' });
  }
  if (b.op === 'check') return out({ ok: true });
  const id = String(b.pick_id || ''), choice = String(b.choice || '');
  if (!id || ['TOOK', 'PASS', ''].indexOf(choice) < 0) return out({ ok: false, error: 'bad pick or choice' });
  const tip = tipOf(id);
  if (tip === null) return out({ ok: false, error: 'pick not in the Sheet yet' });
  const lock = LockService.getScriptLock(); lock.waitLock(10000);
  try {
    const s = tab(), v = s.getDataRange().getValues();
    let row = -1, cur = '';
    for (let i = 1; i < v.length; i++) if (String(v[i][0]) === id) { row = i + 1; cur = String(v[i][1]); }
    const tipped = tip && new Date() >= new Date(tip);
    if (tipped && (choice === 'PASS' || cur === 'PASS')) return out({ ok: false, error: 'Locked: the game has tipped, so a pass can no longer be set or changed' });
    const vals = [[id, choice, new Date().toISOString(), tip]];
    if (row > 0) s.getRange(row, 1, 1, 4).setValues(vals); else s.appendRow(vals[0]);
    return out({ ok: true, pick_id: id, choice: choice });
  } finally { lock.releaseLock(); }
}
