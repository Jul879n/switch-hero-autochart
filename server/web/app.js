const $ = (id) => document.getElementById(id);
const STAGES = { descarga: "Descargando", separacion: "Separando instrumentos", tempo: "Detectando tempo",
  transcripcion: "Transcribiendo notas", carta: "Generando carta", empaque: "Empaquetando", listo: "Listo", "": "En cola" };
const STATUS = { queued: "En cola", running: "En proceso", done: "Terminada", error: "Con error" };
let file = null;

function setFile(f) {
  file = f;
  $("drop-label").textContent = f ? `Archivo: ${f.name}` : "Arrastre un archivo de audio o pulse para elegirlo";
}
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (r.status === 401 && path !== "/api/auth/login") { showAuth(); throw new Error("Sesión vencida"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
  return r.json();
}
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
let me = null, timer = null;

function showAuth(invite) {
  clearInterval(timer); me = null;
  ["form", "bar", "admin", "songs-h", "jobs"].forEach((i) => ($(i).hidden = true));
  $("auth").hidden = false;
  $("login-form").hidden = !!invite; $("invite-form").hidden = !invite;
}
async function showApp(user) {
  me = user;
  $("auth").hidden = true;
  ["form", "bar", "songs-h", "jobs"].forEach((i) => ($(i).hidden = false));
  $("who").textContent = `${user.email}${user.role === "admin" ? " (administrador)" : ""}`;
  $("admin").hidden = user.role !== "admin";
  if (user.role === "admin") loadUsers();
  refresh(); clearInterval(timer); timer = setInterval(refresh, 3000);
}
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { showApp(await post("/api/auth/login", { email: $("login-email").value, password: $("login-pass").value })); $("login-pass").value = ""; }
  catch (err) { $("login-msg").textContent = err.message; }
});
$("logout").addEventListener("click", async () => { await post("/api/auth/logout", {}).catch(() => {}); showAuth(); });
$("invite-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post(`/api/auth/invite/${inviteToken}`, { name: $("invite-name").value, password: $("invite-pass").value });
    history.replaceState(null, "", "/"); showAuth(); $("login-msg").textContent = "Cuenta creada. Inicie sesión.";
  } catch (err) { $("invite-msg").textContent = err.message; }
});

function amsg(t, err) { const m = $("adm-msg"); m.textContent = t; m.style.color = err ? "" : "var(--ok)"; }
async function loadUsers() {
  const list = await api("/api/admin/users");
  const ul = $("users"); ul.replaceChildren();
  for (const u of list) {
    const li = el("li", { className: "user" }, el("span", { textContent: `${u.email} · ${u.role === "admin" ? "admin" : "usuario"}${u.active ? "" : " · desactivado"}${u.has_password ? "" : " · invitación pendiente"}` }));
    if (u.id !== me.id) {
      const tog = el("button", { type: "button", className: "secondary", textContent: u.active ? "Desactivar" : "Activar" });
      tog.addEventListener("click", async () => { await api(`/api/admin/users/${u.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !u.active }) }); loadUsers(); });
      const del = el("button", { type: "button", className: "secondary", textContent: "Eliminar" });
      del.addEventListener("click", async () => { if (confirm(`¿Eliminar a ${u.email}?`)) { await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); loadUsers(); } });
      li.append(tog, del);
    }
    ul.append(li);
  }
}
$("admin-invite").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await post("/api/admin/invites", { email: $("adm-email").value });
    amsg(r.emailed ? `Invitación enviada a ${r.email}.` : `No se pudo enviar el correo. Enlace para compartir: ${r.link}`, !r.emailed);
    $("adm-email").value = ""; loadUsers();
  } catch (err) { amsg(err.message, true); }
});
$("admin-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await post("/api/admin/users", { email: $("adm-cemail").value, password: $("adm-cpass").value }); amsg("Usuario creado."); e.target.reset(); loadUsers(); }
  catch (err) { amsg(err.message, true); }
});

const inviteToken = new URLSearchParams(location.search).get("invite");
(async () => {
  if (inviteToken) {
    try { const i = await api(`/api/auth/invite/${inviteToken}`); $("invite-email").textContent = `Correo: ${i.email}`; showAuth(true); return; }
    catch { history.replaceState(null, "", "/"); }
  }
  try { showApp(await api("/api/auth/me")); } catch { showAuth(); }
})();

const drop = $("drop");
drop.addEventListener("click", () => $("file").click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
$("file").addEventListener("change", (e) => setFile(e.target.files[0] || null));
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0] || null));

function say(text, err) { const m = $("msg"); m.textContent = text; m.className = err ? "err" : ""; }

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("url").value.trim();
  if (!file && !url) return say("Elija un archivo o pegue una URL.", true);
  if (file && url) return say("Use solo uno: archivo o URL.", true);
  const fd = new FormData();
  if (file) fd.append("file", file); else fd.append("url", url);
  fd.append("title", $("title").value); fd.append("artist", $("artist").value);
  $("submit").disabled = true; say("Enviando…");
  try {
    const r = await fetch("/api/jobs", { method: "POST", body: fd, credentials: "same-origin" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
    say("Trabajo en cola.");
    $("form").reset(); setFile(null);
    refresh();
  } catch (err) { say(err.message, true); }
  finally { $("submit").disabled = false; }
});

function el(tag, props = {}, ...kids) {
  const n = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => n.append(k));
  return n;
}

function render(list) {
  const ul = $("jobs");
  ul.replaceChildren();
  if (!list.length) { ul.append(el("li", { textContent: "Aún no hay canciones." })); return; }
  for (const j of list) {
    const li = el("li", { className: "job" });
    li.append(el("h3", { textContent: `${j.artist || "—"} · ${j.title || "(sin título)"}` }));
    li.append(el("span", { className: `badge ${j.status}`, textContent: STATUS[j.status] }));
    if (j.status === "queued" || j.status === "running") {
      li.append(el("span", { textContent: `${STAGES[j.stage] ?? j.stage} (${j.progress}%)` }));
      li.append(el("progress", { max: 100, value: j.progress, ariaLabel: "Progreso" }));
    }
    if (j.status === "error") li.append(el("span", { textContent: j.error }));
    if (j.status === "done") {
      const inf = j.info || {};
      li.append(el("span", { textContent: `Instrumentos: ${(inf.instruments || []).join(", ") || "—"}` }));
      li.append(el("span", { textContent: `Dificultades: ${(inf.difficulties || []).join(", ")}` }));
    }
    const acts = el("div", { className: "actions" });
    if (j.status === "done") acts.append(el("a", { className: "btn", href: `/api/jobs/${j.id}/zip`, textContent: "Descargar ZIP" }));
    if (j.status === "done" && window.showDirectoryPicker) {
      const sv = el("button", { type: "button", className: "secondary", textContent: "Guardar en carpeta de canciones" });
      sv.addEventListener("click", () => saveToFolder(j, sv));
      acts.append(sv);
    }
    if (j.status !== "running") {
      const del = el("button", { type: "button", className: "secondary", textContent: "Eliminar" });
      del.addEventListener("click", async () => {
        if (!confirm("¿Eliminar esta canción?")) return;
        await fetch(`/api/jobs/${j.id}`, { method: "DELETE" });
        refresh();
      });
      acts.append(del);
    }
    li.append(acts);
    ul.append(li);
  }
}

async function refresh() {
  try {
    render(await api("/api/jobs"));
  } catch { /* se reintenta en el siguiente ciclo */ }
}

async function saveToFolder(j, btn) {
  try {
    const root = await window.showDirectoryPicker({ mode: "readwrite" });
    const dir = await root.getDirectoryHandle(j.folder, { create: true });
    const songs = await api("/api/switch/songs");
    const s = songs.find((x) => x.id === j.id);
    btn.disabled = true;
    for (const f of s.files) {
      btn.textContent = `Guardando ${f.name}…`;
      const r = await fetch(`/api/switch/songs/${j.id}/${encodeURIComponent(f.name)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(`Error ${r.status} en ${f.name}`);
      const w = await (await dir.getFileHandle(f.name, { create: true })).createWritable();
      await r.body.pipeTo(w);
    }
    btn.textContent = "Guardada ✓";
  } catch (err) {
    if (err.name !== "AbortError") alert(err.message);
    btn.textContent = "Guardar en carpeta de canciones";
  } finally { btn.disabled = false; }
}
