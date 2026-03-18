/**
* weekly_report.gs (sans template literals)
* Rapport hebdomadaire HTML basé sur la feuille ProxyClients (A:E, dès ligne 3).
*
* Mises à jour intégrées:
* - Tables "Base clients" et "Activité (semaine)" avec nouveaux labels (clients approx + Shopify proxy)
* - Bloc "Informations semaine" redesigné (lisible en email): encart + cartes empilées
* - Shopify rendu explicite: "inscrits / non inscrits"
* - Introduction sobre + description + crédit automatisation
* - Correction du double "</style>"
*
* + Logging EMAIL_LOG:
* - Ajoute une ligne dans l'onglet EMAIL_LOG à chaque envoi (LIVE/TEST) ou erreur
* - En-têtes attendues: ts | status | context | ref_id | row_number | email | subject | error
*
* Changement trigger (robuste):
* - Trigger quotidien à ~23:50
* - Envoi uniquement si on est dimanche ET dans l’heure 23 (anti-dérive après minuit)
*
* + AJOUT DOUBLONS (A20:E20 ProxyClients):
* - Label attendu: "Doublons dans la feuille client"
* - Si B (nbre) > 0 => affiche D (sup) + E (comm) dans "Informations semaine"
* - Si B == 0 => affiche uniquement 0
*/
var WEEKLY_REPORT_CFG = {
 SHEET_NAME: "ProxyClients", // <-- Mets le nom exact : "ProxyClients" ou "Proxy client"
 START_ROW: 3,
 START_COL: 1,
 NUM_COLS: 5,

EMAIL_TO: "contact@resellervinted.com,savsupply@resellervinted.com,supply@resellervinted.com,kevinandreazza@gmail.com",

SUBJECT: "[ProxyCall] Rapport hebdomadaire",
 POOL_LOW_THRESHOLD: 10,

// Trigger: dimanche soir (robuste => marge avant minuit)
 TRIGGER_HOUR: 23,
 TRIGGER_NEAR_MINUTE: 50,

// EMAIL_LOG
 EMAIL_LOG_SHEET_NAME: "EMAIL_LOG",
 EMAIL_LOG_CONTEXT_LIVE: "WEEKLY_REPORT",
 EMAIL_LOG_CONTEXT_TEST: "WEEKLY_REPORT_TEST" };
var WEEKLY_REPORT_PROP_LAST_SENT = "WEEKLY_REPORT_LAST_SENT_YYYYWW";
function installWeeklyReportTrigger() {
 var triggers = ScriptApp.getProjectTriggers();
 for (var i = 0; i < triggers.length; i++) {

 if (triggers[i].getHandlerFunction() === "sendWeeklyReportGuarded") {


 ScriptApp.deleteTrigger(triggers[i]);

 }
 }

ScriptApp.newTrigger("sendWeeklyReportGuarded")

 .timeBased()

 .everyDays(1)

 .atHour(WEEKLY_REPORT_CFG.TRIGGER_HOUR)

 .nearMinute(WEEKLY_REPORT_CFG.TRIGGER_NEAR_MINUTE)

 .create();

console.log("[WeeklyReport] Trigger installé: envoi chaque dimanche ~23:50 (trigger quotidien + filtre dimanche + filtre heure 23 + garde-fou hebdo)"); }
function sendWeeklyReportGuarded() {
 var lock = LockService.getScriptLock();
 try {

 lock.waitLock(30000);


var now = new Date();

 var day = now.getDay();
 // 0=dimanche, 1=lundi

 var hour = now.getHours();// 0..23


// Garde-fous temps:

 // - dimanche uniquement

 // - uniquement entre 23:00 et 23:59 (anti-dérive après minuit)

 if (day !== 0) return;

 if (hour !== 23) return;


var yw = isoYearWeek(now);

 var key = String(yw.year) + pad2(yw.week);


var props = PropertiesService.getScriptProperties();

 var last = String(props.getProperty(WEEKLY_REPORT_PROP_LAST_SENT) || "");

 if (last === key) return;


sendWeeklyReportNow();


props.setProperty(WEEKLY_REPORT_PROP_LAST_SENT, key);
 } catch (e) {

 console.error("[WeeklyReport] Erreur sendWeeklyReportGuarded: " + safeErr(e));
 } finally {

 try { lock.releaseLock(); } catch (_) {}
 } }
function sendWeeklyReportNow() {
 var lock = LockService.getScriptLock();
 var cfg = WEEKLY_REPORT_CFG;

try {

 lock.waitLock(30000);


var payload = buildWeeklyReportPayload(cfg);


var html = buildWeeklyReportHtmlStructured(payload);

 var subject = cfg.SUBJECT;


MailApp.sendEmail({


 to: cfg.EMAIL_TO,


 subject: subject,


 htmlBody: html,


 body: "Votre client mail ne supporte pas le HTML."

 });


// LOG success

 logEmailToEmailLogSheet({


 ts: new Date(),


 status: "SENT",


 context: cfg.EMAIL_LOG_CONTEXT_LIVE,


 refId: "rows=" + payload.rows.length + ";pool=" + payload.poolAvailable + ";low=" + payload.poolIsLow + ";generatedAt=" + payload.generatedAtIso,


 rowNumber: "",


 email: cfg.EMAIL_TO,


 subject: subject,


 error: "",


 sheetName: cfg.EMAIL_LOG_SHEET_NAME

 });


console.log(


 "[WeeklyReport] Envoye a " + cfg.EMAIL_TO +


 " (rows=" + payload.rows.length +


 ", pool=" + payload.poolAvailable +


 ", low=" + payload.poolIsLow + ")"

 );
 } catch (e) {

 var errMsg = safeErr(e);

 console.error("[WeeklyReport] Erreur sendWeeklyReportNow: " + errMsg);


// LOG error

 try {


 logEmailToEmailLogSheet({



 ts: new Date(),



 status: "ERROR",



 context: cfg.EMAIL_LOG_CONTEXT_LIVE,



 refId: "sendWeeklyReportNow",



 rowNumber: "",



 email: cfg.EMAIL_TO,



 subject: cfg.SUBJECT,



 error: errMsg,



 sheetName: cfg.EMAIL_LOG_SHEET_NAME


 });

 } catch (logErr) {


 console.error("[WeeklyReport] Logging ERROR failed: " + safeErr(logErr));

 }


throw e;
 } finally {

 try { lock.releaseLock(); } catch (_) {}
 } }
function testWeeklyReportNowKevin() {
 var lock = LockService.getScriptLock();
 var cfg = WEEKLY_REPORT_CFG;

try {

 lock.waitLock(30000);


var TEST_TO = "kevinandreazza@gmail.com";


var payload = buildWeeklyReportPayload(cfg);

 payload.testMode = true;


var html = buildWeeklyReportHtmlStructured(payload);

 var subject = cfg.SUBJECT + " [TEST]";


MailApp.sendEmail({


 to: TEST_TO,


 subject: subject,


 htmlBody: html,


 body: "Votre client mail ne supporte pas le HTML."

 });


// LOG success (TEST)

 logEmailToEmailLogSheet({


 ts: new Date(),


 status: "SENT",


 context: cfg.EMAIL_LOG_CONTEXT_TEST,


 refId: "rows=" + payload.rows.length + ";pool=" + payload.poolAvailable + ";low=" + payload.poolIsLow + ";generatedAt=" + payload.generatedAtIso,


 rowNumber: "",


 email: TEST_TO,


 subject: subject,


 error: "",


 sheetName: cfg.EMAIL_LOG_SHEET_NAME

 });


console.log(


 "[WeeklyReport][TEST] Envoye a " + TEST_TO +


 " (rows=" + payload.rows.length +


 ", pool=" + payload.poolAvailable +


 ", low=" + payload.poolIsLow + ")"

 );
 } catch (e) {

 var errMsg = safeErr(e);

 console.error("[WeeklyReport][TEST] Erreur: " + errMsg);


// LOG error (TEST)

 try {


 logEmailToEmailLogSheet({



 ts: new Date(),



 status: "ERROR",



 context: cfg.EMAIL_LOG_CONTEXT_TEST,



 refId: "testWeeklyReportNowKevin",



 rowNumber: "",



 email: "kevinandreazza@gmail.com",



 subject: cfg.SUBJECT + " [TEST]",



 error: errMsg,



 sheetName: cfg.EMAIL_LOG_SHEET_NAME


 });

 } catch (logErr) {


 console.error("[WeeklyReport][TEST] Logging ERROR failed: " + safeErr(logErr));

 }


throw e;
 } finally {

 try { lock.releaseLock(); } catch (_) {}
 } }
/* =========================================================

EMAIL_LOG helper

========================================================= */
/**
* Ajoute une ligne dans EMAIL_LOG selon les en-têtes:
* ts | status | context | ref_id | row_number | email | subject | error
*
* IMPORTANT:
* - Cette fonction écrit dans le spreadsheet ACTIF (celui auquel le script est lié).
* - Si l'onglet EMAIL_LOG n'existe pas, le log est ignoré (avec console.error).
*/ function logEmailToEmailLogSheet(entry) {
 try {

 var ss = SpreadsheetApp.getActiveSpreadsheet();

 var sh = ss.getSheetByName(entry.sheetName || "EMAIL_LOG");

 if (!sh) {


 console.error("[EmailLog] Onglet EMAIL_LOG introuvable (log ignore)");


 return;

 }


sh.appendRow([


 entry.ts || new Date(),

 // ts (Date)


 entry.status || "",



 // status


 entry.context || "",



// context


 entry.refId || "",




// ref_id


 entry.rowNumber || "",


// row_number


 entry.email || "",




// email


 entry.subject || "",



// subject


 entry.error || ""




 // error

 ]);
 } catch (e) {

 console.error("[EmailLog] Erreur logEmailToEmailLogSheet: " + safeErr(e));
 } }
/* =========================================================

Core: lecture + normalisation + mapping

========================================================= */
function buildWeeklyReportPayload(cfg) {
 var ss = SpreadsheetApp.getActiveSpreadsheet();
 var sh = ss.getSheetByName(cfg.SHEET_NAME);
 if (!sh) throw new Error("[WeeklyReport] Onglet introuvable: " + cfg.SHEET_NAME);

var lastRow = sh.getLastRow();
 var lastCol = sh.getLastColumn();
 if (lastRow < cfg.START_ROW) throw new Error("[WeeklyReport] Aucune donnée (lastRow < START_ROW).");

var endCol = Math.min(cfg.START_COL + cfg.NUM_COLS - 1, lastCol);
 var numCols = endCol - cfg.START_COL + 1;
 var numRows = lastRow - cfg.START_ROW + 1;

var raw = sh.getRange(cfg.START_ROW, cfg.START_COL, numRows, numCols).getDisplayValues();

var rows = [];
 for (var i = 0; i < raw.length; i++) {

 var r = raw[i];

 var label = String((r[0] || "")).trim();

 if (!label) continue;


rows.push({


 label: label,


 nbre: String((r[1] || "")).trim(),


 pct:
String((r[2] || "")).trim(),


 sup:
String((r[3] || "")).trim(),


 comm: String((r[4] || "")).trim()

 });
 }

var data = mapByLabel(rows);

var poolLabel = "Nombre de numéro disponible dans le pool";
 var poolAvailable = parseFrNumber(data[poolLabel] ? data[poolLabel].nbre : "");
 var poolIsLow = (poolAvailable !== null) && (poolAvailable < cfg.POOL_LOW_THRESHOLD);

// Doublons (nouvelle ligne A20:E20)
 var dupLabel = "Doublons dans la feuille client";
 var dupRow = data[dupLabel] || null;

var duplicatesCount = parseFrNumber(dupRow ? dupRow.nbre : "");
 if (duplicatesCount == null) duplicatesCount = 0;

var duplicatesDetails = dupRow ? (dupRow.sup || "") : "";
 // colonne D
 var duplicatesComment = dupRow ? (dupRow.comm || "") : "";
// colonne E

// Conservé pour debug/usage futur (le HTML n'en dépend pas)
 var narrative = [

 buildDecisionSummary({


 data: data,


 poolAvailable: poolAvailable,


 poolThreshold: cfg.POOL_LOW_THRESHOLD,


 poolIsLow: poolIsLow,


 duplicatesCount: duplicatesCount

 })
 ];

return {

 spreadsheetName: ss.getName(),

 sheetName: cfg.SHEET_NAME,

 generatedAtIso: new Date().toISOString(),

 poolIsLow: poolIsLow,

 poolAvailable: poolAvailable,

 poolThreshold: cfg.POOL_LOW_THRESHOLD,

 rows: rows,

 data: data,

 narrative: narrative,

 testMode: false,


// Doublons

 duplicatesCount: duplicatesCount,

 duplicatesDetails: duplicatesDetails,

 duplicatesComment: duplicatesComment
 }; }
function mapByLabel(rows) {
 var out = {};
 for (var i = 0; i < rows.length; i++) {

 out[rows[i].label] = rows[i];
 }
 return out; }
function parseFrNumber(s) {
 var v = String(s || "").trim();
 if (!v) return null;

var cleaned = v.replace(/\s/g, "")

 .replace(",", ".")

 .replace(/[^0-9.\-+]/g, "");

if (!cleaned) return null;

var n = Number(cleaned);
 return isFinite(n) ? n : null; }
function buildDecisionSummary(ctx) {
 var d = ctx.data || {};
 var get = function (label) {

 return d[label] || { nbre: "", pct: "", sup: "" };
 };

var poolStatus = ctx.poolIsLow ? "action requise" : "niveau conforme";

return (

 "État du système : le pool dispose de " + ctx.poolAvailable +

 " numéros (seuil " + ctx.poolThreshold + "), " + poolStatus + " ; " +


"activité hebdomadaire : " +

 get("Nombre de proxy assigné cette semaine*").nbre + " proxies assignés (" +

 get("Nombre de proxy assigné cette semaine*").pct + "), " +

 get("Nombre de proxy assigné cette semaine*").sup +

 " par rapport à la semaine précédente, " +

 get("Nombre de commande cette semaine*").nbre +

 " commandes enregistrées (" +

 get("Nombre de commande cette semaine*").pct +

 " de l’objectif précédent) ; " +


"base clients : " +

 get("Nombre approximatif de client").nbre +

 " clients estimés, dont " +

 get("Nombre approximatif de client sans proxy").nbre +

 " sans proxy (" +

 get("Nombre approximatif de client sans proxy").pct +

 ") et " +

 get("Clients avec un proxy").nbre +

 " avec proxy (" +

 get("Clients avec un proxy").pct +

 "), répartis entre " +

 get("Clients avec un proxy inscrits sur Shopify").nbre +

 " inscrits Shopify (" +

 get("Clients avec un proxy inscrits sur Shopify").pct +

 ") et " +

 get("Clients avec un proxy non inscrits sur Shopify").nbre +

 " non inscrits (" +

 get("Clients avec un proxy non inscrits sur Shopify").pct +

 ") ; " +


"usage effectif : " +

 get("Client avec un proxy qui ont commandé cette semaine*").nbre +

 " clients avec proxy ont commandé cette semaine (" +

 get("Client avec un proxy qui ont commandé cette semaine*").pct +

 ") et " +

 get("Nombre de proxy actif (ayant au mois reçu un premier appel)").nbre +

 " proxies sont actifs (" +

 get("Nombre de proxy actif (ayant au mois reçu un premier appel)").pct +

 ")."
 ); }
/* =========================================================

HTML

========================================================= */
function buildWeeklyReportHtmlStructured(ctx) {
 var safe = function (s) { return htmlEscape(String(s == null ? "" : s)); };

var d = ctx.data || {};
 var get = function (label) {

 return d[label] || { label: label, nbre: "", pct: "", sup: "", comm: "" };
 };

// Tables conservées (simplifiées)
 var kpiRows = [

 get("Nombre approximatif de client"),

 get("Nombre approximatif de client sans proxy"),

 get("Clients avec un proxy"),

 get("Clients avec un proxy inscrits sur Shopify"),

 get("Clients avec un proxy non inscrits sur Shopify")
 ];

var flowRows = [

 get("Nombre de proxy assigné la semaine* dernière"),

 get("Nombre de proxy assigné cette semaine*"),

 get("Nombre de commande la semaine dernière*"),

 get("Nombre de commande cette semaine*"),

 get("Client avec un proxy qui ont commandé cette semaine*"),

 get("Nombre de proxy actif (ayant au mois reçu un premier appel)")
 ];

// Bannière pool
 var poolBanner = "";
 if (ctx.poolIsLow) {

 poolBanner =


 '<div class="banner danger"><b>ALERTE POOL :</b> ' +


 safe(ctx.poolAvailable) +


 " numéro(s) disponible(s) (seuil: " +


 safe(ctx.poolThreshold) +


 ").</div>";
 } else {

 poolBanner =


 '<div class="banner ok">Pool OK : ' +


 safe(ctx.poolAvailable == null ? "" : ctx.poolAvailable) +


 " numéro(s) disponible(s) (seuil: " +


 safe(ctx.poolThreshold) +


 ").</div>";
 }

var testBanner = ctx.testMode

 ? '<div class="banner test"><b>MODE TEST :</b> email envoyé manuellement (simulation run hebdo).</div>'

 : "";

var introHtml = [

 '<div class="intro">',


 "<p><b>Bonjour,</b></p>",


 "<p>Ce mail contient le rapport hebdomadaire ProxyCall : synthèse des indicateurs clés (pool, activité, base clients, usage) et tableaux de suivi.</p>",


 "<p class=\"small muted\">Généré automatiquement le " + safe(ctx.generatedAtIso) +



 " depuis la feuille <b>" + safe(ctx.sheetName) + "</b> (" + safe(ctx.spreadsheetName) +



 "). Automatisation développée par Kévin Andreazza - Développeur Full stack</p>",

 "</div>"
 ].join("");

// Informations semaine (design lisible en email)
 var weekInfoHtml = buildWeekInfoPrettyHtml(ctx);

function renderTableSimple(title, rows) {

 var body = [];

 for (var i = 0; i < rows.length; i++) {


 var r = rows[i];


 body.push(



 "<tr>" +




 '<td class="label">' + safe(r.label) + "</td>" +




 '<td class="num">' + safe(r.nbre) + "</td>" +




 '<td class="num">' + safe(r.pct) + "</td>" +



 "</tr>"


 );

 }


return [


 "<h2>", safe(title), "</h2>",


 "<table>",


 "<thead><tr>",


 "<th>Indicateur</th><th>Nbre</th><th>%</th>",


 "</tr></thead>",


 "<tbody>", body.join(""), "</tbody>",


 "</table>"

 ].join("");
 }

return [ "<!doctype html>", "<html>", "<head>", '
<meta charset="utf-8">', "
<style>", "

body { font-family: Arial, sans-serif; font-size: 14px; color: #111; margin: 0; padding: 18px; }", "

.meta { margin-bottom: 12px; }", "

.small { color: #666; font-size: 12px; }", "

h1 { font-size: 18px; margin: 0 0 6px; }", "

h2 { font-size: 15px; margin: 18px 0 8px; }", "

.banner { border-radius: 10px; padding: 10px 12px; margin: 12px 0 16px; }", "

.banner.ok { background: #e8fff0; border: 1px solid #b8f0cc; }", "

.banner.danger { background: #ffe7ea; border: 1px solid #ffc3cb; }", "

.banner.test { background: #fff7db; border: 1px solid #ffe7a5; }", "

.intro { border: 1px solid #ddd; border-radius: 12px; padding: 12px; background: #fff; margin: 12px 0 16px; }", "

.intro p { margin: 0 0 8px; }", "

table { border-collapse: collapse; width: 100%; }", "

th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }", "

th { background: #f4f4f4; text-align: left; }", "

td.num { width: 110px; text-align: right; white-space: nowrap; }", "

td.label { width: 420px; }", "

.muted { color: #666; }",
// Styles "Informations semaine" (compatibles email) "

.wk { border: 1px solid #ddd; border-radius: 12px; padding: 12px; margin: 12px 0 16px; background: #fafafa; }", "

.wk-title { font-size: 15px; font-weight: bold; margin: 0 0 8px; }", "

.wk-sub { font-size: 12px; color: #666; margin: 0 0 10px; }", "

.wk-card { border: 1px solid #ddd; border-radius: 10px; background: #fff; padding: 10px 12px; margin: 10px 0; }", "

.wk-card h3 { font-size: 13px; margin: 0 0 8px; }", "

.wk-row { width: 100%; border-collapse: collapse; }", "

.wk-row td { border: 0; padding: 4px 0; vertical-align: top; }", "

.wk-k { color: #666; width: 240px; padding-right: 10px; }", "

.wk-v { text-align: right; white-space: nowrap; }", "

.wk-badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; border: 1px solid #ddd; background: #fff; }", "

.wk-badge.ok { border-color: #b8f0cc; background: #e8fff0; }", "

.wk-badge.danger { border-color: #ffc3cb; background: #ffe7ea; }",
"
</style>", "</head>", "<body>", '
<div class="meta">', "

<h1>Rapport hebdomadaire ProxyCall</h1>", '

<div class="small">Fichier: ' + safe(ctx.spreadsheetName) + " · Feuille: " + safe(ctx.sheetName) + " · Généré le: " + safe(ctx.generatedAtIso) + "</div>", '

<div class="small muted">* semaine civile du lundi au vendredi</div>', "
</div>", "", "
" + testBanner, "
" + poolBanner, "", introHtml, "", weekInfoHtml, "", renderTableSimple("Base clients", kpiRows), renderTableSimple("Activité (semaine)", flowRows), "", '
<p class="small">Ce rapport reflète l’état de la feuille au moment de l’envoi.</p>', "</body>", "</html>"
 ].join("\n"); }
function buildWeekInfoPrettyHtml(ctx) {
 var safe = function (s) { return htmlEscape(String(s == null ? "" : s)); };
 var d = ctx.data || {};
 var get = function (label) {

 return d[label] || { nbre: "", pct: "", sup: "" };
 };

var poolBadgeClass = ctx.poolIsLow ? "danger" : "ok";
 var poolBadgeText
= ctx.poolIsLow ? "Action requise" : "OK";

var assigned = get("Nombre de proxy assigné cette semaine*");
 var orders
 = get("Nombre de commande cette semaine*");

var total
 = get("Nombre approximatif de client");
 var noProxy = get("Nombre approximatif de client sans proxy");
 var withP
 = get("Clients avec un proxy");

var shopYes = get("Clients avec un proxy inscrits sur Shopify");
 var shopNo
= get("Clients avec un proxy non inscrits sur Shopify");

var ordered = get("Client avec un proxy qui ont commandé cette semaine*");
 var active
= get("Nombre de proxy actif (ayant au mois reçu un premier appel)");

// Doublons (issus du payload)
 var duplicatesCount = Number(ctx.duplicatesCount || 0);
 var duplicatesDetails = ctx.duplicatesDetails || "";
 var duplicatesComment = ctx.duplicatesComment || "";

var delta = assigned.sup ? safe(assigned.sup) : "";

return [

 '<div class="wk">',


 '<div class="wk-title">Informations semaine</div>',


 '<div class="wk-sub">',



 'Synthèse décisionnelle (indicateurs clés). ',



 '<span class="wk-badge ' + poolBadgeClass + '">Pool : ' +




 safe(ctx.poolAvailable == null ? "" : ctx.poolAvailable) + ' / seuil ' + safe(ctx.poolThreshold) +




 ' · ' + safe(poolBadgeText) +



 '</span>',


 '</div>',



'<div class="wk-card">',



 '<h3>Système</h3>',



 '<table class="wk-row">',




 '<tr><td class="wk-k">Numéros disponibles</td><td class="wk-v"><b>' + safe(ctx.poolAvailable == null ? "" : ctx.poolAvailable) + '</b></td></tr>',




 '<tr><td class="wk-k">Seuil</td><td class="wk-v">' + safe(ctx.poolThreshold) + '</td></tr>',




 '<tr><td class="wk-k">Statut</td><td class="wk-v"><b>' + safe(poolBadgeText) + '</b></td></tr>',



 '</table>',


 '</div>',



'<div class="wk-card">',



 '<h3>Activité</h3>',



 '<table class="wk-row">',




 '<tr><td class="wk-k">Proxies assignés (semaine)</td><td class="wk-v"><b>' + safe(assigned.nbre) + '</b> ' +





 '<span class="muted">(' + safe(assigned.pct) + ')</span>' +





 (delta ? '<span class="muted"> · Δ ' + delta + '</span>' : '') +




 '</td></tr>',




 '<tr><td class="wk-k">Commandes (semaine)</td><td class="wk-v"><b>' + safe(orders.nbre) + '</b> ' +





 (orders.pct ? '<span class="muted">(' + safe(orders.pct) + ')</span>' : '') +




 '</td></tr>',



 '</table>',


 '</div>',



'<div class="wk-card">',



 '<h3>Base clients</h3>',



 '<table class="wk-row">',




 '<tr><td class="wk-k">Clients estimés</td><td class="wk-v"><b>' + safe(total.nbre) + '</b> <span class="muted">(' + safe(total.pct) + ')</span></td></tr>',




 '<tr><td class="wk-k">Sans proxy</td><td class="wk-v"><b>' + safe(noProxy.nbre) + '</b> <span class="muted">(' + safe(noProxy.pct) + ')</span></td></tr>',




 '<tr><td class="wk-k">Avec proxy</td><td class="wk-v"><b>' + safe(withP.nbre) + '</b> <span class="muted">(' + safe(withP.pct) + ')</span></td></tr>',



 '</table>',


 '</div>',



'<div class="wk-card">',



 '<h3>Doublons (feuille Clients)</h3>',



 '<table class="wk-row">',




 '<tr><td class="wk-k">Nombre de doublons</td><td class="wk-v"><b>' + safe(duplicatesCount) + '</b></td></tr>',



 '</table>',



 (duplicatesCount > 0




 ? (






 '<div class="small muted" style="margin-top:8px;">' +







 '<b>Lignes en doublon :</b><br>' + htmlNl2Br(duplicatesDetails) +






 '</div>' +






 (duplicatesComment







 ? '<div class="small muted" style="margin-top:8px;"><b>Note :</b><br>' + htmlNl2Br(duplicatesComment) + '</div>'







 : ''






 )





 )




 : ''



 ),


 '</div>',



'<div class="wk-card">',



 '<h3>Adoption & usage</h3>',



 '<table class="wk-row">',




 '<tr><td class="wk-k">Shopify (clients avec proxy)</td><td class="wk-v">' +





 '<b>' + safe(shopYes.nbre) + '</b> <span class="muted">(' + safe(shopYes.pct) + ')</span> inscrits' +





 '<span class="muted"> / </span>' +





 '<b>' + safe(shopNo.nbre) + '</b> <span class="muted">(' + safe(shopNo.pct) + ')</span> non inscrits' +




 '</td></tr>',




 '<tr><td class="wk-k">Clients proxy ayant commandé</td><td class="wk-v"><b>' + safe(ordered.nbre) + '</b> <span class="muted">(' + safe(ordered.pct) + ')</span></td></tr>',




 '<tr><td class="wk-k">Proxies actifs</td><td class="wk-v"><b>' + safe(active.nbre) + '</b> <span class="muted">(' + safe(active.pct) + ')</span></td></tr>',



 '</table>',


 '</div>',


'</div>'
 ].join(""); }
function htmlEscape(s) {
 return String(s || "")

 .replace(/&/g, "&amp;")

 .replace(/</g, "&lt;")

 .replace(/>/g, "&gt;")

 .replace(/"/g, "&quot;")

 .replace(/'/g, "&#039;"); }
/**
* Convertit les retours à la ligne en <br> (texte multi-lignes pour email).
* IMPORTANT: échappe d'abord le HTML.
*/ function htmlNl2Br(s) {
 var v = String(s == null ? "" : s);
 return htmlEscape(v).replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n/g, "<br>"); }
/* =========================================================

ISO week helpers + utils

========================================================= */
function isoYearWeek(date) {
 var d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
 var dayNum = d.getUTCDay() || 7;
 d.setUTCDate(d.getUTCDate() + 4 - dayNum);
 var yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
 var weekNo = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
 return { year: d.getUTCFullYear(), week: weekNo }; }
function pad2(n) {
 var s = String(n);
 return s.length === 1 ? "0" + s : s; }
function safeErr(e) {
 try {

 if (!e) return "unknown";

 if (typeof e === "string") return e;

 if (e.message) return e.message;

 return String(e);
 } catch (_) {

 return "unknown";
 } }
