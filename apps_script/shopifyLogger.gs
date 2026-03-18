/**
* shopifyLogger.gs
* Journalisation fine et structurée pour les synchros Shopify
*/
const LOG_LEVEL = {
 DEBUG: 0,
 INFO: 1,
 WARN: 2,
 ERROR: 3, };
// Niveau minimum à logger (configurable) const SHOPIFY_LOG_LEVEL = LOG_LEVEL.DEBUG;
/**
* Logger Shopify
*/ const ShopifyLogger = {


/**

* @param {string} level

* @param {string} syncType - ex: "CUSTOMERS", "ORDERS", "PRODUCTS"

* @param {string} event

* @param {Object} data - données NON sensibles uniquement

*/
 log(level, syncType, event, data = {}) {

 if (LOG_LEVEL[level] < SHOPIFY_LOG_LEVEL) return;




const entry = {


 ts: new Date().toISOString(),


 level,


 sync: syncType,


 event,


 ...this.sanitize_(data),

 };




// Console (toujours)

 const msg = `[Shopify][${syncType}] ${event} ${JSON.stringify(entry)}`;

 switch (level) {


 case "ERROR": console.error(msg); break;


 case "WARN": console.warn(msg); break;


 default: console.log(msg);

 }




// Sheet log (async-safe)

 this.appendToSheet_(entry);
 },


debug(syncType, event, data) { this.log("DEBUG", syncType, event, data); },
 info(syncType, event, data) { this.log("INFO", syncType, event, data); },
 warn(syncType, event, data) { this.log("WARN", syncType, event, data); },
 error(syncType, event, data) { this.log("ERROR", syncType, event, data); },


/**

* Supprime les données sensibles

*/
 sanitize_(data) {

 const clean = { ...data };




// Hash les emails/phones si présents

 if (clean.email) clean.email = this.hash_(clean.email);

 if (clean.phone) clean.phone = this.hash_(clean.phone);

 if (clean.emails_count !== undefined) { /* ok, c'est un compteur */ }




// Tronque les messages d'erreur longs

 if (clean.error && clean.error.length > 300) {


 clean.error = clean.error.slice(0, 300) + "...";

 }




return clean;
 },


hash_(value) {

 if (!value) return "";

 const bytes = Utilities.computeDigest(


 Utilities.DigestAlgorithm.SHA_256,


 String(value).toLowerCase().trim(),


 Utilities.Charset.UTF_8

 );

 return bytes.slice(0, 6).map(b => ((b < 0 ? b + 256 : b).toString(16).padStart(2, "0"))).join("");
 },


/**

* Écrit dans l'onglet de logs (crée si absent)

*/
 appendToSheet_(entry) {

 try {


 const ss = SpreadsheetApp.getActiveSpreadsheet();


 let sheet = ss.getSheetByName(SHOPIFY_CONFIG.LOG_SHEET);






if (!sheet) {



 sheet = ss.insertSheet(SHOPIFY_CONFIG.LOG_SHEET);



 sheet.getRange(1, 1, 1, 6).setValues([["timestamp", "level", "sync_type", "event", "duration_ms", "details"]]);



 sheet.setFrozenRows(1);


 }






sheet.appendRow([



 entry.ts,



 entry.level,



 entry.sync,



 entry.event,



 entry.duration_ms || "",



 JSON.stringify(entry),


 ]);

 } catch (e) {


 console.error(`[ShopifyLogger] Sheet write failed: ${e.message}`);

 }
 }, };
