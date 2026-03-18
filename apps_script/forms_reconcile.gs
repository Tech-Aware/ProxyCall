/**
* ProxyCall — Reconciliation cron: Réponses au formulaire 1 -> statut final (PROMOTED)
* Objectif:
* - Si le client est présent dans "Clients" (match email OU téléphone) => status=PROMOTED
* - Sinon, si une ligne existe dans "CONFIRMATION_PENDING" => status=PENDING (ou laisse tel quel)
* - Sinon => ne touche pas (par défaut)
*
* A coller dans un nouveau fichier Apps Script: forms_reconcile.gs
*/
const RECONCILE_CFG = {
 RESPONSES_SHEET_NAME: "Réponses au formulaire 1",
 CLIENTS_SHEET_NAME: "Clients",
 PENDING_SHEET_NAME: "CONFIRMATION_PENDING",

// Entêtes exactes dans Réponses au formulaire 1
 RESP_EMAIL_COL: "Email",
 RESP_PHONE_COL: "Téléphone",

// Colonnes status (seront créées si absentes)
 STATUS_COL: "status",
 STATUS_DETAIL_COL: "status_detail",
 LINKED_CLIENT_ID_COL: "linked_client_id",

// Valeur finale voulue
 PROMOTED_VALUE: "PROMOTED",

// Pour éviter de casser tes array_formula: on NE TOUCHE PAS la ligne 1.
 // (On lit/écrit à partir de la ligne 2) };
function installReconcileTrigger_every5min() {
 // Supprime les triggers existants pour éviter doublons
 ScriptApp.getProjectTriggers()

 .filter(t => t.getHandlerFunction() === "reconcileFormResponsesStatus")

 .forEach(t => ScriptApp.deleteTrigger(t));

ScriptApp.newTrigger("reconcileFormResponsesStatus")

 .timeBased()

 .everyMinutes(5)

 .create(); }
function reconcileFormResponsesStatus() {
 const ss = SpreadsheetApp.getActiveSpreadsheet();

const shResp = ss.getSheetByName(RECONCILE_CFG.RESPONSES_SHEET_NAME);
 const shClients = ss.getSheetByName(RECONCILE_CFG.CLIENTS_SHEET_NAME);
 const shPending = ss.getSheetByName(RECONCILE_CFG.PENDING_SHEET_NAME);

if (!shResp) throw new Error(`Onglet introuvable: ${RECONCILE_CFG.RESPONSES_SHEET_NAME}`);
 if (!shClients) throw new Error(`Onglet introuvable: ${RECONCILE_CFG.CLIENTS_SHEET_NAME}`);
 if (!shPending) throw new Error(`Onglet introuvable: ${RECONCILE_CFG.PENDING_SHEET_NAME}`);

// Headers réponses + ensure colonnes status
 const respHeaders = getHeaders_(shResp);
 ensureColumns_(shResp, respHeaders, [

 RECONCILE_CFG.STATUS_COL,

 RECONCILE_CFG.STATUS_DETAIL_COL,

 RECONCILE_CFG.LINKED_CLIENT_ID_COL,
 ]);

const idxEmail = respHeaders.indexOf(RECONCILE_CFG.RESP_EMAIL_COL);
 const idxPhone = respHeaders.indexOf(RECONCILE_CFG.RESP_PHONE_COL);
 const idxStatus = respHeaders.indexOf(RECONCILE_CFG.STATUS_COL);
 const idxDetail = respHeaders.indexOf(RECONCILE_CFG.STATUS_DETAIL_COL);
 const idxLinked = respHeaders.indexOf(RECONCILE_CFG.LINKED_CLIENT_ID_COL);

if (idxEmail === -1) throw new Error(`Colonne manquante dans Réponses: ${RECONCILE_CFG.RESP_EMAIL_COL}`);
 if (idxPhone === -1) throw new Error(`Colonne manquante dans Réponses: ${RECONCILE_CFG.RESP_PHONE_COL}`);

// Build index Clients (email/phone -> {id})
 const clientsHeaders = getHeaders_(shClients);
 const cId = clientsHeaders.indexOf("client_id");
 const cMail = clientsHeaders.indexOf("client_mail");
 const cPhone = clientsHeaders.indexOf("client_real_phone");

if (cId === -1 || cMail === -1 || cPhone === -1) {

 throw new Error("Clients doit contenir au minimum: client_id, client_mail, client_real_phone");
 }

const clientsValues = shClients.getDataRange().getValues(); // incl header
 const clientsByEmail = new Map();
 const clientsByPhone = new Map();

for (let r = 1; r < clientsValues.length; r++) {

 const row = clientsValues[r];

 const id = String(row[cId] || "").trim();

 const email = normEmail_(row[cMail]);

 const phone = normPhoneCmp_(row[cPhone]);

 if (id) {


 if (email) clientsByEmail.set(email, id);


 if (phone) clientsByPhone.set(phone, id);

 }
 }

// Build index Pending (email/phone -> status)
 const pendingHeaders = getHeaders_(shPending);
 const pMail = pendingHeaders.indexOf("client_mail");
 const pPhone = pendingHeaders.indexOf("client_real_phone");
 const pStatus = pendingHeaders.indexOf("status");

if (pMail === -1 || pPhone === -1 || pStatus === -1) {

 throw new Error("CONFIRMATION_PENDING doit contenir: client_mail, client_real_phone, status");
 }

const pendingValues = shPending.getDataRange().getValues();
 const pendingByEmail = new Map(); // email -> status
 const pendingByPhone = new Map(); // phone -> status

for (let r = 1; r < pendingValues.length; r++) {

 const row = pendingValues[r];

 const email = normEmail_(row[pMail]);

 const phone = normPhoneCmp_(row[pPhone]);

 const st = String(row[pStatus] || "").trim().toUpperCase();

 if (email) pendingByEmail.set(email, st);

 if (phone) pendingByPhone.set(phone, st);
 }

const lastRow = shResp.getLastRow();
 const lastCol = shResp.getLastColumn();
 if (lastRow < 2) return;

// Read all response rows (2..lastRow)
 const rng = shResp.getRange(2, 1, lastRow - 1, lastCol);
 const values = rng.getValues();

let changed = false;

for (let i = 0; i < values.length; i++) {

 const row = values[i];


const email = normEmail_(row[idxEmail]);

 const phoneCmp = normPhoneCmp_(row[idxPhone]);


if (!email && !phoneCmp) continue;


const curStatus = String(row[idxStatus] || "").trim().toUpperCase();


// On évite de réécrire si déjà PROMOTED

 if (curStatus === RECONCILE_CFG.PROMOTED_VALUE) continue;


// 1) Si présent dans Clients => PROMOTED

 const clientId =


 (email && clientsByEmail.get(email)) ||


 (phoneCmp && clientsByPhone.get(phoneCmp)) ||


 "";


if (clientId) {


 row[idxStatus] = RECONCILE_CFG.PROMOTED_VALUE;


 row[idxDetail] = "Client confirmé et présent dans Clients.";


 row[idxLinked] = clientId;


 changed = true;


 continue;

 }


// 2) Sinon si pending existe => (optionnel) harmonise en PENDING

 const pendSt =


 (email && pendingByEmail.get(email)) ||


 (phoneCmp && pendingByPhone.get(phoneCmp)) ||


 "";


if (pendSt) {


 // On remet une info cohérente (sans forcer si tu veux garder ton message)


 row[idxStatus] = "PENDING";


 row[idxDetail] = `En attente de confirmation (${pendSt}).`;


 row[idxLinked] = row[idxLinked] || "";


 changed = true;


 continue;

 }


// 3) Sinon: ne touche pas (pour éviter faux positifs)
 }

if (changed) {

 rng.setValues(values);
 } }
/* ========================= helpers ========================= */
function getHeaders_(sheet) {
 const lastCol = sheet.getLastColumn();
 if (lastCol < 1) return [];
 return sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(h => String(h || "").trim()); }
function ensureColumns_(sheet, headers, neededCols) {
 if (!neededCols || neededCols.length === 0) return;

let changed = false;
 neededCols.forEach(col => {

 if (headers.indexOf(col) === -1) {


 sheet.getRange(1, headers.length + 1).setValue(col);


 headers.push(col);


 changed = true;

 }
 });

if (changed) SpreadsheetApp.flush(); }
function normEmail_(v) {
 const s = String(v || "").trim().toLowerCase();
 return s || ""; }
function normPhoneCmp_(v) {
 let s = String(v || "").trim();
 if (!s) return "";
 s = s.replace(/[^\d+]/g, ""); // garde + et chiffres
 if (s.startsWith("00")) s = "+" + s.slice(2);
 if (s.startsWith("+")) s = s.slice(1);
 return s; // comparateur sans "+" }
