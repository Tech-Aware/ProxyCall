/**
* shopifyTriggers.gs
* Gestion centralisée des triggers Shopify
*/
const SHOPIFY_TRIGGERS = {
 CUSTOMER_SYNC: {

 handler: "syncShopifyCustomerStatus",

 intervalHours: 1,
 },
 // Futures synchros :
 // ORDER_SYNC: { handler: "syncShopifyOrders", intervalHours: 1 },
 // PRODUCT_SYNC: { handler: "syncShopifyProducts", intervalHours: 6 }, };
/**
* Installe tous les triggers Shopify
*/ function installAllShopifyTriggers() {
 Object.entries(SHOPIFY_TRIGGERS).forEach(([name, config]) => {

 installShopifyTrigger_(config.handler, config.intervalHours);

 ShopifyLogger.info("TRIGGERS", "INSTALLED", { trigger: name, handler: config.handler });
 }); }
/**
* Désinstalle tous les triggers Shopify
*/ function uninstallAllShopifyTriggers() {
 const handlers = Object.values(SHOPIFY_TRIGGERS).map(c => c.handler);


ScriptApp.getProjectTriggers().forEach(t => {

 if (handlers.includes(t.getHandlerFunction())) {


 ScriptApp.deleteTrigger(t);


 ShopifyLogger.info("TRIGGERS", "REMOVED", { handler: t.getHandlerFunction() });

 }
 }); }
/**
* Installe un trigger spécifique (remplace si existe)
*/ function installShopifyTrigger_(handlerName, intervalHours) {
 // Supprime l'ancien
 ScriptApp.getProjectTriggers().forEach(t => {

 if (t.getHandlerFunction() === handlerName) {


 ScriptApp.deleteTrigger(t);

 }
 });


// Crée le nouveau
 ScriptApp.newTrigger(handlerName)

 .timeBased()

 .everyHours(intervalHours)

 .create(); }
/**
* Liste les triggers Shopify actifs
*/ function listShopifyTriggers() {
 const handlers = Object.values(SHOPIFY_TRIGGERS).map(c => c.handler);


const active = ScriptApp.getProjectTriggers()

 .filter(t => handlers.includes(t.getHandlerFunction()))

 .map(t => ({


 handler: t.getHandlerFunction(),


 id: t.getUniqueId(),

 }));


console.log("[ShopifyTriggers] Active:", JSON.stringify(active, null, 2));
 return active; }
