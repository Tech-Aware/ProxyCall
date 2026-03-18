function cleanScriptProperties() {
 const KEEP = {

 SHOPIFY_TOKEN: true,

 SHOPIFY_DOMAIN: true,

 PUBLIC_BASE_URL: true,

 PROXYCALL_API_TOKEN: true,

 POOL_ALERT_LAST_SENT_TS: true,

 POOL_ALERT_STATE: true,


POOL_ALERT_LAST_VALUE: true,
 };

const props = PropertiesService.getScriptProperties();
 const all = props.getProperties(); // { key: value }

let deleted = 0;

for (const key in all) {

 if (!KEEP[key]) {


 props.deleteProperty(key);


 deleted++;

 }
 }

Logger.log(

 "Nettoyage terminé. Supprimées=%s, Conservées=%s (%s)",

 deleted,

 Object.keys(all).length - deleted,

 Object.keys(KEEP).join(", ")
 ); }
