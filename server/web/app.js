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

let isAdmin = false;
async function enter(user) {
  me = user;
  isAdmin = user.role === "admin";
  $("who").textContent = `${user.email}${user.role === "admin" ? " · administrador" : ""}`;
  if (user.must_change) { show("change"); $("chg-cur").focus(); return; }
  show("app");
  $("admin").hidden = user.role !== "admin";
  if (user.role === "admin") { loadUsers().catch(() => {}); loadCookies(); }
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
    const blocked = j.status === "error" && /YouTube bloque/.test(j.error || "");
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
    if (j.status === "done") {
      const q = el("button", { type: "button", className: "btn", textContent: j.sent ? "En cola para la Switch" : "Enviar a la Switch" });
      q.disabled = !!j.sent;
      q.addEventListener("click", async () => {
        q.disabled = true;
        try { await send("POST", `/api/jobs/${j.id}/send`); lastJobs = ""; refresh(); } catch (e) { q.disabled = false; alert(e.message); }
      });
      acts.append(q);
    }
    if (j.status === "done") {
      const ftp = el("button", { type: "button", className: "btn ghost", textContent: "Enviar por FTP" });
      ftp.addEventListener("click", async () => {
        if (!sw.ok) { alert("Conecte primero la Switch en la sección «Switch (FTP)»."); return; }
        ftp.disabled = true; ftp.textContent = "Enviando…";
        try {
          const r = await send("POST", `/api/jobs/${j.id}/ftp`, { host: sw.host, port: sw.port });
          alert(`Enviada a la Switch (${r.files} archivos).`);
        } catch (e) { sw.ok = false; swState(); alert(e.message); }
        ftp.disabled = false; ftp.textContent = "Enviar por FTP";
      });
      acts.append(ftp);
    }
    if (j.status === "error" && j.src_kind === "url") {
      const rt = el("button", { type: "button", className: "btn", textContent: "Reintentar" });
      rt.addEventListener("click", async () => { await send("POST", `/api/jobs/${j.id}/retry`).catch((e) => alert(e.message)); lastJobs = ""; refresh(); });
      acts.append(rt);
    }
    if (blocked && isAdmin) {
      const lb = el("label", { className: "btn ghost", textContent: "Iniciar sesión en YouTube (subir cookies)" });
      const fi = el("input", { type: "file", accept: ".txt,text/plain", hidden: true });
      lb.append(fi);
      fi.addEventListener("change", async () => {
        const f = fi.files[0];
        if (!f) return;
        try {
          await api("/api/admin/cookies", { method: "PUT", headers: { "Content-Type": "text/plain" }, body: await f.text() });
          await send("POST", `/api/jobs/${j.id}/retry`);
          loadCookies(); lastJobs = ""; refresh();
        } catch (e) { alert(e.message); }
      });
      acts.append(lb);
      const how = el("details", {});
      how.append(el("summary", { textContent: "¿Cómo obtener el cookies.txt?" }));
      how.append(el("p", { className: "hint", textContent: "Safari (Mac): 1) Inicie sesión en youtube.com en Safari. 2) Instale yt-dlp (brew install yt-dlp) y dé «Acceso total al disco» a la Terminal (Ajustes del Sistema > Privacidad y seguridad). 3) Ejecute: yt-dlp --cookies-from-browser safari --cookies cookies.txt --skip-download https://www.youtube.com 4) Suba el cookies.txt aquí. Chrome o Firefox: use la extensión «Get cookies.txt LOCALLY» en youtube.com y exporte. Se reintenta la descarga solo." }));
      li.append(how);
    }
    if (blocked && !isAdmin) li.append(el("span", { className: "hint", textContent: "Pida al administrador que inicie sesión en YouTube, o suba el audio directamente." }));
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
/* ---------- Switch por FTP ---------- */
const sw = { host: localStorage.getItem("shHost") || "", port: Number(localStorage.getItem("shPort")) || 5000, ok: false };
function swState() {
  const b = $("sw-state");
  b.textContent = sw.ok ? `Conectada · ${sw.host}:${sw.port}` : "Desconectada";
  b.className = `badge ${sw.ok ? "done" : "error"}`;
}
async function swConnect(e) {
  e?.preventDefault();
  const host = $("sw-host").value.trim().replace(/[,\s]/g, "."), port = Number($("sw-port").value) || 5000;
  const btn = $("sw-btn");
  btn.disabled = true; btn.textContent = "Conectando…"; $("sw-msg").textContent = "";
  try {
    const r = await send("POST", "/api/ftp/check", { host, port });
    Object.assign(sw, { host: r.host, port: r.port, ok: true });
    localStorage.setItem("shHost", r.host); localStorage.setItem("shPort", String(r.port));
  } catch (err) { sw.ok = false; $("sw-msg").textContent = err.message; }
  btn.disabled = false; btn.textContent = "Conectar"; swState();
}
$("sw-form").addEventListener("submit", swConnect);
$("sw-host").value = sw.host; $("sw-port").value = sw.port; swState();

/* ---------- cookies de YouTube (admin) ---------- */
async function loadCookies() {
  try {
    const r = await api("/api/admin/cookies");
    const b = $("ck-state");
    b.textContent = r.present ? `Cargadas (${new Date(r.modified * 1000).toLocaleDateString()})` : "Sin cookies";
    b.className = `badge ${r.present ? "done" : "error"}`;
    $("ck-del").hidden = !r.present;
  } catch { /* no es admin */ }
}
$("ck-file").addEventListener("change", async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  try {
    await api("/api/admin/cookies", { method: "PUT", headers: { "Content-Type": "text/plain" }, body: await f.text() });
    $("ck-msg").className = "msg ok"; $("ck-msg").textContent = "Cookies cargadas.";
  } catch (err) { $("ck-msg").className = "msg"; $("ck-msg").textContent = err.message; }
  loadCookies();
});
$("ck-del").addEventListener("click", async () => {
  if (!confirm("¿Quitar las cookies de YouTube?")) return;
  await send("DELETE", "/api/admin/cookies").catch((e) => alert(e.message));
  loadCookies();
});

/* ---------- Switch (app) y vinculación por QR ---------- */
async function swAppPoll() {
  if ($("app").hidden) return;
  try {
    const r = await api("/api/switch/status");
    const b = $("swapp-state");
    b.textContent = r.connected ? "Switch conectada" : "Switch no conectada";
    b.className = `badge ${r.connected ? "done" : "error"}`;
  } catch { /* sin sesión */ }
}
setInterval(swAppPoll, 8000); swAppPoll();
const pairCode = (new URLSearchParams(location.search).get("pair") || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
if (pairCode) {
  $("pair").hidden = false; $("pair-code").textContent = pairCode;
  $("pair-btn").addEventListener("click", async () => {
    $("pair-btn").disabled = true;
    try {
      await send("POST", "/api/switch/pair/confirm", { code: pairCode });
      $("pair-msg").className = "msg ok"; $("pair-msg").textContent = "Listo: la Switch quedó vinculada. Puede volver a ella.";
      history.replaceState(null, "", location.pathname);
    } catch (e) { $("pair-btn").disabled = false; $("pair-msg").className = "msg"; $("pair-msg").textContent = e.message; }
  });
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
