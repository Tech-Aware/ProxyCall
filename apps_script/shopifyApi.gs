/**
* shopifyApi.gs
* Client API Shopify réutilisable avec retry, pagination, rate limiting
*/
const ShopifyApi = {


/**

* GET paginé avec retry automatique

* @param {string} endpoint - ex: "/customers.json"

* @param {Object} params - query params

* @param {function} onPage - callback(items[]) appelé par page

* @returns {{total: number, pages: number}}

*/
 getPaginated(endpoint, params = {}, onPage = null) {

 const { domain, token } = getShopifyCredentials_();

 const baseUrl = `https://${domain}/admin/api/${SHOPIFY_CONFIG.API_VERSION}`;




let url = this.buildUrl_(baseUrl + endpoint, { limit: 250, ...params });

 let total = 0;

 let pages = 0;




while (url) {


 const response = this.fetchWithRetry_(url, token);


 const data = JSON.parse(response.getContentText());






// Trouve la clé de données (customers, orders, products, etc.)


 const dataKey = Object.keys(data).find(k => Array.isArray(data[k]));


 const items = dataKey ? data[dataKey] : [];






total += items.length;


 pages++;






if (onPage && items.length > 0) {



 onPage(items);


 }






// Pagination Link header


 url = this.getNextPageUrl_(response);

 }




return { total, pages };
 },


/**

* GET simple (une seule requête)

*/
 get(endpoint, params = {}) {

 const { domain, token } = getShopifyCredentials_();

 const baseUrl = `https://${domain}/admin/api/${SHOPIFY_CONFIG.API_VERSION}`;

 const url = this.buildUrl_(baseUrl + endpoint, params);




const response = this.fetchWithRetry_(url, token);

 return JSON.parse(response.getContentText());
 },


/**

* Fetch avec retry exponentiel

*/
 fetchWithRetry_(url, token, attempt = 1) {

 try {


 const response = UrlFetchApp.fetch(url, {



 method: "GET",



 headers: {




 "X-Shopify-Access-Token": token,




 "Accept": "application/json",



 },



 muteHttpExceptions: true,


 });






const code = response.getResponseCode();






// Rate limited -> retry


 if (code === 429) {



 if (attempt >= SHOPIFY_CONFIG.MAX_RETRIES) {




 throw new ShopifyApiError("Rate limit exceeded after retries", 429, url);



 }



 const delay = SHOPIFY_CONFIG.RETRY_DELAY_MS * Math.pow(2, attempt - 1);



 ShopifyLogger.warn("API", "RATE_LIMITED", { attempt, delay_ms: delay });



 Utilities.sleep(delay);



 return this.fetchWithRetry_(url, token, attempt + 1);


 }






// Erreur serveur -> retry


 if (code >= 500 && attempt < SHOPIFY_CONFIG.MAX_RETRIES) {



 const delay = SHOPIFY_CONFIG.RETRY_DELAY_MS * Math.pow(2, attempt - 1);



 ShopifyLogger.warn("API", "SERVER_ERROR_RETRY", { code, attempt, delay_ms: delay });



 Utilities.sleep(delay);



 return this.fetchWithRetry_(url, token, attempt + 1);


 }






// Erreur client


 if (code >= 400) {



 const body = response.getContentText().slice(0, 200);



 throw new ShopifyApiError(`HTTP ${code}: ${body}`, code, url);


 }






return response;





} catch (e) {


 if (e instanceof ShopifyApiError) throw e;






// Erreur réseau -> retry


 if (attempt < SHOPIFY_CONFIG.MAX_RETRIES) {



 const delay = SHOPIFY_CONFIG.RETRY_DELAY_MS * Math.pow(2, attempt - 1);



 ShopifyLogger.warn("API", "NETWORK_ERROR_RETRY", { error: e.message, attempt });



 Utilities.sleep(delay);



 return this.fetchWithRetry_(url, token, attempt + 1);


 }






throw new ShopifyApiError(`Network error: ${e.message}`, null, url);

 }
 },


/**

* Construit URL avec query params

*/
 buildUrl_(base, params) {

 const query = Object.entries(params)


 .filter(([_, v]) => v !== null && v !== undefined && v !== "")


 .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)


 .join("&");

 return query ? `${base}?${query}` : base;
 },


/**

* Extrait l'URL de la page suivante du header Link

*/
 getNextPageUrl_(response) {

 const linkHeader = response.getHeaders()["Link"] || response.getHeaders()["link"] || "";

 const match = linkHeader.match(/<([^>]+)>;\s*rel="next"/);

 return match ? match[1] : null;
 }, };
