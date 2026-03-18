/**
* shopifySyncCustomers.gs
* Synchronisation du statut d'inscription Shopify des clients
* Source: colonne C (client_mail) -> Destination: colonne I (sub_in_shopi)
*/
const SYNC_TYPE_CUSTOMERS = "CUSTOMERS";
/**
* Point d'entrée principal
*/ function syncShopifyCustomerStatus() {
 const runId = Utilities.getUuid().slice(0, 8);
 const t0 = Date.now();


ShopifyLogger.info(SYNC_TYPE_CUSTOMERS, "SYNC_START", { run_id: runId });


const lock = LockService.getScriptLock();
 if (!lock.tryLock(SHOPIFY_CONFIG.LOCK_TIMEOUT_MS)) {

 ShopifyLogger.warn(SYNC_TYPE_CUSTOMERS, "LOCK_FAILED", { run_id: runId });

 return { ok: false, error: "Lock non acquis" };
 }


try {

 const shopifyEmails = fetchAllShopifyEmails_(runId);

 const result = updateClientsShopifyStatus_(shopifyEmails, runId);




const duration = Date.now() - t0;

 ShopifyLogger.info(SYNC_TYPE_CUSTOMERS, "SYNC_COMPLETE", {


 run_id: runId,


 duration_ms: duration,


 shopify_count: shopifyEmails.size,


 ...result,

 });




return { ok: true, ...result, duration_ms: duration };



} catch (e) {

 const duration = Date.now() - t0;

 ShopifyLogger.error(SYNC_TYPE_CUSTOMERS, "SYNC_FAILED", {


 run_id: runId,


 duration_ms: duration,


 error: e.message,


 error_type: e.name,

 });

 return { ok: false, error: e.message, duration_ms: duration };



} finally {

 lock.releaseLock();
 } }
/**
* Récupère tous les emails clients Shopify
*/ function fetchAllShopifyEmails_(runId) {
 const emails = new Set();


const stats = ShopifyApi.getPaginated("/customers.json", {}, (customers) => {

 customers.forEach(c => {


 if (c.email) {



 emails.add(c.email.toLowerCase().trim());


 }

 });
 });


ShopifyLogger.debug(SYNC_TYPE_CUSTOMERS, "SHOPIFY_FETCH_DONE", {

 run_id: runId,

 emails_count: emails.size,

 pages: stats.pages,
 });


return emails; }
/**
* Met à jour la colonne I (sub_in_shopi) dans Clients
*/ function updateClientsShopifyStatus_(shopifyEmails, runId) {
 const ss = SpreadsheetApp.getActiveSpreadsheet();
 const sheet = ss.getSheetByName(SHOPIFY_CONFIG.CLIENTS_SHEET);


if (!sheet) {

 throw new ShopifySyncError(`Onglet introuvable: ${SHOPIFY_CONFIG.CLIENTS_SHEET}`, SYNC_TYPE_CUSTOMERS);
 }


const lastRow = sheet.getLastRow();
 if (lastRow < 2) {

 ShopifyLogger.debug(SYNC_TYPE_CUSTOMERS, "NO_DATA", { run_id: runId });

 return { total: 0, inscrit: 0, non_inscrit: 0, skipped: 0 };
 }


// Vérifie que le header colonne I est correct
 const headerI = sheet.getRange(1, SHOPIFY_CONFIG.COL_SHOPIFY_STATUS_IDX).getValue();
 if (String(headerI).trim() !== SHOPIFY_CONFIG.COL_SHOPIFY_STATUS) {

 ShopifyLogger.warn(SYNC_TYPE_CUSTOMERS, "HEADER_MISMATCH", {


 run_id: runId,


 expected: SHOPIFY_CONFIG.COL_SHOPIFY_STATUS,


 found: String(headerI).trim(),


 column: "I",

 });
 }


// Lecture colonne C (emails)
 const dataRows = lastRow - 1;
 const emails = sheet.getRange(2, SHOPIFY_CONFIG.COL_EMAIL_IDX, dataRows, 1).getValues();


let inscrit = 0, non_inscrit = 0, skipped = 0;


const statuses = emails.map((row, idx) => {

 const email = String(row[0] || "").toLowerCase().trim();




if (!email) {


 skipped++;


 return [""];

 }




if (shopifyEmails.has(email)) {


 inscrit++;


 return [SHOPIFY_CONFIG.STATUS_INSCRIT];

 } else {


 non_inscrit++;


 return [SHOPIFY_CONFIG.STATUS_NON_INSCRIT];

 }
 });


// Écriture batch colonne I
 sheet.getRange(2, SHOPIFY_CONFIG.COL_SHOPIFY_STATUS_IDX, statuses.length, 1).setValues(statuses);


ShopifyLogger.debug(SYNC_TYPE_CUSTOMERS, "WRITE_COMPLETE", {

 run_id: runId,

 rows_written: statuses.length,
 });


return { total: statuses.length, inscrit, non_inscrit, skipped }; }
