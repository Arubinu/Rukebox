const networkButtonList = document.getElementById("networkButtonList");

async function renameNetworkButton(button) {
  const box = document.createElement("div");
  box.className = "prepared-dialog";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text-input";
  input.maxLength = 40;
  input.value = button.name || "";
  input.setAttribute("aria-label", t("netbutton.name"));
  box.appendChild(input);
  const answer = openModal({ title: t("netbutton.rename"), bodyNode: box, confirm: true });
  input.focus();
  if (!(await answer)) return;
  const r = await apiPost("/api/network_buttons/" + encodeURIComponent(button.id), { name: input.value });
  if (!r.ok) showError(r.error);
  refreshNetworkButtons();
}

function networkButtonIcon(icon, label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn-icon";
  b.dataset.icon = icon;
  b.title = label;
  b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}

function networkButtonRow(button) {
  const li = document.createElement("li");
  const label = document.createElement("span");
  const name = document.createElement("span");
  name.className = "device-name";
  name.textContent = button.name || "";
  const sub = document.createElement("span");
  sub.className = "device-sub";
  const address = document.createElement("span");
  address.className = "device-mac";
  address.textContent = button.ip || "";
  const states = document.createElement("span");
  states.className = "device-state";
  const state = document.createElement("span");
  state.className = button.approved ? "badge connected" : "badge";
  state.textContent = t(button.approved ? "netbutton.approved" : "netbutton.waiting");
  states.appendChild(state);
  sub.append(address, states);
  label.append(name, sub);
  if (button.seen) {
    const seen = document.createElement("span");
    seen.className = "device-sub";
    seen.textContent = t("netbutton.seen", { time: new Date(button.seen * 1000).toLocaleTimeString(currentLang) });
    label.appendChild(seen);
  }
  const actions = document.createElement("span");
  actions.className = "device-actions";
  const left = [];
  if (!button.approved) {
    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "btn";
    approve.textContent = t("netbutton.approve");
    approve.addEventListener("click", async () => {
      const r = await apiPost("/api/network_buttons/" + encodeURIComponent(button.id), { approved: true });
      if (!r.ok) showError(r.error);
      refreshNetworkButtons();
    });
    left.push(approve);
  }
  const rename = networkButtonIcon("pencil", t("netbutton.rename"), () => renameNetworkButton(button));
  const remove = networkButtonIcon("x", t("netbutton.remove", { name: button.name || "" }), async () => {
    if (!await showConfirm(t("netbutton.remove_confirm", { name: button.name || "" }))) return;
    const r = await apiDelete("/api/network_buttons/" + encodeURIComponent(button.id));
    if (!r.ok) showError(r.error);
    refreshNetworkButtons();
  });
  splitActions(actions, left, [rename, remove]);
  li.append(label, actions);
  return li;
}

async function refreshNetworkButtons() {
  const result = await apiGet("/api/network_buttons");
  if (!result.ok || !result.data) return;
  const buttons = result.data.buttons || [];
  networkButtonList.replaceChildren(...buttons.map(networkButtonRow));
  networkButtonList.hidden = !buttons.length;
  document.getElementById("networkButtonEmpty").hidden = buttons.length > 0;
}

// A part this late registers after the page's first arrival: it reads once on its own.
refreshEvery(refreshNetworkButtons, 5000, ["settings/buttons"]);
refreshNetworkButtons();
