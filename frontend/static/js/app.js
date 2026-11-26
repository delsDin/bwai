/*
 * BWAI 2026 - Logique Client Moderne (Onglets accessibles, Modales <dialog>, RBAC en direct, Toasts)
 */

(function () {
    "use strict";

    const request = window.bwaiDemo?.request || window.fetch.bind(window);
    const refresh = window.bwaiDemo?.refresh || (() => window.location.reload());

    function storage(type, key, value) {
        try {
            const area = window[type];
            return value === undefined ? area.getItem(key) : area.setItem(key, value);
        } catch {
            return null;
        }
    }

    function handleAsync(action) {
        return async (event) => {
            event?.preventDefault?.();
            const submit = event?.submitter;
            if (submit) submit.disabled = true;
            try {
                await action(event);
            } catch (error) {
                showToast("Action non effectuée", error.message || String(error), "error");
            } finally {
                if (submit) submit.disabled = false;
            }
        };
    }

    function escapeHtml(value) {
        const node = document.createElement("span");
        node.textContent = String(value ?? "");
        return node.innerHTML;
    }

    // --- 1. Gestion du profil operateur (RBAC & 4 yeux) ---
    const ROLE_STORAGE_KEY = "bwai_active_token";
    const roleSelect = document.getElementById("operator-role-select");

    if (roleSelect) {
        const savedToken = storage("localStorage", ROLE_STORAGE_KEY);
        if (savedToken && Array.from(roleSelect.options).some((o) => o.value === savedToken)) {
            roleSelect.value = savedToken;
        }
        roleSelect.addEventListener("change", () => {
            storage("localStorage", ROLE_STORAGE_KEY, roleSelect.value);
            const label = roleSelect.options[roleSelect.selectedIndex].text;
            showToast("Profil actif changé", `Opérateur : ${label}`, "success");
        });
    }

    function getActiveHeaders(extraHeaders = {}) {
        const headers = { ...extraHeaders };
        const token = roleSelect ? roleSelect.value : storage("localStorage", ROLE_STORAGE_KEY);
        if (token) {
            headers["X-API-Token"] = token;
        }
        return headers;
    }

    // --- 2. Systeme de notifications Toast ---
    function showToast(title, message, type = "info") {
        const stack = document.getElementById("toast-stack");
        if (!stack) return;
        const el = document.createElement("div");
        el.className = `toast is-${type}`;
        const strong = document.createElement("strong");
        strong.textContent = title;
        const span = document.createElement("span");
        span.textContent = message;
        el.append(strong, span);
        stack.appendChild(el);
        setTimeout(() => {
            el.remove();
        }, 4200);
    }
    window.showToast = showToast;

    // --- 3. Navigation par onglets au clavier ---
    const tablist = document.querySelector('[role="tablist"]');
    if (tablist) {
        const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
        const panels = tabs.map((tab) => document.getElementById(tab.dataset.target));

        function activateTab(selectedTab, moveFocus = false) {
            tabs.forEach((tab, idx) => {
                const isSelected = tab === selectedTab;
                tab.setAttribute("aria-selected", isSelected ? "true" : "false");
                tab.tabIndex = isSelected ? 0 : -1;
                const panel = panels[idx];
                if (panel) {
                    if (isSelected) {
                        panel.removeAttribute("hidden");
                    } else {
                        panel.hidden = true;
                    }
                }
            });
            if (moveFocus) {
                selectedTab.focus();
            }
            if (moveFocus) selectedTab.scrollIntoView({ block: "nearest", inline: "nearest" });
            if (selectedTab.id) {
                storage("sessionStorage", "bwai_active_tab", selectedTab.id);
            }
        }

        tabs.forEach((tab, idx) => {
            tab.addEventListener("click", () => activateTab(tab, false));
            tab.addEventListener("keydown", (e) => {
                let nextIdx = null;
                if (e.key === "ArrowRight") nextIdx = (idx + 1) % tabs.length;
                if (e.key === "ArrowLeft") nextIdx = (idx - 1 + tabs.length) % tabs.length;
                if (e.key === "Home") nextIdx = 0;
                if (e.key === "End") nextIdx = tabs.length - 1;
                if (nextIdx !== null) {
                    e.preventDefault();
                    activateTab(tabs[nextIdx], true);
                }
            });
        });

        const savedTabId = storage("sessionStorage", "bwai_active_tab");
        const savedTab = savedTabId ? document.getElementById(savedTabId) : null;
        if (savedTab && tabs.includes(savedTab)) {
            activateTab(savedTab, false);
        }
    }

    // --- 4. Fallback light-dismiss pour <dialog closedby="any"> (Guide modern-web-guidance: dialog) ---
    if (typeof HTMLDialogElement !== "undefined" && !("closedBy" in HTMLDialogElement.prototype)) {
        const outside = (e, dialog) => {
            const r = dialog.getBoundingClientRect();
            return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
        };
        for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
            let pressedOutside = false;
            dialog.addEventListener("pointerdown", (e) => {
                pressedOutside = outside(e, dialog);
            });
            dialog.addEventListener("pointerup", (e) => {
                if (pressedOutside && outside(e, dialog)) dialog.close();
            });
        }
    }

    // --- 5. Cache persistant de l'etat de l'application (Serveur + Navigateur) & Appels API ---
    const APP_CACHE_KEY = "bwai_app_state_cache_v1";

    async function syncAppStateCache() {
        if (window.bwaiDemo) return;
        try {
            const res = await request("/api/system/cache");
            if (!res.ok) return;
            const snapshot = await res.json();
            if (snapshot && snapshot.has_active_data) {
                storage("localStorage", APP_CACHE_KEY, JSON.stringify(snapshot));
            }
        } catch {
            // Ignore cache sync errors silently
        }
    }

    async function autoRestoreCachedStateIfNeeded() {
        if (window.bwaiDemo) return;
        const hasServerData = document.body?.dataset?.hasData === "true";
        if (hasServerData) {
            await syncAppStateCache();
            return;
        }
        const rawCache = storage("localStorage", APP_CACHE_KEY);
        if (!rawCache) return;
        try {
            const snapshot = JSON.parse(rawCache);
            if (!snapshot || !snapshot.has_active_data) return;
            const res = await request("/api/system/cache/restore", {
                method: "POST",
                headers: getActiveHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify(snapshot),
            });
            if (res.ok) {
                const data = await res.json();
                if (data.restored) {
                    showToast("Session restaurée", "Vos données importées ont été rechargées depuis le cache.", "success");
                    setTimeout(refresh, 500);
                }
            }
        } catch {
            // Ignore restore errors
        }
    }

    window.openImportTab = function () {
        const importTab = document.getElementById("tab-import");
        if (importTab) {
            importTab.click();
            document.getElementById("import-file-input")?.focus();
        }
    };

    window.clearAllData = handleAsync(async () => {
        const res = await request("/api/system/clear", {
            method: "POST",
            headers: getActiveHeaders(),
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(`Erreur HTTP ${res.status}`, data.detail || "Impossible d'effacer les données", "error");
            return;
        }
        try {
            window.localStorage.removeItem(APP_CACHE_KEY);
            window.localStorage.removeItem("bwai-offline-demo-v1");
        } catch {
            // Ignore localStorage removal errors
        }
        showToast("Remise à zéro effectuée", data.message || "Toutes les données et le cache ont été effacés.", "warning");
        setTimeout(refresh, 800);
    });

    window.callApiAction = async function (url, reloadAfter = true) {
        try {
            const res = await request(url, {
                method: "POST",
                headers: getActiveHeaders(),
            });
            const data = await res.json();
            if (!res.ok) {
                showToast(`Erreur HTTP ${res.status}`, data.detail || "Action refusée", "error");
                return;
            }
            await syncAppStateCache();
            const summary = data.reason || data.message || data.status || "Opération effectuée";
            const toastType =
                typeof data.status === "string" && (data.status.includes("BLOCKED") || data.status.includes("CANCELLED") || data.status.includes("MIN_DELAY"))
                    ? "warning"
                    : "success";
            showToast("Opération terminée", summary, toastType);
            if (reloadAfter) {
                setTimeout(refresh, 950);
            }
        } catch (err) {
            showToast("Erreur réseau", String(err), "error");
        }
    };

    autoRestoreCachedStateIfNeeded();

    // --- 6. Modale de Paiement Simule ---
    const payDialog = document.getElementById("dialog-payment");
    const payForm = document.getElementById("form-payment");
    window.openPaymentModal = function (invoiceId, soldeRestant) {
        if (!payDialog) return;
        document.getElementById("pay-invoice-id").value = invoiceId;
        document.getElementById("pay-invoice-label").textContent = invoiceId;
        document.getElementById("pay-amount").value = soldeRestant;
        document.getElementById("pay-reference").value = `MOMO-SIM-${invoiceId}`;
        payDialog.showModal();
    };

    if (payForm) {
        payForm.addEventListener("submit", handleAsync(async () => {
            const payload = {
                invoice_id: document.getElementById("pay-invoice-id").value,
                montant: parseFloat(document.getElementById("pay-amount").value),
                reference_fictive: document.getElementById("pay-reference").value.trim(),
            };
            const res = await request("/api/payments/simulate", {
                method: "POST",
                headers: getActiveHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            payDialog.close();
            if (!res.ok) {
                showToast(`Erreur ${res.status}`, data.detail || "Paiement refusé", "error");
                return;
            }
            if (data.duplicate) {
                showToast("Doublon bloqué (Idempotence)", data.message, "warning");
            } else if (data.surpaiement_detecte) {
                showToast("Surpaiement détecté !", data.proposition_avoir, "warning");
            } else {
                showToast("Paiement enregistré", `Nouveau solde : ${data.solde_restant} FCFA (${data.statut_calcule})`, "success");
            }
            await syncAppStateCache();
            setTimeout(refresh, 1200);
        }));
    }

    // --- 7. Modale de Revue Humaine (4 yeux & edition du message) ---
    const revDialog = document.getElementById("dialog-review");
    const revForm = document.getElementById("form-review");
    window.openReviewModal = function (reminderId, invoiceId, currentDecision, currentMessage, creePar) {
        if (!revDialog) return;
        document.getElementById("rev-reminder-id").value = reminderId;
        document.getElementById("rev-title-id").textContent = `${reminderId} (${invoiceId})`;
        document.getElementById("rev-cree-par").textContent = creePar || "SYSTEME";
        document.getElementById("rev-decision").value = currentDecision;
        document.getElementById("rev-message").value = currentMessage;
        document.getElementById("rev-motif").value = "Vérifié et validé après contrôle du dossier";
        revDialog.showModal();
    };

    if (revForm) {
        revForm.addEventListener("submit", handleAsync(async () => {
            const reminderId = document.getElementById("rev-reminder-id").value;
            const payload = {
                decision: document.getElementById("rev-decision").value,
                motif: document.getElementById("rev-motif").value.trim(),
                nouveau_message: document.getElementById("rev-message").value.trim(),
            };
            const res = await request(`/api/review/${encodeURIComponent(reminderId)}`, {
                method: "POST",
                headers: getActiveHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            revDialog.close();
            if (!res.ok) {
                showToast(`Accès refusé (${res.status})`, data.detail || "Validation refusée", "error");
                return;
            }
            showToast(
                `Décision appliquée : ${data.statut_action}`,
                `Validé par ${data.valide_par || "opérateur"}`,
                "success"
            );
            await syncAppStateCache();
            setTimeout(refresh, 1000);
        }));
    }

    // --- 8. Interrupteur d'arret d'urgence (Kill-Switch) ---
    window.toggleEmergencyStop = handleAsync(async (activeState) => {
        const res = await request("/api/system/emergency-stop", {
            method: "POST",
            headers: getActiveHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                active: activeState,
                motif: activeState
                    ? "Activation arrêt d'urgence depuis le tableau de bord"
                    : "Levée de l'arrêt d'urgence depuis le tableau de bord",
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(`Accès refusé (${res.status})`, data.detail || "Réservé au rôle ADMIN", "error");
            return;
        }
        showToast(
            activeState ? "Arrêt d'urgence activé" : "Arrêt d'urgence levé",
            `Action exécutée par ${data.actor}`,
            activeState ? "warning" : "success"
        );
        setTimeout(refresh, 900);
    });

    // --- 9. Import de fichier Excel / CSV ---
    const uploadForm = document.getElementById("form-import-upload");
    if (uploadForm) {
        uploadForm.addEventListener("submit", handleAsync(async () => {
            const fileInput = document.getElementById("import-file-input");
            if (!fileInput || !fileInput.files.length) return;
            const formData = new FormData();
            formData.append("file", fileInput.files[0]);

            const res = await request("/api/import/upload", {
                method: "POST",
                headers: getActiveHeaders(),
                body: formData,
            });
            const data = await res.json();
            if (!res.ok) {
                showToast("Erreur d'import", data.detail || "Fichier invalide", "error");
                return;
            }
            await syncAppStateCache();
            showToast(
                `Lot ${data.batch_id} importé`,
                `Acceptées : ${data.total_accepted} | Rejetées : ${data.total_rejected} | Doublons : ${data.duplicate_count}`,
                data.total_rejected > 0 ? "warning" : "success"
            );
            storage("sessionStorage", "bwai_active_tab", "tab-portfolio");
            setTimeout(refresh, 1200);
        }));
    }

    // --- 10. Laboratoire de preuves (Audit SHA-256, Rejeu SHA-256, Simulateur IA) ---
    window.verifyAuditChainUi = handleAsync(async () => {
        const box = document.getElementById("proof-audit-output");
        const res = await request("/api/audit-logs/verify");
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Action refusée");
        if (box) {
            box.innerHTML = data.valid
                ? `<span class="badge badge-SOLDEE">Chaîne SHA-256 Intègre (${escapeHtml(data.checked_events)} événements vérifiés)</span><div style="margin-top:6px;">Head Hash : <code class="code-hash">${escapeHtml(data.head_hash)}</code></div>`
                : `<span class="badge badge-ECHUE">Alerte corruption : événement #${escapeHtml(data.corrupted_event_id)}</span>`;
        }
        showToast("Vérification SHA-256", `${escapeHtml(data.checked_events)} événements vérifiés (Valide : ${data.valid})`, data.valid ? "success" : "warning");
    });

    window.runDeterministicReplayUi = handleAsync(async () => {
        const box = document.getElementById("proof-replay-output");
        const res = await request("/api/system/replay", { method: "POST", headers: getActiveHeaders() });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Action refusée");
        if (box) {
            box.innerHTML = `<span class="badge badge-COUVERTE_PAR_ECHEANCIER">${escapeHtml(data.invoice_count)} factures recalculées à la date ${escapeHtml(data.reference_date)}</span><div style="margin-top:6px;">State Hash SHA-256 : <code class="code-hash">${escapeHtml(data.state_hash)}</code></div>`;
        }
        showToast("Rejeu déterministe", `Empreinte SHA-256 : ${data.state_hash.slice(0, 16)}...`, "success");
    });

    const aiTestForm = document.getElementById("form-ai-tester");
    if (aiTestForm) {
        aiTestForm.addEventListener("submit", handleAsync(async () => {
            const msg = document.getElementById("ai-test-message").value.trim();
            if (!msg) return;
            const res = await request("/api/ai/classify", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message_client: msg }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Analyse refusée");
            const out = document.getElementById("ai-test-output");
            if (out) {
                const revBadge = data.requiert_revue_humaine
                    ? `<span class="badge badge-EN_ATTENTE_VALIDATION">Revue Humaine Obligatoire</span>`
                    : `<span class="badge badge-SOLDEE">Envoi Automatique Autorisé</span>`;
                out.innerHTML = `
                    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px;">
                        <span class="badge badge-CONTESTEE">${escapeHtml(data.categorie)}</span>
                        <span class="pill-meta">Confiance : ${(data.confiance * 100).toFixed(1)}%</span>
                        ${revBadge}
                    </div>
                    <div style="font-size:0.8rem; color:var(--text-secondary); margin-top:6px;">
                        ${escapeHtml(data.motif_revue || "Aucun motif de blocage détecté.")}
                    </div>
                `;
            }
        }));
    }
})();

