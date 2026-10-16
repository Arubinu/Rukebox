async function refreshSecurityCard() {
  const result = await apiGet("/api/auth/status");
  const statusBox = document.getElementById("securityStatus");
  const currentRow = document.getElementById("currentPasswordRow");
  const hasPassword = result.ok && result.data.auth_required;
  currentRow.hidden = !hasPassword;
  setLogoutVisible(!!(hasPassword && result.data.authenticated));
  statusBox.textContent = hasPassword ? t("security.status_set") : t("security.status_none");
}

refreshSecurityCard();

/* Who may log in: the accounts, with what each of them may do. Only the owner
   sees this card (the server refuses the rest), and the role is what decides:
   see src/roles.py for the four of them. */
const ROLE_LABEL_KEYS = {
  owner: "people.role_owner",
  operator: "people.role_operator",
  member: "people.role_member",
};

function personRow(person) {
  const row = document.createElement("li");
  row.className = "device-row";
  row.dataset.account = person.id;
  const name = document.createElement("span");
  name.className = "device-name";
  name.textContent = person.name;
  const role = document.createElement("span");
  role.className = "device-badge";
  role.textContent = t(ROLE_LABEL_KEYS[person.role] || "people.role_member");
  const pass = document.createElement("span");
  pass.className = "device-note";
  pass.textContent = person.password ? "" : t("people.no_password");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-icon";
  remove.dataset.icon = "trash";
  remove.setAttribute("aria-label", t("people.remove"));
  remove.title = t("people.remove");
  remove.addEventListener("click", async () => {
    if (!(await showConfirm(t("people.confirm_remove", { name: person.name })))) return;
    const result = await apiDelete("/api/auth/accounts/" + encodeURIComponent(person.id));
    if (!result.ok) {
      showError(result.error);
      return;
    }
    refreshPeopleCard();
  });
  row.append(name, role, pass, remove);
  return row;
}

async function refreshPeopleCard() {
  const card = document.querySelector('.card[data-page="people"]');
  if (!card || !pageIsOffered(card)) return;
  const result = await apiGet("/api/auth/accounts");
  const list = document.getElementById("peopleList");
  if (!result.ok || !result.data) {
    list.replaceChildren();
    return;
  }
  const people = result.data.accounts || [];
  list.replaceChildren(...people.map(personRow));
  const anyOwnerWithoutPassword = people.some((one) => one.role === "owner" && !one.password);
  document.getElementById("peopleForm").querySelector(".help-text").hidden = !anyOwnerWithoutPassword;
}

refreshPeopleCard();

document.getElementById("peopleForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = await apiPost("/api/auth/accounts", {
    name: document.getElementById("personName").value,
    role: document.getElementById("personRole").value,
    password: document.getElementById("personPassword").value,
  });
  if (!result.ok) {
    const messages = {
      account_name_required: t("people.name_required"),
      account_name_taken: t("people.name_taken"),
      password_too_short: t("security.password_too_short"),
      account_password_required: t("people.owner_needs_none"),
    };
    showToast(t("common.failed"), messages[result.error] || errorLabel(result.error), { error: true });
    return;
  }
  document.getElementById("peopleForm").reset();
  refreshPeopleCard();
  showToast(t("people.added", { name: result.data.name }));
});

refreshSecurityCard();

document.getElementById("securityForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const currentPassword = document.getElementById("currentPassword").value;
  const newPassword = document.getElementById("newPassword").value;
  const result = await apiPost("/api/auth/set_password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
  if (!result.ok) {
    const messages = {
      wrong_current_password: t("security.wrong_current_password"),
      password_too_short: t("security.password_too_short"),
      too_many_attempts: t("login.locked", { s: result.retry_after || 0 }),
    };
    showToast(t("common.failed"), messages[result.error] || errorLabel(result.error), { error: true });
    return;
  }
  document.getElementById("securityForm").reset();
  refreshSecurityCard();
  showToast(result.password_set ? t("security.saved") : t("security.removed"));
});

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (h > 0) return h + " " + t("unit.hour") + " " + String(m).padStart(2, "0") + " " + t("unit.min");
  if (m > 0) return m + " " + t("unit.min");
  return total + " " + t("unit.sec");
}

function formatDateTime(epochSeconds) {
  if (!epochSeconds) return "—";
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

