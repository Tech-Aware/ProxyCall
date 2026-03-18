/**
* shopifySheetUtils.gs
* Utilitaires feuille partagés pour les synchros Shopify
*/
const ShopifySheetUtils = {


/**

* Récupère les headers d'une feuille

* @param {GoogleAppsScript.Spreadsheet.Sheet} sheet

* @returns {string[]}

*/
 getHeaders(sheet) {

 const lastCol = sheet.getLastColumn();

 if (lastCol < 1) return [];

 return sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(h => String(h || "").trim());
 },


/**

* Assure qu'une colonne existe (l'ajoute si absente)

* @param {GoogleAppsScript.Spreadsheet.Sheet} sheet

* @param {string[]} headers - sera muté si colonne ajoutée

* @param {string} colName

*/
 ensureColumn(sheet, headers, colName) {

 if (headers.includes(colName)) return;




const newIdx = headers.length + 1;

 sheet.getRange(1, newIdx).setValue(colName);

 headers.push(colName);

 SpreadsheetApp.flush();




ShopifyLogger.debug("SHEET", "COLUMN_CREATED", { column: colName, index: newIdx });
 },


/**

* Lit une ligne comme objet

* @param {GoogleAppsScript.Spreadsheet.Sheet} sheet

* @param {string[]} headers

* @param {number} rowIndex

* @returns {Object}

*/
 getRowAsObject(sheet, headers, rowIndex) {

 const values = sheet.getRange(rowIndex, 1, 1, headers.length).getValues()[0];

 const obj = {};

 headers.forEach((h, i) => { obj[h] = values[i]; });

 return obj;
 },


/**

* Écrit une valeur dans une cellule par nom de colonne

*/
 writeCell(sheet, headers, rowIndex, colName, value) {

 const idx = headers.indexOf(colName);

 if (idx === -1) return false;

 sheet.getRange(rowIndex, idx + 1).setValue(value);

 return true;
 }, };
