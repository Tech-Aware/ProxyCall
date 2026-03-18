/**
* webapp_activation.gs
* Web App Apps Script — Activation + mise à jour (sans toucher au backend Python)
*
* Logs: aucun PII (email/tel/nom) en clair :
* - on log uniquement des hash (sha256) + pending_id + codes d'erreur + timings
* - on n'écrit jamais le payload complet /confirmations/create, ni les propriétés sensibles
*/
const WEBAPP_CFG = {
 SHEET_PENDING: "CONFIRMATION_PENDING",
 SHEET_CLIENTS: "Clients",
 SHEET_LOG: "WEBAPP_SUBMISSIONS", // optionnel (sera créé si absent)

DEFAULT_COUNTRY_ISO: "FR",
 DEFAULT_NUMBER_TYPE: "national", // alias -> local côté backend

CONFIRM_CREATE_PATH: "/confirmations/create",

COOLDOWN_SECONDS: 180, // 3 minutes
 DAILY_LIMIT_PER_PHONE: 5,
 DAILY_LIMIT_PER_EMAIL: 10,

PENDING_ACTIVE_WINDOW_HOURS: 24,

PENDING_REQUIRED_HEADERS: [

 "pending_id",

 "client_name",

 "client_mail",

 "client_real_phone",

 "proxy_number",

 "otp",

 "status",

 "created_at",

 "verified_at",
 ],
 PENDING_EXTRA_HEADERS: [

 "status_detail",

 "otp_requests_count",

 "otp_last_requested_at",

 "request_source",

 "first_name_norm",

 "last_name_norm",

 "identity_key",

 "matched_client_id",

 "matched_by",

 "update_policy",
 ],

ALLOW_IDENTITY_MATCH_IF_AVAILABLE: true, };
function doGet() {
 return HtmlService.createHtmlOutputFromFile("index")

 .setTitle("Activer / Mettre à jour vos coordonnées")

 .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL); }
function requestActivation(payload) {
 const reqId = Utilities.getUuid(); // corrélation logs
 const t0 = Date.now();

try {

 // --- LOG: entrée (sans PII)

 const clientKey = sha256Hex_(


 normEmail_(payload?.email) + "|" + normPhoneE164Loose_(payload?.phone) + "|" + String(payload?.first_name || "") + "|" + String(payload?.last_name || "")

 ).slice(0, 12);


logConsole_("REQ_START", {


 req_id: reqId,


 client_key: clientKey,

 });


const clean = validateAndNormalize_(payload);


// Hashes non-sensibles pour corrélation

 const emailHash = sha256Hex_(clean.email_norm).slice(0, 12);

 const phoneHash = sha256Hex_(clean.phone_e164).slice(0, 12);

 const identityHash = sha256Hex_(clean.identity_key).slice(0, 12);


// --- Sheets access

 let ss;

 try {


 ss = SpreadsheetApp.getActiveSpreadsheet();

 } catch (e) {


 logConsole_("SHEETS_ACCESS_DENIED", {



 req_id: reqId,



 err: safeErr_(e),


 });


 throw e;

 }


const shPending = mustGetSheet_(ss, WEBAPP_CFG.SHEET_PENDING);

 const shClients = mustGetSheet_(ss, WEBAPP_CFG.SHEET_CLIENTS);

 const shLog = getOrCreateLogSheet_(ss, WEBAPP_CFG.SHEET_LOG);


// --- LOG: validated

 log_(shLog, "VALIDATED", {


 req_id: reqId,


 email_hash: emailHash,


 phone_hash: phoneHash,


 identity_hash: identityHash,

 });


ensureHeaders_(shPending, WEBAPP_CFG.PENDING_REQUIRED_HEADERS.concat(WEBAPP_CFG.PENDING_EXTRA_HEADERS));


// 1) Pending déjà actif (email OU tel)

 const activePending = findActivePending_(shPending, clean.email_norm, clean.phone_e164, WEBAPP_CFG.PENDING_ACTIVE_WINDOW_HOURS);

 if (activePending) {


 log_(shLog, "PENDING_ALREADY_ACTIVE", {



 req_id: reqId,



 email_hash: emailHash,



 phone_hash: phoneHash,



 pending_id: activePending.pending_id,



 status: activePending.status,


 });


 return {



 ok: false,



 code: "PENDING_ALREADY_ACTIVE",



 message: "Une activation est déjà en cours pour ces coordonnées. Réponds au SMS avec le code reçu.",


 };

 }


// 2) Match client existant

 const match = findExistingClient_(shClients, clean.email_norm, clean.phone_e164, clean.identity_key);


// --- LOG: match (sans PII)

 if (match && match.client_id) {


 log_(shLog, "MATCH_FOUND", {



 req_id: reqId,



 email_hash: emailHash,



 phone_hash: phoneHash,



 matched_by: match.matched_by,



 matched_client_id: match.client_id,


 });

 } else {


 log_(shLog, "NO_MATCH", { req_id: reqId, email_hash: emailHash, phone_hash: phoneHash });

 }


// 3) Politique update sans backend: refuser double changement email+tel

 let updatePolicy = "NEW_OR_UPDATE_OK";

 if (match && match.client_id) {


 const oldEmail = normEmail_(match.old_email);


 const oldPhone = normPhoneE164Loose_(match.old_phone_e164);


 const newEmail = clean.email_norm;


 const newPhone = clean.phone_e164;



const emailChanged = oldEmail && newEmail && oldEmail !== newEmail;


 const phoneChanged = oldPhone && newPhone && oldPhone !== newPhone;



if (emailChanged && phoneChanged) {



 log_(shLog, "REFUSE_DOUBLE_CHANGE", {




 req_id: reqId,




 matched_client_id: match.client_id,




 matched_by: match.matched_by,



 });



 return {




 ok: false,




 code: "DOUBLE_CHANGE_NOT_ALLOWED",




 message:





 "Mise à jour impossible en une seule fois (email ET téléphone différents). " +





 "Procède en 2 étapes : (1) change d’abord l’email (téléphone identique) et confirme par SMS, " +





 "puis (2) reviens changer le téléphone (email identique).",



 };


 }



updatePolicy = emailChanged ? "UPDATE_EMAIL_ONLY" : phoneChanged ? "UPDATE_PHONE_ONLY" : "NO_CHANGE_OR_RECONFIRM";

 }


// 4) Cooldown

 const cache = CacheService.getScriptCache();

 const cooldownKey = "cooldown_" + sha256Hex_(clean.email_norm + "|" + clean.phone_e164 + "|" + clean.identity_key);


if (cache.get(cooldownKey)) {


 log_(shLog, "COOLDOWN", { req_id: reqId, email_hash: emailHash, phone_hash: phoneHash });


 return { ok: false, code: "COOLDOWN", message: "Une demande a déjà été faite récemment. Réessaie dans quelques minutes." };

 }


// 5) Quotas journaliers

 const dayKey = dayKey_();

 const phoneKey = `day_phone_${dayKey}_${sha256Hex_(clean.phone_e164)}`;

 const emailKey = `day_email_${dayKey}_${sha256Hex_(clean.email_norm)}`;


const phoneCount = incrDailyCounter_(phoneKey);

 const emailCount = incrDailyCounter_(emailKey);


log_(shLog, "RATE_LIMIT_COUNTERS", {


 req_id: reqId,


 email_hash: emailHash,


 phone_hash: phoneHash,


 phone_count: phoneCount,


 email_count: emailCount,

 });


if (phoneCount > WEBAPP_CFG.DAILY_LIMIT_PER_PHONE) {


 log_(shLog, "DAILY_LIMIT_PHONE", { req_id: reqId, phone_hash: phoneHash, count: phoneCount });


 return { ok: false, code: "DAILY_LIMIT_PHONE", message: "Trop de demandes aujourd’hui pour ce téléphone. Réessaie demain ou contacte le support." };

 }

 if (emailCount > WEBAPP_CFG.DAILY_LIMIT_PER_EMAIL) {


 log_(shLog, "DAILY_LIMIT_EMAIL", { req_id: reqId, email_hash: emailHash, count: emailCount });


 return { ok: false, code: "DAILY_LIMIT_EMAIL", message: "Trop de demandes aujourd’hui pour cet email. Réessaie demain ou contacte le support." };

 }


cache.put(cooldownKey, "1", WEBAPP_CFG.COOLDOWN_SECONDS);


// 6) Écriture INIT

 const pendingId = Utilities.getUuid();

 const nowIso = new Date().toISOString();


const headers = getHeaders_(shPending);

 const row = Array(headers.length).fill("");


setByHeader_(headers, row, "pending_id", pendingId);

 setByHeader_(headers, row, "client_name", clean.client_name);

 setByHeader_(headers, row, "client_mail", clean.email_norm);

 setByHeader_(headers, row, "client_real_phone", clean.phone_e164);

 setByHeader_(headers, row, "proxy_number", "");

 setByHeader_(headers, row, "otp", "");

 setByHeader_(headers, row, "status", "INIT");

 setByHeader_(headers, row, "created_at", nowIso);

 setByHeader_(headers, row, "verified_at", "");


setByHeader_(headers, row, "status_detail", "INIT via WebApp");

 setByHeader_(headers, row, "otp_requests_count", "1");

 setByHeader_(headers, row, "otp_last_requested_at", nowIso);

 setByHeader_(headers, row, "request_source", "WEBAPP");

 setByHeader_(headers, row, "first_name_norm", clean.first_norm);

 setByHeader_(headers, row, "last_name_norm", clean.last_norm);

 setByHeader_(headers, row, "identity_key", clean.identity_key);


if (match && match.client_id) {


 setByHeader_(headers, row, "matched_client_id", match.client_id);


 setByHeader_(headers, row, "matched_by", match.matched_by);


 setByHeader_(headers, row, "update_policy", updatePolicy);


 setByHeader_(headers, row, "status_detail", `INIT via WebApp (matched client_id=${match.client_id} by ${match.matched_by})`);

 } else {


 setByHeader_(headers, row, "matched_client_id", "");


 setByHeader_(headers, row, "matched_by", "");


 setByHeader_(headers, row, "update_policy", "NEW_CLIENT_FLOW");

 }


try {


 shPending.appendRow(row);

 } catch (e) {


 log_(shLog, "APPEND_PENDING_FAILED", { req_id: reqId, pending_id: pendingId, err: safeErr_(e) });


 throw e;

 }


log_(shLog, "PENDING_INIT_WRITTEN", { req_id: reqId, pending_id: pendingId });


// 7) Appel backend (force FR + national/local)

 const createPayload = {


 pending_id: pendingId,


 client_name: clean.client_name,


 client_mail: clean.email_norm,


 client_real_phone: clean.phone_e164,


 country_iso: WEBAPP_CFG.DEFAULT_COUNTRY_ISO || "FR",


 number_type: WEBAPP_CFG.DEFAULT_NUMBER_TYPE || "national",

 };


// LOG sans PII : on log uniquement les champs non sensibles + pending_id

 logConsole_("CALL_BACKEND_CREATE", {


 req_id: reqId,


 pending_id: pendingId,


 country_iso: createPayload.country_iso,


 number_type: createPayload.number_type,

 });


let resp;

 try {


 resp = callApi_("post", WEBAPP_CFG.CONFIRM_CREATE_PATH, createPayload);

 } catch (apiErr) {


 patchPending_(shPending, pendingId, {



 status: "FAILED",



 status_detail: "Backend /confirmations/create exception",


 });


 log_(shLog, "BACKEND_EXCEPTION", { req_id: reqId, pending_id: pendingId, err: safeErr_(apiErr) });


 return { ok: false, code: "OTP_SEND_FAILED", message: "Impossible d’envoyer le code. Réessaie plus tard." };

 }


// LOG réponse backend sans contenu sensible

 logConsole_("BACKEND_CREATE_RESP", {


 req_id: reqId,


 pending_id: pendingId,


 resp_type: typeof resp,


 resp_ok: resp && typeof resp.ok !== "undefined" ? !!resp.ok : "(no-ok-field)",

 });


if (resp && resp.ok === false) {


 patchPending_(shPending, pendingId, {



 status: "FAILED",



 status_detail: "Backend /confirmations/create returned ok=false",


 });


 log_(shLog, "BACKEND_FAIL", { req_id: reqId, pending_id: pendingId });


 return { ok: false, code: "OTP_SEND_FAILED", message: "Impossible d’envoyer le code. Réessaie plus tard." };

 }


patchPending_(shPending, pendingId, { status_detail: "OTP requested via backend" });

 log_(shLog, "OTP_REQUESTED", {


 req_id: reqId,


 pending_id: pendingId,


 matched_client_id: match?.client_id || "",


 number_type: createPayload.number_type,


 country_iso: createPayload.country_iso,

 });


logConsole_("REQ_SUCCESS", {


 req_id: reqId,


 pending_id: pendingId,


 ms: Date.now() - t0,

 });


return {


 ok: true,


 pending_id: pendingId,


 message: "Code envoyé par SMS. Réponds au SMS avec le code pour confirmer.",

 };
 } catch (err) {

 logConsole_("REQ_ERROR", {


 req_id: reqId,


 ms: Date.now() - t0,


 err: safeErr_(err),

 });

 console.log(`requestActivation ERROR: ${err && err.stack ? err.stack : err}`);

 return { ok: false, code: "ERROR", message: String(err.message || err) };
 } }
/* ===================== VALIDATION / NORMALISATION ===================== */
function validateAndNormalize_(p) {
 const first = String(p?.first_name || "").trim();
 const first2 = String(p?.first_name_confirm || "").trim();
 const last = String(p?.last_name || "").trim();
 const last2 = String(p?.last_name_confirm || "").trim();

const email = String(p?.email || "").trim();
 const email2 = String(p?.email_confirm || "").trim();
 const phone = String(p?.phone || "").trim();
 const phone2 = String(p?.phone_confirm || "").trim();

if (!first || !first2 || !last || !last2 || !email || !email2 || !phone || !phone2) {

 throw new Error("Tous les champs sont requis.");
 }

const first_norm = normFirstName_(first);
 const first2_norm = normFirstName_(first2);
 if (first_norm !== first2_norm) throw new Error("Les prénoms ne correspondent pas.");

const last_norm = normLastName_(last);
 const last2_norm = normLastName_(last2);
 if (last_norm !== last2_norm) throw new Error("Les noms ne correspondent pas.");

const email_norm = normEmail_(email);
 const email2_norm = normEmail_(email2);
 if (!isEmailValid_(email_norm) || !isEmailValid_(email2_norm)) throw new Error("Email invalide.");
 if (email_norm !== email2_norm) throw new Error("Les emails ne correspondent pas.");

const phone_e164 = normPhoneE164Strict_(phone);
 const phone2_e164 = normPhoneE164Strict_(phone2);
 if (!phone_e164 || !phone2_e164) throw new Error("Téléphone invalide. Format attendu: +33612345678.");
 if (phone_e164 !== phone2_e164) throw new Error("Les téléphones ne correspondent pas.");

const client_name = `${last_norm} ${first_norm}`.trim();
 const identity_key = sha256Hex_(last_norm + "|" + first_norm);

return { client_name, email_norm, phone_e164, first_norm, last_norm, identity_key }; }
function normEmail_(v) {
 return String(v || "").trim().toLowerCase(); }
function isEmailValid_(email) {
 return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email); }
function normPhoneE164Strict_(v) {
 const s = String(v || "").trim().replace(/\s+/g, "");
 if (!/^\+[1-9]\d{7,14}$/.test(s)) return "";
 return s; }
function normPhoneE164Loose_(v) {
 const s = String(v || "").trim().replace(/\s+/g, "");
 if (!s) return "";
 if (s.startsWith("+")) return s;
 if (/^[0-9]{8,15}$/.test(s)) return "+" + s;
 return s; }
function stripDiacritics_(s) {
 return String(s).normalize("NFD").replace(/[\u0300-\u036f]/g, ""); }
function normLastName_(v) {
 let s = stripDiacritics_(String(v || "").trim());
 s = s.replace(/[^A-Za-z\s'\-]/g, " ").replace(/\s+/g, " ").trim();
 return s.toUpperCase(); }
function normFirstName_(v) {
 let s = stripDiacritics_(String(v || "").trim());
 s = s.replace(/[^A-Za-z\s'\-]/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
 return s

 .split(" ")

 .map(part => part.split("-").map(p => (p ? p[0].toUpperCase() + p.slice(1) : "")).join("-"))

 .join(" "); }
/* ===================== CLIENT MATCHING ===================== */
function findExistingClient_(shClients, emailNorm, phoneE164, identityKey) {
 const data = shClients.getDataRange().getValues();
 if (data.length < 2) return null;

const headers = data[0].map(h => String(h || "").trim());
 const idxId = headers.indexOf("client_id");
 const idxMail = headers.indexOf("client_mail");
 const idxPhone = headers.indexOf("client_real_phone");
 const idxKey = headers.indexOf("identity_key");

if (idxId === -1) return null;

const phoneCmp = String(phoneE164 || "").replace("+", "");

// 1) match email / phone
 if (idxMail !== -1 || idxPhone !== -1) {

 for (let r = 1; r < data.length; r++) {


 const cid = String(data[r][idxId] || "").trim();


 if (!cid) continue;



const mail = idxMail !== -1 ? normEmail_(data[r][idxMail]) : "";


 const ph = idxPhone !== -1 ? String(data[r][idxPhone] || "").trim().replace(/\s+/g, "") : "";


 const phCmp = ph.startsWith("+") ? ph.slice(1) : ph;



if (mail && mail === emailNorm) {



 return { client_id: cid, matched_by: "email", old_email: mail, old_phone_e164: ph };


 }


 if (phCmp && phCmp === phoneCmp) {



 return { client_id: cid, matched_by: "phone", old_email: mail, old_phone_e164: ph };


 }

 }
 }

// 2) match identity_key if column exists
 if (WEBAPP_CFG.ALLOW_IDENTITY_MATCH_IF_AVAILABLE && idxKey !== -1) {

 for (let r = 1; r < data.length; r++) {


 const cid = String(data[r][idxId] || "").trim();


 if (!cid) continue;


 const key = String(data[r][idxKey] || "").trim();


 if (key && key === identityKey) {



 const mail = idxMail !== -1 ? normEmail_(data[r][idxMail]) : "";



 const ph = idxPhone !== -1 ? String(data[r][idxPhone] || "").trim().replace(/\s+/g, "") : "";



 return { client_id: cid, matched_by: "identity_key", old_email: mail, old_phone_e164: ph };


 }

 }
 }

return null; }
/* ===================== PENDING HELPERS ===================== */
function findActivePending_(shPending, emailNorm, phoneE164, windowHours) {
 const data = shPending.getDataRange().getValues();
 if (data.length < 2) return null;

const headers = data[0].map(h => String(h || "").trim());
 const idxId = headers.indexOf("pending_id");
 const idxMail = headers.indexOf("client_mail");
 const idxPhone = headers.indexOf("client_real_phone");
 const idxStatus = headers.indexOf("status");
 const idxCreated = headers.indexOf("created_at");

if (idxId === -1 || idxMail === -1 || idxPhone === -1 || idxStatus === -1) return null;

const phoneCmp = String(phoneE164 || "").replace("+", "");
 const threshold = Date.now() - windowHours * 3600 * 1000;

for (let r = 1; r < data.length; r++) {

 const st = String(data[r][idxStatus] || "").trim().toUpperCase();

 if (!["INIT", "PENDING", "VERIFIED"].includes(st)) continue;


const mail = normEmail_(data[r][idxMail]);

 const ph = String(data[r][idxPhone] || "").trim().replace(/\s+/g, "");

 const phCmp = ph.startsWith("+") ? ph.slice(1) : ph;


if (!(mail === emailNorm || phCmp === phoneCmp)) continue;


let createdTs = 0;

 const createdRaw = data[r][idxCreated];

 if (createdRaw instanceof Date) createdTs = createdRaw.getTime();

 else if (createdRaw) {


 const t = Date.parse(String(createdRaw));


 createdTs = isNaN(t) ? 0 : t;

 }

 if (createdTs && createdTs < threshold) continue;


return { pending_id: String(data[r][idxId] || "").trim(), status: st };
 }

return null; }
/* ===================== SHEETS CORE HELPERS ===================== */
function mustGetSheet_(ss, name) {
 const sh = ss.getSheetByName(name);
 if (!sh) throw new Error(`Onglet introuvable: ${name}`);
 return sh; }
function getOrCreateLogSheet_(ss, name) {
 let sh = ss.getSheetByName(name);
 if (!sh) {

 sh = ss.insertSheet(name);

 sh.getRange(1, 1, 1, 6).setValues([["ts", "event", "req_id", "pending_id", "client_hash", "extra_json"]]);
 }
 return sh; }
function getHeaders_(sh) {
 const lastCol = sh.getLastColumn();
 if (lastCol < 1) return [];
 return sh.getRange(1, 1, 1, lastCol).getValues()[0].map(h => String(h || "").trim()); }
function ensureHeaders_(sh, neededHeaders) {
 const headers = getHeaders_(sh);
 let col = headers.length;

neededHeaders.forEach(h => {

 if (!headers.includes(h)) {


 col += 1;


 sh.getRange(1, col).setValue(h);


 headers.push(h);

 }
 });

SpreadsheetApp.flush(); }
function setByHeader_(headers, row, headerName, value) {
 const idx = headers.indexOf(headerName);
 if (idx === -1) return;
 row[idx] = value; }
function patchPending_(shPending, pendingId, patch) {
 const data = shPending.getDataRange().getValues();
 if (data.length < 2) return;

const headers = data[0].map(h => String(h || "").trim());
 const idxId = headers.indexOf("pending_id");
 if (idxId === -1) return;

const headerToIndex = new Map(headers.map((h, i) => [h, i]));

for (let r = 1; r < data.length; r++) {

 if (String(data[r][idxId] || "").trim() !== pendingId) continue;


Object.keys(patch).forEach(k => {


 if (!headerToIndex.has(k)) {



 shPending.getRange(1, headers.length + 1).setValue(k);



 headers.push(k);



 headerToIndex.set(k, headers.length - 1);


 }


 data[r][headerToIndex.get(k)] = patch[k];

 });


const out = Array(headers.length).fill("");

 for (let c = 0; c < headers.length; c++) out[c] = data[r][c] ?? "";

 shPending.getRange(1, 1, 1, headers.length).setValues([headers]);

 shPending.getRange(r + 1, 1, 1, headers.length).setValues([out]);

 return;
 } }
/* ===================== RATE LIMIT (DAILY) ===================== */
function dayKey_() {
 const d = new Date();
 const y = d.getUTCFullYear();
 const m = String(d.getUTCMonth() + 1).padStart(2, "0");
 const da = String(d.getUTCDate()).padStart(2, "0");
 return `${y}${m}${da}`; }
function incrDailyCounter_(key) {
 const props = PropertiesService.getScriptProperties();
 const raw = props.getProperty(key);
 const n = raw ? parseInt(raw, 10) : 0;
 const next = isNaN(n) ? 1 : n + 1;
 props.setProperty(key, String(next));
 return next; }
/* ===================== LOGGING (SANS PII) ===================== */
function log_(shLog, event, data) {
 try {

 const ts = new Date().toISOString();

 const row = [


 ts,


 String(event || ""),


 String(data?.req_id || ""),


 String(data?.pending_id || ""),


 String(data?.email_hash || data?.phone_hash || data?.identity_hash || ""),


 JSON.stringify(data || {}),

 ];

 shLog.appendRow(row);
 } catch (e) {

 console.log("[WEBAPP_LOG] failed: " + safeErr_(e));
 } }
function logConsole_(event, data) {
 try {

 console.log(`[WEBAPP] ${event} ${JSON.stringify(data || {})}`);
 } catch (_) {

 console.log(`[WEBAPP] ${event}`);
 } }
function safeErr_(e) {
 try {

 if (!e) return "unknown";

 if (typeof e === "string") return e.slice(0, 300);

 const msg = e.message ? String(e.message) : String(e);

 return msg.slice(0, 300);
 } catch (_) {

 return "unknown";
 } }
/* ===================== UTILS ===================== */
function sha256Hex_(s) {
 const bytes = Utilities.computeDigest(

 Utilities.DigestAlgorithm.SHA_256,

 String(s || ""),

 Utilities.Charset.UTF_8
 );
 return bytes.map(b => (b < 0 ? b + 256 : b).toString(16).padStart(2, "0")).join(""); }
