/**
 * ProxyCall – Google Apps Script pour le Google Sheet TwilioPools.
 *
 * Ajoute un menu "ProxyCall" avec l'option de libérer les proxys en erreur.
 *
 * Colonnes TwilioPools :
 *   A: country_iso  B: phone_number  C: status  D: friendly_name
 *   E: date_achat   F: date_attribution  G: attribution_to_client_name
 *   H: number_type  I: reserved_token  J: reserved_at  K: reserved_by_client_id
 */

// ─── Menu ────────────────────────────────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("ProxyCall")
    .addItem("Libérer les proxys en erreur", "releaseErrorProxies")
    .addToUi();
}

// ─── Libération des proxys "error" ──────────────────────────────────────────

/**
 * Parcourt la feuille TwilioPools, remet à "available" toutes les lignes
 * dont la colonne C (status) vaut "error" (insensible à la casse),
 * et efface les champs d'attribution/réservation associés.
 */
function releaseErrorProxies() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("TwilioPools");

  if (!sheet) {
    SpreadsheetApp.getUi().alert("Feuille « TwilioPools » introuvable.");
    return;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("Aucune donnée dans TwilioPools.");
    return;
  }

  // Lire toutes les données d'un coup (lignes 2..lastRow, colonnes A..K)
  var dataRange = sheet.getRange(2, 1, lastRow - 1, 11);
  var values = dataRange.getValues();

  var released = 0;

  for (var i = 0; i < values.length; i++) {
    var status = String(values[i][2]).trim().toLowerCase(); // col C (index 2)

    if (status !== "error") continue;

    var rowNum = i + 2; // décalage header

    // C → available
    sheet.getRange(rowNum, 3).setValue("available");
    // F (date_attribution) → vide
    sheet.getRange(rowNum, 6).setValue("");
    // G (attribution_to_client_name) → vide
    sheet.getRange(rowNum, 7).setValue("");
    // I (reserved_token) → vide
    sheet.getRange(rowNum, 9).setValue("");
    // J (reserved_at) → vide
    sheet.getRange(rowNum, 10).setValue("");
    // K (reserved_by_client_id) → vide
    sheet.getRange(rowNum, 11).setValue("");

    released++;
  }

  SpreadsheetApp.getUi().alert(
    released > 0
      ? released + " proxy(s) libéré(s) avec succès."
      : "Aucun proxy en erreur trouvé."
  );
}
