-- Schema SQLite local - Prototype Recouvrement Automatique des Factures Impayees (BWAI 2026)
-- Garantit l'idempotence, la prevention des doublons et la reprise controlee hors connexion.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('GESTIONNAIRE', 'VALIDATEUR', 'ADMIN')),
    token_hash TEXT NOT NULL UNIQUE,
    statut TEXT NOT NULL DEFAULT 'ACTIF' CHECK (statut IN ('ACTIF', 'INACTIF'))
);

CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    nom_affiche TEXT NOT NULL,
    contact_simule TEXT NOT NULL,
    canal_prefere TEXT NOT NULL CHECK (canal_prefere IN ('WHATSAPP', 'SMS', 'EMAIL')),
    consentement_contact INTEGER NOT NULL DEFAULT 1 CHECK (consentement_contact IN (0, 1)),
    nb_factures_historiques INTEGER NOT NULL DEFAULT 0 CHECK (nb_factures_historiques >= 0),
    nb_retards_historiques INTEGER NOT NULL DEFAULT 0 CHECK (nb_retards_historiques >= 0),
    statut TEXT NOT NULL DEFAULT 'ACTIF'
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(client_id),
    date_emission TEXT NOT NULL,
    date_echeance TEXT NOT NULL,
    montant_initial REAL NOT NULL CHECK (montant_initial >= 0),
    devise TEXT NOT NULL DEFAULT 'FCFA',
    statut_source TEXT,
    dernier_message_client TEXT,
    -- Champs calcules de maniere deterministe (Module C & D)
    total_paye REAL NOT NULL DEFAULT 0.0,
    total_avoirs REAL NOT NULL DEFAULT 0.0,
    solde_restant REAL NOT NULL DEFAULT 0.0,
    jours_retard INTEGER NOT NULL DEFAULT 0,
    statut_calcule TEXT NOT NULL DEFAULT 'NON_ECHUE' CHECK (
        statut_calcule IN (
            'NON_ECHUE',
            'ECHUE',
            'PARTIELLEMENT_PAYEE',
            'SOLDEE',
            'CONTESTEE',
            'COUVERTE_PAR_ECHEANCIER'
        )
    ),
    statut_paiement TEXT NOT NULL DEFAULT 'IMPAYEE' CHECK (
        statut_paiement IN ('IMPAYEE', 'PARTIELLEMENT_PAYEE', 'SOLDEE')
    ),
    -- Champs de priorisation explicable (Module E)
    score_priorite REAL NOT NULL DEFAULT 0.0,
    explication_score TEXT,
    eligible_relance INTEGER NOT NULL DEFAULT 0,
    -- Champs d'analyse IA du message client
    ai_categorie TEXT,
    ai_confiance REAL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
    date_paiement TEXT NOT NULL,
    montant REAL NOT NULL CHECK (montant > 0),
    reference_fictive TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'VALIDE' CHECK (statut IN ('VALIDE', 'ANNULE')),
    source TEXT NOT NULL DEFAULT 'IMPORT' CHECK (source IN ('IMPORT', 'SIMULATION')),
    -- Cle anti-doublon physique empechant toute double comptabilisation (Module J / Test T9)
    UNIQUE(invoice_id, reference_fictive)
);

CREATE TABLE IF NOT EXISTS credit_notes (
    credit_note_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
    date_avoir TEXT NOT NULL,
    montant REAL NOT NULL CHECK (montant > 0),
    motif TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS installment_plans (
    plan_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
    date_prochaine_echeance TEXT NOT NULL,
    montant_prevu REAL NOT NULL CHECK (montant_prevu > 0),
    statut TEXT NOT NULL DEFAULT 'ACTIF' CHECK (statut IN ('ACTIF', 'ROMPU', 'TERMINE'))
);

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
    motif TEXT NOT NULL,
    date_ouverture TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'OUVERT' CHECK (statut IN ('OUVERT', 'RESOLU')),
    detecte_par TEXT NOT NULL DEFAULT 'HUMAIN' CHECK (detecte_par IN ('HUMAIN', 'IA'))
);

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
    canal TEXT NOT NULL CHECK (canal IN ('WHATSAPP', 'SMS', 'EMAIL')),
    date_creation TEXT NOT NULL,
    date_envoi_simule TEXT,
    niveau_relance TEXT NOT NULL CHECK (
        niveau_relance IN ('RAPPEL_PRE_ECHEANCE', 'RELANCE_1', 'RELANCE_2')
    ),
    contenu_message TEXT NOT NULL,
    statut_action TEXT NOT NULL DEFAULT 'BROUILLON' CHECK (
        statut_action IN (
            'BROUILLON',
            'EN_ATTENTE_VALIDATION',
            'APPROUVEE',
            'SIMULEE',
            'ANNULEE',
            'REJETEE'
        )
    ),
    requiert_revue_humaine INTEGER NOT NULL DEFAULT 0,
    motif_blocage TEXT,
    cree_par TEXT NOT NULL DEFAULT 'SYSTEME',
    valide_par TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    -- Cle anti-doublon metier : une seule relance par facture et par niveau de relance (Section 9 / Test T10)
    UNIQUE(invoice_id, niveau_relance)
);

CREATE TABLE IF NOT EXISTS import_errors (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    ligne_numero INTEGER NOT NULL,
    entite TEXT NOT NULL,
    donnees_brutes TEXT NOT NULL,
    motif_rejet TEXT NOT NULL,
    date_import TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    acteur TEXT NOT NULL,
    action TEXT NOT NULL,
    objet_type TEXT NOT NULL,
    objet_id TEXT NOT NULL,
    ancienne_valeur TEXT,
    nouvelle_valeur TEXT,
    motif TEXT,
    prev_hash TEXT NOT NULL DEFAULT 'GENESIS',
    entry_hash TEXT NOT NULL DEFAULT ''
);

-- Protection d'immuabilite du journal d'audit (Partie C.3 : interdiction d'UPDATE et de DELETE)
CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Journal d audit immuable : modification interdite');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
BEFORE DELETE ON audit_logs
WHEN (SELECT value FROM settings WHERE key = 'allow_audit_reset') IS NOT '1'
BEGIN
    SELECT RAISE(ABORT, 'Journal d audit immuable : suppression interdite');
END;
