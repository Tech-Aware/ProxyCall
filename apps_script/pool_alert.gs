/**
* ProxyCall Pool Alert (Google Apps Script)
* - Lit le nombre de numéros disponibles en ProxyClients!B11
* - Alerte par email si < THRESHOLD
* - Cooldown (8h) pour éviter le spam
* - En test: email identique à prod + mention TEST, envoyé uniquement à Kevin
* - Log des emails envoyés dans l'onglet EMAIL_LOG avec les en-têtes:
*
 ts | status | context | ref_id | row_number | email | subject | error
*
* Contrainte: aucune fonction ne finit par underscore.
*/
function checkPoolAndAlert() {
 var CONFIG = getPoolAlertConfig();
 runPoolAlert({ mode: "LIVE", config: CONFIG }); }
function testPoolAlertEmailToKevin() {
 var CONFIG = getPoolAlertConfig();
 runPoolAlert({ mode: "TEST", config: CONFIG }); }
function installPoolAlertTrigger() {
 var triggers = ScriptApp.getProjectTriggers();
 for (var i = 0; i < triggers.length; i++) {

 if (triggers[i].getHandlerFunction() === "checkPoolAndAlert") {


 ScriptApp.deleteTrigger(triggers[i]);

 }
 }

ScriptApp.newTrigger("checkPoolAndAlert")

 .timeBased()

 .everyHours(1)

 .create();

console.log("[PoolAlert] Trigger installe: checkPoolAndAlert toutes les heures."); }
function resetPoolAlertState() {
 try {

 var props = PropertiesService.getScriptProperties();

 props.deleteProperty("POOL_ALERT_STATE");

 props.deleteProperty("POOL_ALERT_LAST_SENT_TS");

 props.deleteProperty("POOL_ALERT_LAST_VALUE");

 console.log("[PoolAlert] Etat alerte reset (properties supprimées).");
 } catch (err) {

 console.error("[PoolAlert] Erreur resetPoolAlertState: " + formatError(err));
 } }
function debugPoolAlertSheetAccess() {
 var CONFIG = getPoolAlertConfig();

try {

 var ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);

 var sheetNames = ss.getSheets().map(function(s) { return s.getName(); });

 console.log("[PoolAlert][Debug] Sheets disponibles: " + JSON.stringify(sheetNames));


var sh = ss.getSheetByName(CONFIG.SHEET_NAME);

 console.log("[PoolAlert][Debug] Sheet '" + CONFIG.SHEET_NAME + "' trouve ? " + Boolean(sh));


if (!sh) return;


var raw = sh.getRange(CONFIG.COUNT_CELL_A1).getDisplayValue();

 console.log("[PoolAlert][Debug] Valeur " + CONFIG.COUNT_CELL_A1 + " (display): '" + raw + "'");

 console.log("[PoolAlert][Debug] Valeur parse: " + parseNumber(raw));
 } catch (err) {

 console.error("[PoolAlert][Debug] Erreur debugPoolAlertSheetAccess: " + formatError(err));
 } }
/* =========================

Config

========================= */
function getPoolAlertConfig() {
 return {

 SPREADSHEET_ID: "1tlGN1H6suKPnNf7zSS-w7p1rQv2UTQT8xgVLln1B854",


SHEET_NAME: "ProxyClients",

 COUNT_CELL_A1: "B11",

 THRESHOLD: 10,


EMAIL_TO: "contact@resellervinted.com,savsupply@resellervinted.com,supply@resellervinted.com",

 TEST_EMAIL_TO: "kevinandreazza@gmail.com",


// Cooldown: 8 heures = 480 minutes

 COOLDOWN_MINUTES: 480,


SUBJECT: "[ProxyCall] Pool de numeros proxy a reapprovisionner",


// Logging

 EMAIL_LOG_SHEET_NAME: "EMAIL_LOG",

 EMAIL_LOG_CONTEXT_LIVE: "POOL_ALERT",

 EMAIL_LOG_CONTEXT_TEST: "POOL_ALERT_TEST"
 }; }
/* =========================

Core

========================= */
/**
* mode:
* - LIVE: cooldown actif + envoi sur EMAIL_TO si isLow
* - TEST: email identique + mention TEST, envoi uniquement sur TEST_EMAIL_TO, ignore seuil/cooldown
*/ function runPoolAlert(params) {
 var mode = params && params.mode ? String(params.mode).toUpperCase() : "LIVE";
 var CONFIG = params && params.config ? params.config : getPoolAlertConfig();

var props = PropertiesService.getScriptProperties();
 var lock = LockService.getScriptLock();

var now = new Date();
 var available = null;
 var rawCount = null;

try {

 lock.waitLock(20000);


var ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);

 var sheet = ss.getSheetByName(CONFIG.SHEET_NAME);

 if (!sheet) {


 console.error("[PoolAlert] Onglet introuvable: " + CONFIG.SHEET_NAME);


 return;

 }


rawCount = sheet.getRange(CONFIG.COUNT_CELL_A1).getDisplayValue();

 available = parseNumber(rawCount);

 if (available === null) {


 console.error('[PoolAlert] Valeur non numerique en ' + CONFIG.SHEET_NAME + "!" + CONFIG.COUNT_CELL_A1 + ': "' + rawCount + '"');


 return;

 }


var nowTs = now.getTime();

 var prevState = (props.getProperty("POOL_ALERT_STATE") || "UNKNOWN").toUpperCase();

 var lastSentTs = Number(props.getProperty("POOL_ALERT_LAST_SENT_TS") || "0");

 var isLow = available < CONFIG.THRESHOLD;


console.log(


 "[PoolAlert] mode=" + mode +


 " available=" + available +


 " threshold=" + CONFIG.THRESHOLD +


 " isLow=" + isLow +


 " prevState=" + prevState +


 " lastSentTs=" + lastSentTs

 );


if (mode === "LIVE") {


 if (!isLow) {



 props.setProperty("POOL_ALERT_STATE", "OK");



 props.setProperty("POOL_ALERT_LAST_VALUE", String(available));



 console.log("[PoolAlert] Etat OK. Pas d'envoi.");



 return;


 }



var cooldownMs = CONFIG.COOLDOWN_MINUTES * 60 * 1000;


 var inCooldown = lastSentTs > 0 && (nowTs - lastSentTs) < cooldownMs;



if (prevState === "LOW" && inCooldown) {



 var remainingMin = Math.ceil((cooldownMs - (nowTs - lastSentTs)) / 60000);



 console.log("[PoolAlert] En cooldown, pas d'envoi. minutes_restantes=" + remainingMin);



 return;


 }

 }


var subject = CONFIG.SUBJECT;

 var bodyPrefix = "";

 if (mode === "TEST") {


 subject = "[TEST] " + subject;


 bodyPrefix =



 "Ceci est un email de TEST. Le contenu ci-dessous est identique a l'email envoye en production.\n\n" +



 "------------------------------\n\n";

 }


var body =


 bodyPrefix +


 "Bonjour,\n\n" +


 "Nous vous informons que le nombre de numeros proxy au sein du pool present dans la feuille fichier commande mere " +


 "ne contient plus que " + available + " numero(s) restant(s).\n\n" +


 "Veuillez approvisionner le pool en consequence afin que vos clients continuent a souscrire un proxy.\n\n" +


 "Merci.";


var to = (mode === "TEST") ? CONFIG.TEST_EMAIL_TO : CONFIG.EMAIL_TO;


// Envoi

 MailApp.sendEmail(to, subject, body);


// Log EMAIL_LOG (succès)

 logEmailToSheet({


 spreadsheetId: CONFIG.SPREADSHEET_ID,


 logSheetName: CONFIG.EMAIL_LOG_SHEET_NAME,


 ts: now,


 status: "SENT",


 context: (mode === "TEST") ? CONFIG.EMAIL_LOG_CONTEXT_TEST : CONFIG.EMAIL_LOG_CONTEXT_LIVE,


 refId: "pool_cell=" + CONFIG.SHEET_NAME + "!" + CONFIG.COUNT_CELL_A1 + ";available=" + String(available),


 rowNumber: extractRowNumberFromA1(CONFIG.COUNT_CELL_A1),


 email: to,


 subject: subject,


 error: ""

 });


if (mode === "LIVE") {


 props.setProperty("POOL_ALERT_STATE", "LOW");


 props.setProperty("POOL_ALERT_LAST_SENT_TS", String(nowTs));


 props.setProperty("POOL_ALERT_LAST_VALUE", String(available));

 }


console.log("[PoolAlert] Email envoye. mode=" + mode + " to=" + to + " available=" + available);
 } catch (err) {

 var errMsg = formatError(err);

 console.error("[PoolAlert] Erreur runPoolAlert: " + errMsg);


// Log EMAIL_LOG (échec) — utile si MailApp/permissions/etc.

 try {


 var CONFIG2 = CONFIG || getPoolAlertConfig();


 logEmailToSheet({



 spreadsheetId: CONFIG2.SPREADSHEET_ID,



 logSheetName: CONFIG2.EMAIL_LOG_SHEET_NAME,



 ts: now,



 status: "ERROR",



 context: (mode === "TEST") ? CONFIG2.EMAIL_LOG_CONTEXT_TEST : CONFIG2.EMAIL_LOG_CONTEXT_LIVE,



 refId: "pool_cell=" + CONFIG2.SHEET_NAME + "!" + CONFIG2.COUNT_CELL_A1 + ";raw=" + String(rawCount),



 rowNumber: extractRowNumberFromA1(CONFIG2.COUNT_CELL_A1),



 email: (mode === "TEST") ? CONFIG2.TEST_EMAIL_TO : CONFIG2.EMAIL_TO,



 subject: (mode === "TEST") ? "[TEST] " + CONFIG2.SUBJECT : CONFIG2.SUBJECT,



 error: errMsg


 });

 } catch (logErr) {


 console.error("[PoolAlert] Impossible de logger dans EMAIL_LOG: " + formatError(logErr));

 }
 } finally {

 try { lock.releaseLock(); } catch (e) {}
 } }
/* =========================

Helpers

========================= */
function parseNumber(raw) {
 var s = String(raw).trim().replace(",", ".");
 var n = Number(s);
 return isFinite(n) ? n : null; }
function extractRowNumberFromA1(a1) {
 // ex: "B11" -> 11 ; "AA103" -> 103
 var m = String(a1).toUpperCase().match(/\d+/);
 return m ? Number(m[0]) : ""; }
function formatError(err) {
 if (!err) return "Unknown error";
 if (err && err.stack) return String(err.stack);
 return String(err); }
/**
* Ajoute une ligne dans EMAIL_LOG selon les en-têtes:
* ts | status | context | ref_id | row_number | email | subject | error
*
* Hypothèses:
* - EMAIL_LOG existe déjà avec ces colonnes (dans cet ordre).
* - Si l'onglet n'existe pas, on n'échoue pas: on log en console.
*/ function logEmailToSheet(entry) {
 var ss = SpreadsheetApp.openById(entry.spreadsheetId);
 var logSheet = ss.getSheetByName(entry.logSheetName);

if (!logSheet) {

 console.error("[PoolAlert] Onglet de log introuvable: " + entry.logSheetName + " (log ignore)");

 return;
 }

var row = [

 entry.ts,






// ts (Date)

 entry.status,




// status

 entry.context,



 // context

 entry.refId,




 // ref_id

 entry.rowNumber,


 // row_number

 entry.email,




 // email

 entry.subject,



 // subject

 entry.error





// error
 ];

logSheet.appendRow(row); }
