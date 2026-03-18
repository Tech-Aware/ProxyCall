/**
 * ProxyCall - Menu Sheets (Render)
 * Fonctions principales :
 * - Attribuer un proxy (ligne sélectionnée ou tout client sans proxy)
 * - Approvisionner le pool (achat) via une action explicite (comme la CLI)
 * - Lister les numéros disponibles, synchroniser le pool Twilio/Sheets, purger les numéros sans SMS, réparer les webhooks
 * - Rafraîchir une ligne client depuis Render (sans toucher aux champs protégés)
 *
 * Prérequis (Script properties) :
 * - PUBLIC_BASE_URL (ex: https://proxycall.onrender.com)
 * - PROXYCALL_API_TOKEN (Bearer)
 *
 * Feuille attendue :
 * - Onglet "Clients" avec les colonnes REQUIRED_HEADERS (ligne 1 = en-tête)
 *
 * IMPORTANT (alignement CLI) :
 * - Attribution client = POST /pool/assign -> PUT /clients/{id} (client_proxy_number)
 * - Approvisionnement = action séparée (menu dédié), pas de provision auto avant attribution
 */

const CLIENTS_SHEET_NAME = "Clients";
const PENDING_SHEET_NAME = "CONFIRMATION_PENDING";

const REQUIRED_HEADERS = [
  "client_id",
  "client_name",
  "client_mail",
  "client_real_phone",
  "client_proxy_number",
  "client_iso_residency",
  "client_country_code",
  "client_last_caller",
];
const PROTECTED_REFRESH_FIELDS = new Set(["client_iso_residency", "client_country_code"]);

// Paramètres par défaut pour le pool (alignés CLI)
const DEFAULT_COUNTRY = "FR";            // ISO2
const DEFAULT_NUMBER_TYPE = "national";  // "mobile" / "local" / "national"
const DEFAULT_PROVISION_QTY = 2;         // CLI: "2 par défaut"
const DEFAULT_REQUIRE_SMS = true;
const DEFAULT_REQUIRE_VOICE = true;
const DEFAULT_CANDIDATES_LIMIT = 10;

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("ProxyCall")
    .addItem("Attribuer proxy (sélection)", "proxycallAssignSelection_")
    //.addItem("Attribuer proxy (tous sans proxy)", "proxycallAssignAllNoProxy_")
    .addSeparator()
    .addItem("Envoyer email (sélection)", "proxycallEmailSelection_")
    .addSeparator()
    .addItem("Lister numéros disponibles (pays/type)", "proxycallListAvailablePrompt_")
    .addItem("Approvisionner le pool (achat) – saisie guidée", "proxycallProvisionPrompt_")
    .addSeparator()
    //.addItem("Rafraîchir ligne sélectionnée depuis Render", "proxycallRefreshSelected_")
    //.addSeparator()
    .addItem("Vérifier et compléter TwilioPools avec les numéros Twilio", "proxycallPoolSync_")
    .addItem("Libérer les numéros erronés", "proxycallReleaseErrorNumbers_")
    //.addItem("Purger numéros sans SMS", "proxycallPurgeSansSms_")
    //.addItem("Réparer webhooks (voice/SMS) – dry-run", "proxycallFixWebhooksDry_")
    //.addItem("Réparer webhooks (voice/SMS) – appliquer", "proxycallFixWebhooksApply_")
    .addSeparator()
    .addItem("Configurer (URL + token)", "proxycallConfigure_")
    .addToUi();
}

/* ---------------- Configuration ---------------- */

function proxycallConfigure_() {
  const ui = SpreadsheetApp.getUi();
  const props = PropertiesService.getScriptProperties();

  const currentBase = props.getProperty("PUBLIC_BASE_URL") || "";
  const baseResp = ui.prompt(
    "ProxyCall - Configuration",
    `PUBLIC_BASE_URL (ex: https://proxycall.onrender.com)\nActuel: ${currentBase || "(vide)"}`,
    ui.ButtonSet.OK_CANCEL
  );
  if (baseResp.getSelectedButton() !== ui.Button.OK) return;

  const tokenResp = ui.prompt(
    "ProxyCall - Configuration",
    "PROXYCALL_API_TOKEN (Bearer)",
    ui.ButtonSet.OK_CANCEL
  );
  if (tokenResp.getSelectedButton() !== ui.Button.OK) return;

  const baseUrl = String(baseResp.getResponseText() || "").trim().replace(/\/$/, "");
  const token = String(tokenResp.getResponseText() || "").trim();

  if (!baseUrl) return ui.alert("Erreur", "PUBLIC_BASE_URL vide.", ui.ButtonSet.OK);
  if (!token) return ui.alert("Erreur", "PROXYCALL_API_TOKEN vide.", ui.ButtonSet.OK);

  props.setProperty("PUBLIC_BASE_URL", baseUrl);
  props.setProperty("PROXYCALL_API_TOKEN", token);
  ui.alert("OK", "Configuration enregistrée.", ui.ButtonSet.OK);
}

/* ---------------- Attribution de proxy ---------------- */

function proxycallAssignSelection_() {
  const ui = SpreadsheetApp.getUi();
  try {
    const ctx = getClientsSheetContext_();
    const range = ctx.sheet.getActiveRange();
    if (!range) throw new Error("Aucune sélection détectée.");
    const startRow = range.getRow();
    const numRows = range.getNumRows();
    if (startRow < 2) throw new Error("Sélectionne une ou plusieurs lignes (pas l'en-tête).");

    const confirm = ui.alert(
      "Attribution proxy",
      `Attribuer un proxy aux ${numRows} ligne(s) sélectionnée(s) (clients déjà pourvus ignorés) ?\n\n` +
        `Note: contrairement à l'ancien script, il n'y a PLUS d'approvisionnement automatique.`,
      ui.ButtonSet.OK_CANCEL
    );
    if (confirm !== ui.Button.OK) return;

    const summary = assignRange_(ctx, startRow, numRows);

    ui.alert(
      "Terminé",
      `OK=${summary.ok} | Déjà pourvus=${summary.skipped} | Erreurs=${summary.failed}\n\nDétails:\n${truncate_(summary.details.join("\n"), 1500)}`,
      ui.ButtonSet.OK
    );
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

function proxycallAssignAllNoProxy_() {
  const ui = SpreadsheetApp.getUi();
  try {
    const ctx = getClientsSheetContext_();
    const stats = computeClientsStats_(ctx);
    if (stats.withoutProxy === 0) return ui.alert("Info", "Aucun client sans proxy.", ui.ButtonSet.OK);

    const confirm = ui.alert(
      "Attribution en masse",
      `Attribuer un proxy à TOUS les clients sans proxy ?\n` +
        `Total=${stats.total}\nSans proxy=${stats.withoutProxy}\n\n` +
        `Note: aucune commande d'achat n'est lancée automatiquement. Si le pool est insuffisant, certaines lignes échoueront.`,
      ui.ButtonSet.OK_CANCEL
    );
    if (confirm !== ui.Button.OK) return;

    const summary = assignAllNoProxy_(ctx);
    ui.alert(
      "Terminé",
      `OK=${summary.ok} | Déjà pourvus=${summary.skipped} | Erreurs=${summary.failed}\n\nDétails:\n${truncate_(summary.details.join("\n"), 1500)}`,
      ui.ButtonSet.OK
    );
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

function assignRange_(ctx, startRow, numRows) {
  let ok = 0, skipped = 0, failed = 0;
  const details = [];
  for (let r = startRow; r < startRow + numRows; r++) {
    try {
      const rec = readRowRecord_(ctx, r);
      const clientId = String(rec.client_id || "").trim();
      if (!clientId) throw new Error("client_id manquant.");
      if (String(rec.client_proxy_number || "").trim()) {
        skipped++; details.push(`Ligne ${r} (client_id=${clientId}): déjà pourvu`);
        continue;
      }
      assignProxyForRecord_(ctx, r, rec);
      ok++; details.push(`Ligne ${r} (client_id=${clientId}): OK`);
    } catch (e) {
      failed++; details.push(`Ligne ${r}: ERREUR (${normalizeErr_(e)})`);
      console.error(`Row ${r} failed`, e);
    }
  }
  return { ok, skipped, failed, details };
}

function assignAllNoProxy_(ctx) {
  let ok = 0, skipped = 0, failed = 0;
  const details = [];
  const values = ctx.sheet.getRange(2, 1, ctx.lastRow - 1, ctx.lastCol).getValues();
  for (let i = 0; i < values.length; i++) {
    const row = i + 2;
    try {
      const rec = recordFromRow_(ctx.headers, values[i]);
      const clientId = String(rec.client_id || "").trim();
      if (!clientId) throw new Error("client_id manquant.");
      if (String(rec.client_proxy_number || "").trim()) { skipped++; continue; }
      assignProxyForRecord_(ctx, row, rec);
      ok++; if (ok <= 30) details.push(`Ligne ${row} (client_id=${clientId}): OK`);
    } catch (e) {
      failed++; if (failed <= 30) details.push(`Ligne ${row}: ERREUR (${normalizeErr_(e)})`);
      console.error(`Row ${row} failed`, e);
    }
  }
  if (ok > 30) details.push(`... ${ok - 30} autres OK`);
  if (failed > 30) details.push(`... ${failed - 30} autres erreurs`);
  return { ok, skipped, failed, details };
}

/**
 * Attribution unitaire (alignée sur la CLI) :
 * - POST /pool/assign  -> récupère {proxy}
 * - PUT /clients/{id}  -> écrit client_proxy_number=proxy
 * - Écrit client_proxy_number dans la feuille (UI immédiate)
 */
function assignProxyForRecord_(ctx, row, rec) {
  const clientIdStr = String(rec.client_id || "").trim();
  const clientId = Number(clientIdStr);
  if (!clientIdStr || Number.isNaN(clientId) || clientId <= 0) throw new Error("client_id manquant/invalide.");
  if (String(rec.client_proxy_number || "").trim()) throw new Error("Déjà pourvu.");

  const countryIso = String(rec.client_iso_residency || DEFAULT_COUNTRY).trim().toUpperCase();
  const numberType = DEFAULT_NUMBER_TYPE;

  if (!countryIso || countryIso.length !== 2) throw new Error("country_iso manquant/invalide (ex: FR).");

  const clientName = String(rec.client_name || `Client-${clientId}`);

  const assignPayload = {
    client_id: clientId,
    country_iso: countryIso,
    client_name: clientName,
    number_type: numberType,
    friendly_name: clientName,
  };

  const assignRes = callApi_("post", "/pool/assign", assignPayload, null);
  const proxy = assignRes && assignRes.proxy ? String(assignRes.proxy).trim() : "";
  if (!proxy) throw new Error("Réponse /pool/assign invalide: champ 'proxy' absent.");

  callApi_("put", `/clients/${encodeURIComponent(String(clientId))}`, { client_proxy_number: proxy }, null);
  writeBackIfExists_(ctx, row, "client_proxy_number", proxy);
}

/* =========================
   Email clients (selection)
   ========================= */

const EMAIL_TEMPLATES = {
  PROXY_RESERVATION_ERROR: {
    label: "Erreur réservation proxy",
    subject: "[PROXY-CALL] Une erreur est survenue lors de la réservation de votre numéro proxy",
    body:
      "Bonjour {{client_name}}\n\n" +
      "Nous avons bien reçue ta demande de réservation de numéro proxy pour tes livraisons reseller 2.0. " +
      "Toutefois, nous avons constaté une erreur dans la procédure et t'invitons à renouveler ta demande. " +
      "N'hésite pas à nous contacter si tu rencontres des difficultés quelconque dans la procédure.\n\n" +
      "Bonne journée à toi.\n\n" +
      "Kevin Andreazza \n" +
      "Développeur Full Stack"
  }
};

function proxycallEmailSelection_() {
  const ui = SpreadsheetApp.getUi();

  const sheet = SpreadsheetApp.getActiveSheet();
  const sheetName = sheet.getName();

  // Autorise CONFIRMATION_PENDING + Clients
  const allowed = new Set([PENDING_SHEET_NAME, CLIENTS_SHEET_NAME]);
  if (!allowed.has(sheetName)) {
    ui.alert(
      "Action impossible",
      `Cette action doit être lancée depuis l'onglet "${PENDING_SHEET_NAME}" ou "${CLIENTS_SHEET_NAME}".\n` +
        `Onglet actuel: "${sheetName}".`,
      ui.ButtonSet.OK
    );
    return;
  }

  try {
    const range = sheet.getActiveRange();
    if (!range) throw new Error("Aucune sélection détectée.");

    const startRow = range.getRow();
    const numRows = range.getNumRows();
    if (startRow < 2) throw new Error("Sélectionne une ou plusieurs lignes (pas l'en-tête).");

    // Récupérer headers dynamiques de la feuille active
    const lastCol = sheet.getLastColumn();
    if (lastCol < 1) throw new Error("Feuille vide.");
    const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(h => String(h || "").trim());

    // Colonnes communes
    const idxName = headers.indexOf("client_name");
    const idxMail = headers.indexOf("client_mail");
    if (idxName === -1) throw new Error(`Colonne requise absente dans ${sheetName}: client_name`);
    if (idxMail === -1) throw new Error(`Colonne requise absente dans ${sheetName}: client_mail`);

    // Identifiant de référence (pending_id OU client_id)
    const refHeader = (sheetName === PENDING_SHEET_NAME) ? "pending_id" : "client_id";
    const idxRef = headers.indexOf(refHeader);
    if (idxRef === -1) throw new Error(`Colonne requise absente dans ${sheetName}: ${refHeader}`);

    const values = sheet.getRange(startRow, 1, numRows, lastCol).getValues();

    // 1) Construire la liste STRICTE "1 ligne sélectionnée = 1 email"
    // (donc si tu sélectionnes 2 lignes avec le même mail => 2 emails)
    const targets = [];
    for (let i = 0; i < values.length; i++) {
      const rowNumber = startRow + i;
      if (rowNumber < 2) continue;

      const row = values[i];
      const refId = String(row[idxRef] || "").trim();
      const name = String(row[idxName] || "").trim();
      const mail = String(row[idxMail] || "").trim().toLowerCase();

      if (!mail) continue;
      if (!isEmailValid_(mail)) continue;

      targets.push({
        rowNumber,
        refId,      // pending_id OU client_id
        name,
        mail,
        context: sheetName,
      });
    }

    if (!targets.length) {
      ui.alert("Info", "Aucun email valide trouvé dans la sélection.", ui.ButtonSet.OK);
      return;
    }

    // 2) Choix modèle (prédéfini ou personnalisé)
    const tplResp = ui.prompt(
      "Choix du modèle",
      "Tape :\n" +
        "1 = Erreur réservation proxy\n" +
        "2 = Message personnalisé",
      ui.ButtonSet.OK_CANCEL
    );
    if (tplResp.getSelectedButton() !== ui.Button.OK) return;

    const tplChoice = String(tplResp.getResponseText() || "").trim();
    let subject = "";
    let bodyTemplate = "";

    if (tplChoice === "1") {
      subject = EMAIL_TEMPLATES.PROXY_RESERVATION_ERROR.subject;
      bodyTemplate = EMAIL_TEMPLATES.PROXY_RESERVATION_ERROR.body;
    } else if (tplChoice === "2") {
      const subjResp = ui.prompt(
        `Envoyer un email (${sheetName})`,
        "Objet de l'email :",
        ui.ButtonSet.OK_CANCEL
      );
      if (subjResp.getSelectedButton() !== ui.Button.OK) return;
      subject = String(subjResp.getResponseText() || "").trim();
      if (!subject) throw new Error("Objet vide.");

      const bodyResp = ui.prompt(
        `Envoyer un email (${sheetName})`,
        `Message (texte). Variables: {{client_name}} et {{${refHeader}}}`,
        ui.ButtonSet.OK_CANCEL
      );
      if (bodyResp.getSelectedButton() !== ui.Button.OK) return;
      bodyTemplate = String(bodyResp.getResponseText() || "").trim();
      if (!bodyTemplate) throw new Error("Message vide.");
    } else {
      throw new Error("Choix invalide. Tape 1 ou 2.");
    }

    // 3) Preview + confirmation
    const previewList = targets
      .slice(0, 20)
      .map(t => `- ligne ${t.rowNumber}: ${t.mail} (${t.name || "sans nom"})`)
      .join("\n");

    const confirm = ui.alert(
      "Confirmation envoi",
      `Onglet: ${sheetName}\nEmails séparés qui seront envoyés: ${targets.length}\n\n` +
        `Aperçu (max 20):\n${previewList}\n\n` +
        `Objet: ${subject}\n\n` +
        `Envoyer maintenant ?`,
      ui.ButtonSet.OK_CANCEL
    );
    if (confirm !== ui.Button.OK) return;

    // --- APERÇU AVANT ENVOI (sur le 1er destinataire)
    const first = targets[0];
    const firstPreviewBody = bodyTemplate
      .replaceAll("{{client_name}}", first.name || "")
      // Compat: templates / messages peuvent utiliser pending_id OU client_id
      .replaceAll("{{pending_id}}", first.refId || "")
      .replaceAll("{{client_id}}", first.refId || "");

    const recipientsPreview = targets
      .slice(0, 25)
      .map(t => `- ${t.mail} (${t.name || "sans nom"}) [ligne ${t.rowNumber}]`)
      .join("\n");

    const previewText =
      "APERÇU AVANT ENVOI\n\n" +
      `Onglet: ${sheetName}\n` +
      `Objet:\n${subject}\n\n` +
      "Corps (exemple sur le 1er destinataire):\n" +
      "----------------------------------------\n" +
      `${firstPreviewBody}\n` +
      "----------------------------------------\n\n" +
      `Destinataires: ${targets.length}\n` +
      `${recipientsPreview}` +
      (targets.length > 25 ? `\n... +${targets.length - 25} autres` : "");

    const previewOk = ui.alert(
      "Aperçu email",
      truncate_(previewText, 1800),
      ui.ButtonSet.OK_CANCEL
    );
    if (previewOk !== ui.Button.OK) return;

    // 4) Envoi 1 par 1 + log
    const logSheet = getOrCreateEmailLogSheet_();
    let ok = 0, failed = 0;

    for (const t of targets) {
      try {
        const personalized = bodyTemplate
          .replaceAll("{{client_name}}", t.name || "")
          // Compat: templates / messages peuvent utiliser pending_id OU client_id
          .replaceAll("{{pending_id}}", t.refId || "")
          .replaceAll("{{client_id}}", t.refId || "");

        MailApp.sendEmail({
          to: t.mail,
          subject: subject,
          body: personalized,
        });

        ok++;
        logEmail_(logSheet, {
          status: "SENT",
          context: t.context,     // "CONFIRMATION_PENDING" ou "Clients"
          ref_id: t.refId,        // pending_id ou client_id
          row_number: t.rowNumber,
          email: t.mail,
          subject: subject,
          error: "",
        });
      } catch (e) {
        failed++;
        logEmail_(logSheet, {
          status: "FAILED",
          context: t.context,
          ref_id: t.refId,
          row_number: t.rowNumber,
          email: t.mail,
          subject: subject,
          error: normalizeErr_(e),
        });
      }
    }

    ui.alert(
      "Terminé",
      `Envoyés (emails séparés): ${ok}\nÉchecs: ${failed}\n\nLog: onglet ${EMAIL_LOG_SHEET}`,
      ui.ButtonSet.OK
    );

  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

/* ---------------- Helpers email ---------------- */

const EMAIL_LOG_SHEET = "EMAIL_LOG";

function isEmailValid_(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(email || "").trim());
}

function getOrCreateEmailLogSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(EMAIL_LOG_SHEET);

  if (!sh) {
    sh = ss.insertSheet(EMAIL_LOG_SHEET);
  }

  // Initialise l'en-tête si la feuille est vide
  const lastRow = sh.getLastRow();
  const lastCol = sh.getLastColumn();

  const expected = ["ts", "status", "context", "ref_id", "row_number", "email", "subject", "error"];

  if (lastRow === 0 || lastCol === 0) {
    sh.getRange(1, 1, 1, expected.length).setValues([expected]);
    return sh;
  }

  // Si l'en-tête ne correspond pas, on le remet (sur les 8 premières colonnes)
  const headers = sh.getRange(1, 1, 1, Math.max(expected.length, lastCol)).getValues()[0]
    .slice(0, expected.length)
    .map(h => String(h || "").trim());

  const same = expected.every((h, i) => headers[i] === h);
  if (!same) {
    sh.getRange(1, 1, 1, expected.length).setValues([expected]);
  }

  return sh;
}

function logEmail_(sh, data) {
  try {
    sh.appendRow([
      new Date().toISOString(),
      String(data.status || ""),
      String(data.context || ""),
      String(data.ref_id || ""),
      String(data.row_number || ""),
      String(data.email || ""),
      String(data.subject || ""),
      String(data.error || "").slice(0, 500),
    ]);
  } catch (e) {
    console.log("[EMAIL_LOG] failed: " + normalizeErr_(e));
  }
}

/* ---------------- Pool : lister / approvisionner (alignement CLI) ---------------- */

/**
 * Équivalent CLI: "Lister les numéros disponibles par pays"
 * (ici on demande pays + type, puis on affiche les compteurs)
 */
function proxycallListAvailablePrompt_() {
  const ui = SpreadsheetApp.getUi();
  try {
    const countryResp = ui.prompt(
      "Lister les numéros disponibles",
      `Pays ISO2 (ex: FR) [défaut: ${DEFAULT_COUNTRY}] :`,
      ui.ButtonSet.OK_CANCEL
    );
    if (countryResp.getSelectedButton() !== ui.Button.OK) return;
    const countryIso = (String(countryResp.getResponseText() || "").trim() || DEFAULT_COUNTRY).toUpperCase();
    if (!countryIso || countryIso.length !== 2) throw new Error("Pays ISO invalide (ex: FR).");

    const typeResp = ui.prompt(
      "Lister les numéros disponibles",
      `Type (mobile/local/national) [défaut: ${DEFAULT_NUMBER_TYPE}] :`,
      ui.ButtonSet.OK_CANCEL
    );
    if (typeResp.getSelectedButton() !== ui.Button.OK) return;
    const numberType = (String(typeResp.getResponseText() || "").trim() || DEFAULT_NUMBER_TYPE).toLowerCase();

    const res = listPool_(countryIso, numberType);
    const available = (res && res.available) ? res.available : [];
    ui.alert(
      "Disponibles",
      `Pays=${countryIso}\nType=${numberType}\nDisponibles=${available.length}\n\n` +
        `Exemples:\n${truncate_(available.slice(0, 10).join("\n"), 1500)}`,
      ui.ButtonSet.OK
    );
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

/**
 * Équivalent CLI: "Approvisionner le pool"
 * - Demande pays
 * - Demande combien acheter (défaut 2)
 * - Demande type (défaut national)
 * - Appelle POST /pool/provision
 */
function proxycallProvisionPrompt_() {
  const ui = SpreadsheetApp.getUi();
  try {
    const countryResp = ui.prompt(
      "Approvisionner le pool (achat)",
      `Pays ISO2 (ex: FR) [défaut: ${DEFAULT_COUNTRY}] :`,
      ui.ButtonSet.OK_CANCEL
    );
    if (countryResp.getSelectedButton() !== ui.Button.OK) return;
    const countryIso = (String(countryResp.getResponseText() || "").trim() || DEFAULT_COUNTRY).toUpperCase();
    if (!countryIso || countryIso.length !== 2) throw new Error("Pays ISO invalide (ex: FR).");

    const qtyResp = ui.prompt(
      "Approvisionner le pool (achat)",
      `Combien de numéros acheter ? [défaut: ${DEFAULT_PROVISION_QTY}] :`,
      ui.ButtonSet.OK_CANCEL
    );
    if (qtyResp.getSelectedButton() !== ui.Button.OK) return;
    const qtyRaw = String(qtyResp.getResponseText() || "").trim();
    const qty = qtyRaw ? parseInt(qtyRaw, 10) : DEFAULT_PROVISION_QTY;
    if (!qty || Number.isNaN(qty) || qty <= 0) throw new Error("Quantité invalide (entier > 0).");

    const typeResp = ui.prompt(
      "Approvisionner le pool (achat)",
      `Type (mobile/local/national) [défaut: ${DEFAULT_NUMBER_TYPE}] :`,
      ui.ButtonSet.OK_CANCEL
    );
    if (typeResp.getSelectedButton() !== ui.Button.OK) return;
    const numberType = (String(typeResp.getResponseText() || "").trim() || DEFAULT_NUMBER_TYPE).toLowerCase();

    const confirm = ui.alert(
      "Confirmation d'achat",
      `Acheter ${qty} numéro(s) pour le pool ?\nPays=${countryIso}\nType=${numberType}\n\n` +
        `Cette action peut générer des coûts Twilio.`,
      ui.ButtonSet.OK_CANCEL
    );
    if (confirm !== ui.Button.OK) return;

    const res = provisionPool_(countryIso, qty, numberType);
    ui.alert(
      "Approvisionnement terminé",
      truncate_(JSON.stringify(res, null, 2), 1500),
      ui.ButtonSet.OK
    );
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

/* ---------------- Pool : endpoints bas niveau ---------------- */

function listPool_(countryIso, numberType) {
  const c = countryIso.toUpperCase();
  const nt = numberType || "";
  return callApi_("get", `/pool/available?country_iso=${encodeURIComponent(c)}&number_type=${encodeURIComponent(nt)}`, null, null);
}

function provisionPool_(countryIso, batchSize, numberType) {
  const payload = {
    country_iso: countryIso,
    batch_size: Math.max(1, parseInt(batchSize || 1, 10)),
    number_type: numberType || DEFAULT_NUMBER_TYPE,
    require_sms_capability: DEFAULT_REQUIRE_SMS,
    require_voice_capability: DEFAULT_REQUIRE_VOICE,
    candidates_limit: DEFAULT_CANDIDATES_LIMIT,
  };
  console.log(`[ProxyCall][provision] ${JSON.stringify(payload)}`);
  return callApi_("post", "/pool/provision", payload, null);
}

/* ---------------- Sync / purge / webhooks ---------------- */

function proxycallPoolSync_() {
  const ui = SpreadsheetApp.getUi();

  try {
    // 1) Scan sans appliquer (comme la CLI avant confirmation)
    const scan = callApi_("post", "/pool/sync", { apply: false }, null);

    const missing = (scan && scan.missing_numbers) ? scan.missing_numbers : [];
    const totalTwilio = scan && (scan.total_twilio !== undefined) ? scan.total_twilio : "?";
    const totalSheet = scan && (scan.total_sheet !== undefined) ? scan.total_sheet : "?";

    if (!missing.length) {
      ui.alert(
        "Synchronisation pool Twilio",
        `Aucun numéro Twilio manquant.\nTwilio=${totalTwilio} | TwilioPools=${totalSheet}`,
        ui.ButtonSet.OK
      );
      return;
    }

    // 2) Afficher + demander confirmation (comme la CLI)
    const preview = truncate_(missing.map((n) => `- ${n}`).join("\n"), 1200);
    const confirm = ui.alert(
      "Synchronisation pool Twilio",
      `Numéros trouvés côté Twilio: ${totalTwilio}\n` +
        `Numéros absents de TwilioPools: ${missing.length}\n\n` +
        `${preview}\n\n` +
        `Importer ces numéros manquants dans TwilioPools ?`,
      ui.ButtonSet.OK_CANCEL
    );

    if (confirm !== ui.Button.OK) return;

    // 3) Appliquer l'import
    const applied = callApi_("post", "/pool/sync", { apply: true }, null);

    ui.alert(
      "Synchronisation terminée",
      truncate_(JSON.stringify(applied, null, 2), 1500),
      ui.ButtonSet.OK
    );
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

/* ---------------- Libérer les numéros erronés ---------------- */

/**
 * Lit la feuille TwilioPools, identifie en colonne C toutes les lignes
 * avec la mention "error", et libère les numéros Twilio correspondants
 * via POST /pool/release.
 */
function proxycallReleaseErrorNumbers_() {
  const ui = SpreadsheetApp.getUi();
  const POOL_SHEET_NAME = "TwilioPools";
  const ERROR_MARKER = "error";

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(POOL_SHEET_NAME);
    if (!sheet) throw new Error("Onglet introuvable : " + POOL_SHEET_NAME);

    const lastRow = sheet.getLastRow();
    if (lastRow < 2) {
      ui.alert(
        "Aucune donnée",
        "La feuille " + POOL_SHEET_NAME + " est vide ou ne contient que l'en-tête.",
        ui.ButtonSet.OK
      );
      return;
    }

    const rangeB = sheet.getRange(2, 2, lastRow - 1, 1).getValues();
    const rangeC = sheet.getRange(2, 3, lastRow - 1, 1).getValues();

    const errorNumbers = [];
    for (let i = 0; i < rangeC.length; i++) {
      const status = String(rangeC[i][0] || "").trim().toLowerCase();
      if (status === ERROR_MARKER) {
        const phone = String(rangeB[i][0] || "").trim();
        if (phone) errorNumbers.push(phone);
      }
    }

    if (!errorNumbers.length) {
      ui.alert(
        "Libération des numéros erronés",
        "Aucun numéro avec la mention \"" + ERROR_MARKER + "\" trouvé en colonne C de " + POOL_SHEET_NAME + ".",
        ui.ButtonSet.OK
      );
      return;
    }

    const confirm = ui.alert(
      "Libération des numéros erronés",
      errorNumbers.length + " numéro(s) en erreur détecté(s) :\n\n" +
        truncate_(errorNumbers.join("\n"), 1200) + "\n\n" +
        "Libérer ces numéros côté Twilio ?",
      ui.ButtonSet.OK_CANCEL
    );
    if (confirm !== ui.Button.OK) return;

    const res = callApi_("post", "/pool/release", { numbers: errorNumbers }, null);

    // Supprimer les lignes libérées de TwilioPools (du bas vers le haut)
    const releasedSet = new Set((res.released || []).map(function(n) { return String(n).trim(); }));
    if (releasedSet.size > 0) {
      const allB = sheet.getRange(2, 2, lastRow - 1, 1).getValues();
      let deletedCount = 0;
      for (let r = allB.length - 1; r >= 0; r--) {
        const phone = String(allB[r][0] || "").trim();
        if (releasedSet.has(phone)) {
          sheet.deleteRow(r + 2);
          deletedCount++;
        }
      }
      console.log("[ProxyCall][release] " + deletedCount + " ligne(s) supprimée(s) de " + POOL_SHEET_NAME);
    }

    ui.alert(
      "Libération terminée",
      truncate_(JSON.stringify(res, null, 2), 1500),
      ui.ButtonSet.OK
    );
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

function proxycallPurgeSansSms_() {
  try {
    const res = callApi_("post", "/pool/purge-sans-sms", {}, null);
    SpreadsheetApp.getUi().alert("Purge terminée", truncate_(JSON.stringify(res, null, 2), 1500), SpreadsheetApp.getUi().ButtonSet.OK);
  } catch (e) {
    console.error(e);
    SpreadsheetApp.getUi().alert("Erreur", normalizeErr_(e), SpreadsheetApp.getUi().ButtonSet.OK);
  }
}

function proxycallFixWebhooksDry_() {
  try {
    const res = callApi_("post", "/pool/fix-webhooks", { dry_run: true }, null);
    SpreadsheetApp.getUi().alert("Simulation terminée", truncate_(JSON.stringify(res, null, 2), 1500), SpreadsheetApp.getUi().ButtonSet.OK);
  } catch (e) {
    console.error(e);
    SpreadsheetApp.getUi().alert("Erreur", normalizeErr_(e), SpreadsheetApp.getUi().ButtonSet.OK);
  }
}

function proxycallFixWebhooksApply_() {
  const ui = SpreadsheetApp.getUi();
  const confirm = ui.alert(
    "Réparer les webhooks Twilio",
    "Cette action modifie la configuration Twilio (voice/sms_url). Continuer ?",
    ui.ButtonSet.OK_CANCEL
  );
  if (confirm !== ui.Button.OK) return;

  try {
    const res = callApi_("post", "/pool/fix-webhooks", { dry_run: false }, null);
    ui.alert("Réparation appliquée", truncate_(JSON.stringify(res, null, 2), 1500), ui.ButtonSet.OK);
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

/* ---------------- Rafraîchir une ligne client ---------------- */

function proxycallRefreshSelected_() {
  const ui = SpreadsheetApp.getUi();
  try {
    const ctx = getClientsSheetContext_();
    const range = ctx.sheet.getActiveRange();
    if (!range) throw new Error("Aucune sélection détectée.");
    const row = range.getRow();
    if (row < 2) throw new Error("Sélectionne une ligne client (pas l'en-tête).");

    const rec = readRowRecord_(ctx, row);
    const clientId = String(rec.client_id || "").trim();
    if (!clientId) throw new Error("client_id manquant sur la ligne.");

    const fresh = callApi_("get", `/clients/${encodeURIComponent(clientId)}`, null, null);
    for (const [k, v] of Object.entries(fresh || {})) {
      if (!ctx.headers.includes(k)) continue;
      if (PROTECTED_REFRESH_FIELDS.has(k)) continue;
      if (v && typeof v === "object") continue;
      writeBackIfExists_(ctx, row, k, v);
    }
    ui.alert("OK", "Ligne rafraîchie depuis Render.", ui.ButtonSet.OK);
  } catch (e) {
    console.error(e);
    ui.alert("Erreur", normalizeErr_(e), ui.ButtonSet.OK);
  }
}

/* ---------------- Helpers sheet ---------------- */

function getClientsSheetContext_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(CLIENTS_SHEET_NAME);
  if (!sheet) throw new Error(`Onglet introuvable: ${CLIENTS_SHEET_NAME}`);

  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  if (lastRow < 1 || lastCol < 1) throw new Error("Feuille Clients vide.");

  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map((h) => String(h || "").trim());
  for (const req of REQUIRED_HEADERS) {
    if (!headers.includes(req)) throw new Error(`Colonne requise absente: ${req}`);
  }
  return { ss, sheet, headers, lastRow, lastCol };
}

function readRowRecord_(ctx, row) {
  const values = ctx.sheet.getRange(row, 1, 1, ctx.lastCol).getValues()[0];
  return recordFromRow_(ctx.headers, values);
}

function recordFromRow_(headers, values) {
  const out = {};
  for (let i = 0; i < headers.length; i++) out[headers[i]] = values[i];
  return out;
}

function writeBackIfExists_(ctx, row, colName, value) {
  const idx = ctx.headers.indexOf(colName);
  if (idx === -1) return;
  ctx.sheet.getRange(row, idx + 1).setValue(value === null || value === undefined ? "" : value);
}

function computeClientsStats_(ctx) {
  const values = ctx.sheet.getRange(2, 1, Math.max(ctx.lastRow - 1, 0), ctx.lastCol).getValues();
  let total = values.length, withoutProxy = 0;
  for (let i = 0; i < values.length; i++) {
    const rec = recordFromRow_(ctx.headers, values[i]);
    if (!String(rec.client_proxy_number || "").trim()) withoutProxy++;
  }
  return { total, withoutProxy };
}

/* ---------------- HTTP helper ---------------- */

function callApi_(method, path, payload, params) {
  const props = PropertiesService.getScriptProperties();
  const baseUrl = String(props.getProperty("PUBLIC_BASE_URL") || "").trim().replace(/\/$/, "");
  const token = String(props.getProperty("PROXYCALL_API_TOKEN") || "").trim();

  if (!baseUrl) throw new Error("PUBLIC_BASE_URL non configurée (menu ProxyCall → Configurer).");
  if (!token) throw new Error("PROXYCALL_API_TOKEN non configuré (menu ProxyCall → Configurer).");

  let url = baseUrl + path;
  if (params && Object.keys(params).length) {
    const q = Object.keys(params)
      .filter((k) => params[k] !== null && params[k] !== undefined && String(params[k]).trim() !== "")
      .map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(String(params[k])))
      .join("&");
    if (q) url += "?" + q;
  }

  const options = {
    method: String(method || "get").toUpperCase(),
    muteHttpExceptions: true,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
    },
  };
  if (payload && options.method !== "GET") {
    options.payload = JSON.stringify(payload);
  }

  const resp = UrlFetchApp.fetch(url, options);
  const status = resp.getResponseCode();
  const text = resp.getContentText() || "";

  if (status >= 400) {
    let detail = text;
    try {
      const j = JSON.parse(text);
      detail = j.detail ? JSON.stringify(j.detail) : JSON.stringify(j);
    } catch (_) {}
    throw new Error(`API ${status} ${path}: ${truncate_(detail, 800)}`);
  }

  try {
    return text ? JSON.parse(text) : {};
  } catch (e) {
    throw new Error(`JSON invalide ${path}: ${truncate_(text, 300)}`);
  }
}

/* ---------------- Utils ---------------- */

function normalizeErr_(e) {
  if (!e) return "Erreur inconnue";
  if (typeof e === "string") return e;
  if (e.message) return e.message;
  return String(e);
}

function truncate_(s, maxLen) {
  const str = String(s || "");
  return str.length > maxLen ? str.slice(0, maxLen - 3) + "..." : str;
}
