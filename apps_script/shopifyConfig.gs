/**
* shopifyConfig.gs
* Configuration centralisée pour toutes les synchros Shopify
*/
const SHOPIFY_CONFIG = {
 // Script Properties keys
 PROP_DOMAIN: "SHOPIFY_DOMAIN",
 PROP_TOKEN: "SHOPIFY_TOKEN",


// API
 API_VERSION: "2024-01",
 MAX_RETRIES: 3,
 RETRY_DELAY_MS: 1000,


// Sheets
 CLIENTS_SHEET: "Clients",
 LOG_SHEET: "SHOPIFY_LOGS",


// Colonnes Clients (index 1-based)
 COL_EMAIL: "client_mail",
 COL_EMAIL_IDX: 3,




 // Colonne C


COL_SHOPIFY_STATUS: "sub_in_shopi",
 COL_SHOPIFY_STATUS_IDX: 9,
// Colonne I


// Valeurs statut
 STATUS_INSCRIT: "Inscrit",
 STATUS_NON_INSCRIT: "Non inscrit",


// Rate limiting
 LOCK_TIMEOUT_MS: 30000, };
/**
* Récupère la config Shopify depuis Script Properties
*/ function getShopifyCredentials_() {
 const props = PropertiesService.getScriptProperties();
 const domain = (props.getProperty(SHOPIFY_CONFIG.PROP_DOMAIN) || "").trim();
 const token = (props.getProperty(SHOPIFY_CONFIG.PROP_TOKEN) || "").trim();


if (!domain) throw new ShopifyConfigError("SHOPIFY_DOMAIN non configuré");
 if (!token) throw new ShopifyConfigError("SHOPIFY_TOKEN non configuré");


return { domain, token }; }
/**
* Erreurs typées
*/ class ShopifyConfigError extends Error {
 constructor(message) {

 super(message);

 this.name = "ShopifyConfigError";
 } }
class ShopifyApiError extends Error {
 constructor(message, statusCode = null, endpoint = null) {

 super(message);

 this.name = "ShopifyApiError";

 this.statusCode = statusCode;

 this.endpoint = endpoint;
 } }
class ShopifySyncError extends Error {
 constructor(message, syncType = null) {

 super(message);

 this.name = "ShopifySyncError";

 this.syncType = syncType;
 } }
