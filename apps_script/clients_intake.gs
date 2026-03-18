/**
 * clients_intake.gs (UPDATED)
 *
 * Workflow:
 * - Google Forms -> "Réponses au formulaire 1" (journal)
 * - Normalisation + validation (email + phone E.164)
 * - Upsert -> "CONFIRMATION_PENDING" (PAS d'écriture dans "Clients")
 * - Appel backend Render: POST /confirmations/create
 *   -> réserve proxy (TwilioPools) + écrit proxy_number/otp dans CONFIRMATION_PENDING + envoie SMS OTP
 * - Cascade OTP automatique : SMS → Appel vocal (1 min) → Email (2 min)
 *
 * IMPORTANT:
 * - Ce script n'a aucune incidence sur les clients existants tant que tu n'appelles pas /confirmations/create.
 * - Pour tester "live", il faut que le backend Render ait déjà /confirmations/create déployé.
 */

const CFG = {
  // Onglets
  FORM_RESPONSES_SHEET_NAME: "Réponses au formulaire 1",
  PENDING_SHEET_NAME: "CONFIRMATION_PENDING",

  // Questions Forms (intitulés EXACTS)
  FORM_COL_FIRSTNAME: "Prénom",
  FORM_COL_LASTNAME: "Nom",
  FORM_COL_EMAIL: "Email",
  FORM_COL_PHONE: "Téléphone",

  // Colonnes CONFIRMATION_PENDING (en-têtes EXACTS)
  PENDING_COL_ID: "pending_id",
  PENDING_COL_NAME: "client_name",
  PENDING_COL_MAIL: "client_mail",
  PENDING_COL_REAL_PHONE: "client_real_phone",
  PENDING_COL_PROXY: "proxy_number",
  PENDING_COL_OTP: "otp",
  PENDING_COL_STATUS: "status",
  PENDING_COL_CREATED_AT: "created_at",
  PENDING_COL_VERIFIED_AT: "verified_at",

  // Validation E.164
  PHONE_E164_REGEX: /^\+[1-9]\d{7,14}$/,

  // Write-back dans feuille réponses (audit/debug)
  WRITE_BACK_STATUS: true,
  STATUS_COL: "status",
  STATUS_DETAIL_COL: "status_detail",
  LINKED_CLIENT_ID_COL: "linked_client_id", // on y stocke le pending_id (pas un client_id)

  // Backend
  BACKEND_CREATE_CONFIRMATION_PATH: "/confirmations/create",
  DEFAULT_COUNTRY_ISO: "FR",          // si tu veux piloter côté backend
  DEFAULT_NUMBER_TYPE: "national",    // aligné proxyCall.gs (national -> local côté backend si besoin)
};

/**
 * Trigger Spreadsheet onFormSubmit (installable) - déclenché à chaque soumission Forms
 */
function onFormSubmit(e) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) {
    console.error("[ProxyCall][pending] Lock non acquis, abandon (soumission concurrente).");
    return;
  }

  try {
    if (!e || !e.range) {
      console.error("[ProxyCall][pending] Event onFormSubmit invalide (e.range manquant).");
      return;
    }

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const responsesSheet = ss.getSheetByName(CFG.FORM_RESPONSES_SHEET_NAME);
    const pendingSheet = ss.getSheetByName(CFG.PENDING_SHEET_NAME);

    if (!responsesSheet) throw new Error(`Onglet réponses introuvable: '${CFG.FORM_RESPONSES_SHEET_NAME}'`);
    if (!pendingSheet) throw new Error(`Onglet pending introuvable: '${CFG.PENDING_SHEET_NAME}'`);

    const rowIndex = e.range.getRow();
    if (rowIndex < 2) return; // ligne 1 = en-têtes

    // --- Feuille réponses : headers + colonnes statut ---
    const respHeaders = getHeaders_(responsesSheet);
    ensureColumns_(
      responsesSheet,
      respHeaders,
      CFG.WRITE_BACK_STATUS ? [CFG.STATUS_COL, CFG.STATUS_DETAIL_COL, CFG.LINKED_CLIENT_ID_COL] : []
    );

    const rowObj = getRowAsObject_(responsesSheet, respHeaders, rowIndex);

    const rawFirst = (rowObj[CFG.FORM_COL_FIRSTNAME] || "").toString();
    const rawLast  = (rowObj[CFG.FORM_COL_LASTNAME] || "").toString();
    const rawEmail = (rowObj[CFG.FORM_COL_EMAIL] || "").toString();
    const rawPhone = (rowObj[CFG.FORM_COL_PHONE] || "").toString();

    // --- Normalisation NOM/Prénom selon tes règles ---
    const firstName  = normalizeFirstName_(rawFirst);
    const lastName   = normalizeLastName_(rawLast);
    const clientName = cleanSpaces_(`${firstName} ${lastName}`);

    // --- Normalisation email ---
    const email = cleanSpaces_(rawEmail).toLowerCase();

    // --- Normalisation/validation téléphone ---
    const phoneE164 = normalizePhoneE164_(rawPhone);

    const validationError = validateInput_({ firstName, lastName, clientName, email, phoneE164 });
    if (validationError) {
      writeBack_(responsesSheet, respHeaders, rowIndex, "REJECTED", validationError, "");
      console.warn("[ProxyCall][pending] Rejet soumission:", validationError);
      return;
    }

    // --- CONFIRMATION_PENDING : vérifie colonnes requises ---
    const pendingHeaders = getHeaders_(pendingSheet);
    const requiredPendingCols = [
      CFG.PENDING_COL_ID,
      CFG.PENDING_COL_NAME,
      CFG.PENDING_COL_MAIL,
      CFG.PENDING_COL_REAL_PHONE,
      CFG.PENDING_COL_PROXY,
      CFG.PENDING_COL_OTP,
      CFG.PENDING_COL_STATUS,
      CFG.PENDING_COL_CREATED_AT,
      CFG.PENDING_COL_VERIFIED_AT,
    ];
    for (const col of requiredPendingCols) {
      if (pendingHeaders.indexOf(col) === -1) {
        throw new Error(`Colonne manquante dans CONFIRMATION_PENDING: '${col}' (ligne 1)`);
      }
    }

    // --- Dédup pending : email prioritaire, puis téléphone ---
    const pendingValues = pendingSheet.getDataRange().getValues(); // inclut header
    const match = findPending_(pendingValues, pendingHeaders, email, phoneE164);

    // Cas update pending existant : on ne re-réserve PAS automatiquement un nouveau proxy
    // (sinon risque de réserver plusieurs numéros si l'utilisateur re-soumet).
    // Pour un "re-send OTP" propre, il faudrait un endpoint dédié /confirmations/resend.
    if (match.found) {
      updatePending_(pendingSheet, pendingHeaders, match.rowIndex, {
        [CFG.PENDING_COL_NAME]: clientName,
        [CFG.PENDING_COL_MAIL]: email,
        [CFG.PENDING_COL_REAL_PHONE]: phoneE164,
        [CFG.PENDING_COL_STATUS]: "PENDING",
        // ne touche pas proxy_number / otp ici
        [CFG.PENDING_COL_VERIFIED_AT]: "",
      });

      writeBack_(responsesSheet, respHeaders, rowIndex, "PENDING_EXISTS", "Déjà en attente: répondez au SMS OTP reçu.", String(match.pendingId || ""));
      console.info(`[ProxyCall][pending] Pending déjà existant pending_id=${match.pendingId} row=${match.rowIndex}`);
      return;
    }

    // CREATE : nouveau pending_id
    const pendingId = generatePendingId_();
    const nowIso = new Date().toISOString();

    const newRowIndex = appendRowByHeaders_(pendingSheet, pendingHeaders, {
      [CFG.PENDING_COL_ID]: pendingId,
      [CFG.PENDING_COL_NAME]: clientName,
      [CFG.PENDING_COL_MAIL]: email,
      [CFG.PENDING_COL_REAL_PHONE]: phoneE164,
      [CFG.PENDING_COL_STATUS]: "PENDING",
      [CFG.PENDING_COL_CREATED_AT]: nowIso,
      [CFG.PENDING_COL_VERIFIED_AT]: "",
      [CFG.PENDING_COL_PROXY]: "",
      [CFG.PENDING_COL_OTP]: "",
    });

    // Appel backend: /confirmations/create
    // NOTE: callApi_ est dans proxyCall.gs et utilise PUBLIC_BASE_URL + PROXYCALL_API_TOKEN
    const payload = {
      pending_id: pendingId,
      client_name: clientName,
      client_mail: email,
      client_real_phone: phoneE164,
      country_iso: CFG.DEFAULT_COUNTRY_ISO,
      number_type: CFG.DEFAULT_NUMBER_TYPE,
    };

    try {
      const res = callApi_("post", CFG.BACKEND_CREATE_CONFIRMATION_PATH, payload, null);
      writeBack_(responsesSheet, respHeaders, rowIndex, "PENDING_CREATED", "OTP envoyé par SMS.", pendingId);
      console.info(`[ProxyCall][pending] Pending créé row=${newRowIndex} pending_id=${pendingId} backend_res=${JSON.stringify(res || {})}`);

      // Cascade OTP automatique : SMS (déjà envoyé) → Appel vocal (1 min) → Email (2 min)
      cascadeOtpConfirmation(pendingId);

    } catch (apiErr) {
      // La ligne pending existe, mais OTP/proxy non renseignés (backend a échoué)
      writeBack_(responsesSheet, respHeaders, rowIndex, "PENDING_CREATED_API_FAILED", normalizeErr_(apiErr), pendingId);
      console.error("[ProxyCall][pending] Backend create_confirmation failed:", apiErr);
      // on ne throw pas pour éviter de faire échouer le trigger (la data est déjà en pending)
      return;
    }

  } catch (err) {
    console.error("[ProxyCall][pending] Erreur onFormSubmit:", err && err.stack ? err.stack : String(err));
    throw err;
  } finally {
    lock.releaseLock();
  }
}

/**
 * A exécuter UNE FOIS pour créer le trigger installable.
 */
function installTrigger() {
  const ss = SpreadsheetApp.getActive();
  ScriptApp.newTrigger("onFormSubmit")
    .forSpreadsheet(ss)
    .onFormSubmit()
    .create();

  console.info("[ProxyCall][pending] Trigger onFormSubmit installé.");
}

/* =========================
   Cascade OTP : SMS → Appel vocal (1 min) → Email (2 min)
   ========================= */

/**
 * Cascade automatique après l'envoi initial par SMS.
 * Vérifie le statut et relance par appel vocal puis par email si toujours PENDING.
 *
 * Délais : 1 min (voice) + 2 min (email) = 3 min total (sous la limite Apps Script de 6 min).
 *
 * @param {string} pendingId - Le pending_id créé par /confirmations/create
 */
function cascadeOtpConfirmation(pendingId) {
  var TOKEN = PropertiesService.getScriptProperties().getProperty("PROXYCALL_API_TOKEN");
  var headers = TOKEN ? {"Authorization": "Bearer " + TOKEN} : {};

  // 1) Attendre 1 min puis vérifier si le client a confirmé par SMS
  Utilities.sleep(60 * 1000);
  var status1 = _getConfirmationStatus(pendingId, headers);
  if (status1 !== "PENDING") {
    console.info("[ProxyCall][cascade] Arrêt après SMS: status=" + status1 + " pending_id=" + pendingId);
    return status1;
  }

  // 2) Pas de réponse SMS → relancer par appel vocal
  console.info("[ProxyCall][cascade] SMS sans réponse, relance par appel vocal: " + pendingId);
  _resendOtp(pendingId, "voice", headers);

  // 3) Attendre 2 min puis vérifier
  Utilities.sleep(120 * 1000);
  var status2 = _getConfirmationStatus(pendingId, headers);
  if (status2 !== "PENDING") {
    console.info("[ProxyCall][cascade] Arrêt après appel: status=" + status2 + " pending_id=" + pendingId);
    return status2;
  }

  // 4) Pas de réponse vocale → relancer par email
  console.info("[ProxyCall][cascade] Appel sans réponse, relance par email: " + pendingId);
  _resendOtp(pendingId, "email", headers);
  return "email_sent";
}


function _getConfirmationStatus(pendingId, headers) {
  try {
    var res = callApi_("get", "/confirmations/status?pending_id=" + encodeURIComponent(pendingId), null, null);
    return (res && res.status) ? String(res.status).trim() : "UNKNOWN";
  } catch (e) {
    console.error("[ProxyCall][cascade] Erreur status check: " + normalizeErr_(e));
    return "ERROR";
  }
}


function _resendOtp(pendingId, channel, headers) {
  try {
    var res = callApi_("post", "/confirmations/resend", { pending_id: pendingId, channel: channel }, null);
    console.info("[ProxyCall][cascade] Resend " + channel + " OK pending_id=" + pendingId);
  } catch (e) {
    console.error("[ProxyCall][cascade] Resend " + channel + " FAILED: " + normalizeErr_(e));
  }
}


/* =========================
   Pending helpers
   ========================= */

function generatePendingId_() {
  // Format lisible + unique
  return "P-" + Utilities.getUuid();
}

function findPending_(pendingValues, headers, email, phoneE164) {
  const idxId = headers.indexOf(CFG.PENDING_COL_ID);
  const idxMail = headers.indexOf(CFG.PENDING_COL_MAIL);
  const idxPhone = headers.indexOf(CFG.PENDING_COL_REAL_PHONE);

  for (let r = 1; r < pendingValues.length; r++) {
    const row = pendingValues[r];
    const rowEmail = (row[idxMail] || "").toString().trim().toLowerCase();
    const rowPhone = (row[idxPhone] || "").toString().trim();

    if (rowEmail && rowEmail === email) {
      return { found: true, rowIndex: r + 1, pendingId: String(row[idxId] || "") };
    }
    if (rowPhone && rowPhone === phoneE164) {
      return { found: true, rowIndex: r + 1, pendingId: String(row[idxId] || "") };
    }
  }
  return { found: false };
}

function updatePending_(sheet, headers, rowIndex, fieldsToUpdate) {
  Object.keys(fieldsToUpdate).forEach(colName => {
    const idx = headers.indexOf(colName);
    if (idx === -1) return;
    sheet.getRange(rowIndex, idx + 1).setValue(fieldsToUpdate[colName]);
  });
}

function appendRowByHeaders_(sheet, headers, payload) {
  const row = headers.map(h => (payload[h] !== undefined ? payload[h] : ""));
  sheet.appendRow(row);
  return sheet.getLastRow();
}

/* =========================
   Validation / Normalisation
   ========================= */

function validateInput_({ firstName, lastName, clientName, email, phoneE164 }) {
  if (!firstName) return "Prénom invalide ou vide.";
  if (!lastName) return "Nom invalide ou vide.";
  if (!clientName) return "Nom complet invalide.";
  if (!email || email.indexOf("@") === -1) return "Email invalide.";
  if (!phoneE164 || !CFG.PHONE_E164_REGEX.test(phoneE164)) return "Téléphone invalide (attendu format +indicatif...).";
  return "";
}

function normalizePhoneE164_(rawPhone) {
  let p = (rawPhone || "").toString().trim();
  p = p.replace(/[^\d+]/g, "");
  if (p.startsWith("00")) p = "+" + p.substring(2);
  if (!p.startsWith("+")) return "";
  p = "+" + p.substring(1).replace(/[^\d]/g, "");
  return CFG.PHONE_E164_REGEX.test(p) ? p : "";
}

function stripAccents_(s) {
  return (s || "")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

function cleanSpaces_(s) {
  return (s || "")
    .toString()
    .replace(/\s+/g, " ")
    .trim();
}

function keepLettersAndSpaces_(s) {
  const noAcc = stripAccents_(s);
  return cleanSpaces_(noAcc.replace(/[^A-Za-z]+/g, " "));
}

function normalizeLastName_(rawLastName) {
  const cleaned = keepLettersAndSpaces_(rawLastName);
  return cleaned.toUpperCase();
}

function normalizeFirstName_(rawFirstName) {
  const cleaned = keepLettersAndSpaces_(rawFirstName);
  return cleaned
    .split(" ")
    .filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/* =========================
   Write-back réponses (audit)
   ========================= */

function writeBack_(sheet, headers, rowIndex, status, detail, linkedId) {
  if (!CFG.WRITE_BACK_STATUS) return;

  const updates = {};
  updates[CFG.STATUS_COL] = status;
  updates[CFG.STATUS_DETAIL_COL] = detail || "";
  updates[CFG.LINKED_CLIENT_ID_COL] = linkedId || "";

  Object.keys(updates).forEach(key => {
    const colIdx = headers.indexOf(key);
    if (colIdx === -1) return;
    sheet.getRange(rowIndex, colIdx + 1).setValue(updates[key]);
  });
}

/* =========================
   Sheet helpers
   ========================= */

function getHeaders_(sheet) {
  const lastCol = sheet.getLastColumn();
  if (lastCol < 1) return [];
  return sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(h => String(h || "").trim());
}

function ensureColumns_(sheet, headers, neededCols) {
  if (!neededCols || neededCols.length === 0) return;

  let changed = false;
  neededCols.forEach(col => {
    if (headers.indexOf(col) === -1) {
      sheet.getRange(1, headers.length + 1).setValue(col);
      headers.push(col);
      changed = true;
    }
  });

  if (changed) SpreadsheetApp.flush();
}

function getRowAsObject_(sheet, headers, rowIndex) {
  const values = sheet.getRange(rowIndex, 1, 1, headers.length).getValues()[0];
  const obj = {};
  headers.forEach((h, i) => obj[h] = values[i]);
  return obj;
}

function normalizeErr_(e) {
  if (!e) return "Erreur inconnue";
  if (typeof e === "string") return e;
  if (e.message) return e.message;
  return String(e);
}
