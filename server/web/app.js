"use strict";
const $ = (id) => document.getElementById(id);
const STAGES = { descarga: "Descargando", separacion: "Separando instrumentos", tempo: "Detectando tempo",
  transcripcion: "Transcribiendo notas", carta: "Generando carta", empaque: "Empaquetando", listo: "Listo", "": "En cola" };
const STATUS = { queued: "En cola", running: "En proceso", done: "Terminada", error: "Con error" };
let me = null, timer = null, file = null, lastJobs = "";

function el(tag, props = {}, ...kids) {
  const n = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => n.append(k));
  return n;
}
function say(id, text, ok) { const m = $(id); m.textContent = text; m.classList.toggle("ok", !!ok); }

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  const body = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith("/api/auth/")) { showAuth(); throw new Error("Sesión vencida"); }
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Error ${r.status}`);
  return body;
}
const send = (method, p, body) => api(p, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const post = (p, body) => send("POST", p, body);

/* ---------- vistas ---------- */
function show(view) {
  for (const id of ["auth", "change", "app", "bar"]) $(id).hidden = id !== view && !(id === "bar" && view !== "auth");
  if (view === "auth") clearInterval(timer);
}
function authForm(which) {
  for (const id of ["login-form", "recover-form", "invite-form"]) $(id).hidden = id !== which;
  const f = $(which).querySelector("input");
  if (f) f.focus();
}
function showAuth(form = "login-form") { me = null; show("auth"); authForm(form); }

async function enter(user) {
  me = user;
  $("who").textContent = `${user.email}${user.role === "admin" ? " · administrador" : ""}`;
  if (user.must_change) { show("change"); $("chg-cur").focus(); return; }
  show("app");
  $("admin").hidden = user.role !== "admin";
  if (user.role === "admin") loadUsers().catch(() => {});
  lastJobs = ""; refresh();
  clearInterval(timer); timer = setInterval(refresh, 3000);
}

/* ---------- autenticación ---------- */
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault(); say("login-msg", "");
  try {
    const u = await post("/api/auth/login", { email: $("login-email").value, password: $("login-pass").value });
    $("login-pass").value = ""; enter(u);
  } catch (err) { say("login-msg", err.message); }
});
$("to-recover").addEventListener("click", () => { $("rec-email").value = $("login-email").value; say("rec-msg", ""); authForm("recover-form"); });
$("to-login").addEventListener("click", () => authForm("login-form"));
$("recover-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post("/api/auth/recover", { email: $("rec-email").value });
    say("rec-msg", "Si el correo existe, recibirá una contraseña temporal en unos minutos.", true);
  } catch (err) { say("rec-msg", err.message); }
});
$("logout").addEventListener("click", async () => { await post("/api/auth/logout").catch(() => {}); showAuth(); });
document.querySelectorAll(".toggle").forEach((b) => b.addEventListener("click", () => {
  const i = $(b.dataset.target), show = i.type === "password";
  i.type = show ? "text" : "password"; b.textContent = show ? "Ocultar" : "Mostrar"; b.setAttribute("aria-pressed", String(show));
}));
$("change-form").addEventListener("submit", async (e) => {
  e.preventDefault(); say("chg-msg", "");
  try {
    const u = await post("/api/auth/password", { current: $("chg-cur").value, new: $("chg-new").value });
    e.target.reset(); enter(u);
  } catch (err) { say("chg-msg", err.message); }
});
const inviteToken = new URLSearchParams(location.search).get("invite");
$("invite-form").addEventListener("submit", async (e) => {
  e.preventDefault(); say("invite-msg", "");
  try {
    await post(`/api/auth/invite/${encodeURIComponent(inviteToken)}`, { name: $("invite-name").value, password: $("invite-pass").value });
    history.replaceState(null, "", "/"); showAuth(); say("login-msg", "Cuenta creada. Inicie sesión.", true);
  } catch (err) { say("invite-msg", err.message); }
});

/* ---------- administración ---------- */
async function loadUsers() {
  const ul = $("users"); ul.replaceChildren();
  for (const u of await api("/api/admin/users")) {
    const state = [u.role === "admin" ? "administrador" : "usuario", u.active ? "" : "desactivado", u.has_password ? "" : "invitación pendiente"].filter(Boolean).join(" · ");
    const li = el("li", { className: "user" }, el("span", { textContent: `${u.email} — ${state}` }));
    if (u.id !== me.id) {
      const tog = el("button", { type: "button", className: "btn ghost", textContent: u.active ? "Desactivar" : "Activar" });
      tog.addEventListener("click", async () => { await send("PATCH", `/api/admin/users/${u.id}`, { active: !u.active }).catch((e) => say("adm-msg", e.message)); loadUsers(); });
      const del = el("button", { type: "button", className: "btn danger", textContent: "Eliminar" });
      del.addEventListener("click", async () => { if (confirm(`¿Eliminar a ${u.email}?`)) { await send("DELETE", `/api/admin/users/${u.id}`).catch((e) => say("adm-msg", e.message)); loadUsers(); } });
      li.append(el("span", { className: "actions" }, tog, del));
    }
    ul.append(li);
  }
}
$("admin-invite").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await post("/api/admin/invites", { email: $("adm-email").value });
    say("adm-msg", r.emailed ? `Invitación enviada a ${r.email}.` : `No se pudo enviar el correo. Enlace para compartir: ${r.link}`, r.emailed);
    $("adm-email").value = ""; loadUsers();
  } catch (err) { say("adm-msg", err.message); }
});
$("admin-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await post("/api/admin/users", { email: $("adm-cemail").value, password: $("adm-cpass").value }); say("adm-msg", "Usuario creado.", true); e.target.reset(); loadUsers(); }
  catch (err) { say("adm-msg", err.message); }
});

/* ---------- nueva canción ---------- */
function setFile(f) {
  file = f;
  $("drop-label").textContent = f ? `Archivo: ${f.name}` : "Arrastre un archivo de audio o pulse para elegirlo";
}
const drop = $("drop");
drop.addEventListener("click", () => $("file").click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
$("file").addEventListener("change", (e) => setFile(e.target.files[0] || null));
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0] || null));

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("url").value.trim();
  if (!file && !url) return say("msg", "Elija un archivo o pegue una URL.");
  if (file && url) return say("msg", "Use solo uno: archivo o URL.");
  const fd = new FormData();
  if (file) fd.append("file", file); else fd.append("url", url);
  fd.append("title", $("title").value); fd.append("artist", $("artist").value);
  $("submit").disabled = true; say("msg", "Enviando…");
  try {
    await api("/api/jobs", { method: "POST", body: fd });
    say("msg", "Trabajo en cola.", true);
    e.target.reset(); setFile(null); lastJobs = ""; refresh();
  } catch (err) { say("msg", err.message); }
  finally { $("submit").disabled = false; }
});

/* ---------- lista de canciones ---------- */
function render(list) {
  const ul = $("jobs"); ul.replaceChildren();
  if (!list.length) { ul.append(el("li", { className: "empty", textContent: "Aún no hay canciones." })); return; }
  for (const j of list) {
    const li = el("li", { className: "item" });
    li.append(el("h3", { textContent: `${j.artist || "—"} · ${j.title || "(sin título)"}` }));
    li.append(el("span", { className: `badge ${j.status}`, textContent: STATUS[j.status] }));
    if (j.status === "queued" || j.status === "running") {
      li.append(el("span", { textContent: `${STAGES[j.stage] ?? j.stage} (${j.progress}%)` }));
      li.append(el("progress", { max: 100, value: j.progress, ariaLabel: "Progreso" }));
    }
    if (j.status === "error") li.append(el("span", { className: "msg", textContent: j.error }));
    if (j.status === "done") {
      const inf = j.info || {};
      li.append(el("span", { textContent: `Instrumentos: ${(inf.instruments || []).join(", ") || "—"}` }));
      li.append(el("span", { textContent: `Dificultades: ${(inf.difficulties || []).join(", ")}` }));
    }
    const acts = el("div", { className: "actions" });
    if (j.status === "done") acts.append(el("a", { className: "btn", href: `/api/jobs/${j.id}/zip`, textContent: "Descargar ZIP" }));
    if (j.status === "done" && window.showDirectoryPicker) {
      const sv = el("button", { type: "button", className: "btn ghost", textContent: "Guardar en carpeta de canciones" });
      sv.addEventListener("click", () => saveToFolder(j, sv));
      acts.append(sv);
    }
    if (j.status !== "running") {
      const del = el("button", { type: "button", className: "btn danger", textContent: "Eliminar" });
      del.addEventListener("click", async () => {
        if (!confirm("¿Eliminar esta canción?")) return;
        await send("DELETE", `/api/jobs/${j.id}`).catch((e) => alert(e.message));
        lastJobs = ""; refresh();
      });
      acts.append(del);
    }
    li.append(acts);
    ul.append(li);
  }
}
async function refresh() {
  try {
    const list = await api("/api/jobs");
    const key = JSON.stringify(list);
    if (key !== lastJobs) { lastJobs = key; render(list); }     // no reconstruye si nada cambió (conserva el foco)
  } catch { /* se reintenta en el siguiente ciclo */ }
}

async function saveToFolder(j, btn) {
  const label = btn.textContent;
  try {
    const root = await window.showDirectoryPicker({ mode: "readwrite" });
    const dir = await root.getDirectoryHandle(j.folder, { create: true });
    const s = (await api("/api/switch/songs")).find((x) => x.id === j.id);
    if (!s) throw new Error("La canción ya no está disponible");
    btn.disabled = true;
    for (const f of s.files) {
      btn.textContent = `Guardando ${f.name}…`;
      const r = await fetch(`/api/switch/songs/${j.id}/${encodeURIComponent(f.name)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(`Error ${r.status} en ${f.name}`);
      const w = await (await dir.getFileHandle(f.name, { create: true })).createWritable();
      await r.body.pipeTo(w);
    }
    btn.textContent = "Guardada ✓";
    setTimeout(() => { btn.textContent = label; }, 4000);
  } catch (err) {
    if (err.name !== "AbortError") alert(err.message);
    btn.textContent = label;
  } finally { btn.disabled = false; }
}

/* ---------- arranque ---------- */
(async () => {
  if (inviteToken) {
    try {
      const i = await api(`/api/auth/invite/${encodeURIComponent(inviteToken)}`);
      $("invite-email").textContent = `Correo: ${i.email}`; showAuth("invite-form"); return;
    } catch { history.replaceState(null, "", "/"); }
  }
  try { enter(await api("/api/auth/me")); } catch { showAuth(); }
})();
