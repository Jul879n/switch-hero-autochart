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

function toast(text, ok = true) {
  const t = el("p", { className: ok ? "toast" : "toast err", textContent: text });
  $("toasts").append(t); setTimeout(() => t.remove(), ok ? 5000 : 8000);
}
/* Deshabilita el botón y muestra «Enviando…» mientras dura la petición. */
async function busy(btn, fn, label = "Enviando…") {
  const prev = btn.textContent; btn.disabled = true; btn.textContent = label;
  try { return await fn(); } finally { btn.disabled = false; btn.textContent = prev; }
}
function fieldErr(input, text) {
  $(`${input.id}-err`).textContent = text || "";
  input.toggleAttribute("aria-invalid", !!text); if (text) input.setAttribute("aria-invalid", "true");
  return !text;
}
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const checkEmail = (i) => fieldErr(i, EMAIL_RE.test(i.value.trim()) ? "" : "Ingrese un correo válido.");
function confirmDialog(title, text, yes = "Eliminar") {
  $("confirm-h").textContent = title; $("confirm-text").textContent = text; $("confirm-yes").textContent = yes;
  const d = $("confirm");
  return new Promise((res) => {
    const done = (v) => { d.close(); $("confirm-yes").onclick = $("confirm-no").onclick = d.onclose = null; res(v); };
    $("confirm-yes").onclick = () => done(true); $("confirm-no").onclick = () => done(false);
    d.onclose = () => res(false);
    d.showModal(); $("confirm-no").focus();
  });
}

/* View Transitions: anima el cambio de DOM si el navegador lo soporta y el usuario no pidió menos movimiento. */
function transition(update) {
  if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) { update(); return Promise.resolve(); }
  const vt = document.startViewTransition(update);   // el callback corre de forma asíncrona
  vt.ready.catch(() => {}); vt.finished.catch(() => {});   // una transición interrumpida por otra rechaza con AbortError
  return vt.updateCallbackDone;
}

/* ---------- pestañas ---------- */
const TABS = [["tab-songs", "p-songs"], ["tab-switch", "p-switch"], ["tab-admin", "admin"]];
function selectTab(id, focus) {
  transition(() => {
    for (const [t, p] of TABS) {
      const on = t === id;
      $(t).setAttribute("aria-selected", String(on)); $(t).tabIndex = on ? 0 : -1; $(p).hidden = !on;
    }
  });
  if (focus) $(id).focus();
  try { localStorage.setItem("tab", id); } catch {}
}
$("tab-songs").parentElement.addEventListener("keydown", (e) => {
  const ids = TABS.map(([t]) => t).filter((t) => !$(t).hidden), i = ids.indexOf(document.activeElement.id);
  if (i < 0) return;
  const to = { ArrowRight: ids[(i + 1) % ids.length], ArrowLeft: ids[(i - 1 + ids.length) % ids.length], Home: ids[0], End: ids[ids.length - 1] }[e.key];
  if (to) { e.preventDefault(); selectTab(to, true); }
});
TABS.forEach(([t]) => $(t).addEventListener("click", () => selectTab(t)));

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  const body = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith("/api/auth/")) { showAuth(); throw new Error("Sesión vencida"); }
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Error ${r.status}`);
  return body;
}
const send = (method, p, body) => api(p, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const post = (p, body) => send("POST", p, body);

/* ---------- combobox (MusicBrainz vía /api/suggest) ---------- */
function combo(input, kind, extra) {
  const list = $(`${input.id}-list`);
  let items = [], meta = [], idx = -1, t = null, seq = 0;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); idx = -1; input.removeAttribute("aria-activedescendant"); };
  const pick = (v) => { input.value = v; close(); input.dispatchEvent(new Event("change")); input.dispatchEvent(new Event("pick")); };
  const opts = () => [...list.querySelectorAll("[role=option]")];
  const mark = () => opts().forEach((li, i) => {
    li.setAttribute("aria-selected", String(i === idx));
    if (i === idx) input.setAttribute("aria-activedescendant", li.id);
  });
  function render(q, loading) {
    list.replaceChildren();
    if (loading) list.append(el("li", { className: "status", textContent: "Buscando…", ariaDisabled: "true" }));
    const rows = [...items, ...(items.some((x) => x.toLowerCase() === q.toLowerCase()) ? [] : [`Usar «${q}»`])];
    rows.forEach((text, i) => {
      const isNew = i === items.length && !items.some((x) => x.toLowerCase() === q.toLowerCase());
      const li = el("li", { id: `${input.id}-o${i}`, role: "option", textContent: text, className: isNew ? "new" : "" });
      if (!isNew && meta[i]) li.append(el("span", { className: "dur", textContent: meta[i] }));
      li.setAttribute("aria-selected", "false");
      li.addEventListener("mousedown", (e) => { e.preventDefault(); pick(isNew ? q : items[i]); });
      list.append(li);
    });
    list.hidden = !rows.length; input.setAttribute("aria-expanded", String(!list.hidden)); idx = -1;
  }
  input.addEventListener("input", () => {
    clearTimeout(t);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    items = []; meta = []; render(q, true);          // la lista aparece al instante; las sugerencias llegan después
    t = setTimeout(async () => {
      const mine = ++seq;
      const p = new URLSearchParams({ kind, q }); if (extra) p.set("artist", extra());
      const r = await api(`/api/suggest?${p}`).catch(() => ({ items: [] }));
      if (mine !== seq || document.activeElement !== input) return;
      items = r.items || []; meta = r.meta || []; render(q);
    }, 350);
  });
  input.addEventListener("keydown", (e) => {
    const n = opts().length;
    if (list.hidden || !n) return;
    if (e.key === "ArrowDown") { e.preventDefault(); idx = (idx + 1) % n; mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); idx = (idx - 1 + n) % n; mark(); }
    else if (e.key === "Enter" && idx >= 0) { e.preventDefault(); opts()[idx].dispatchEvent(new MouseEvent("mousedown")); }
    else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", close);
}

/* ---------- vistas ---------- */
function show(view) {
  const done = transition(() => {
    for (const id of ["auth", "change", "app", "bar"]) $(id).hidden = id !== view && !(id === "bar" && view !== "auth");
    $("proj-nav").hidden = view !== "app";
  });
  if (view === "auth") clearInterval(timer);
  return done;
}
function authForm(which) {
  transition(() => { for (const id of ["login-form", "recover-form", "invite-form"]) $(id).hidden = id !== which; })
    .then(() => { const f = $(which).querySelector("input"); if (f) f.focus(); });
}
function showAuth(form = "login-form") { me = null; show("auth").then(() => authForm(form)); }

let isAdmin = false;
async function enter(user) {
  me = user;
  isAdmin = user.role === "admin";
  $("who").textContent = `${user.email}${user.role === "admin" ? " · administrador" : ""}`;
  if (user.must_change) { await show("change"); $("chg-cur").focus(); return; }
  await show("app");
  $("tab-admin").hidden = !isAdmin;
  let saved = "tab-songs"; try { saved = localStorage.getItem("tab") || saved; } catch {}
  selectTab(!isAdmin && saved === "tab-admin" ? "tab-songs" : saved);
  if (!$("pair").hidden) selectTab("tab-switch");
  if (user.role === "admin") { loadUsers().catch(() => {}); loadCookies(); }
  lastJobs = ""; refresh();
  clearInterval(timer); timer = setInterval(refresh, 3000);
}

/* ---------- autenticación ---------- */
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault(); say("login-msg", "");
  if (!checkEmail($("login-email"))) return $("login-email").focus();
  try {
    const u = await busy(e.submitter, () => post("/api/auth/login", { email: $("login-email").value, password: $("login-pass").value }), "Entrando…");
    $("login-pass").value = ""; enter(u);
  } catch (err) { say("login-msg", err.message); }
});
$("to-recover").addEventListener("click", () => { $("rec-email").value = $("login-email").value; say("rec-msg", ""); authForm("recover-form"); });
$("to-login").addEventListener("click", () => authForm("login-form"));
$("recover-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!checkEmail($("rec-email"))) return $("rec-email").focus();
  try {
    await busy(e.submitter, () => post("/api/auth/recover", { email: $("rec-email").value }));
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
  if (!fieldErr($("chg-new"), $("chg-new").value.length >= 10 ? "" : "Use al menos 10 caracteres.")) return $("chg-new").focus();
  try {
    const u = await busy(e.submitter, () => post("/api/auth/password", { current: $("chg-cur").value, new: $("chg-new").value }));
    e.target.reset(); enter(u);
  } catch (err) { say("chg-msg", err.message); }
});
const inviteToken = new URLSearchParams(location.search).get("invite");
$("invite-form").addEventListener("submit", async (e) => {
  e.preventDefault(); say("invite-msg", "");
  try {
    await busy(e.submitter, () => post(`/api/auth/invite/${encodeURIComponent(inviteToken)}`, { name: $("invite-name").value, password: $("invite-pass").value }));
    history.replaceState(null, "", "/"); showAuth(); say("login-msg", "Cuenta creada. Inicie sesión.", true);
  } catch (err) { say("invite-msg", err.message); }
});

/* ---------- administración ---------- */
async function loadUsers() {
  const ul = $("users"); ul.replaceChildren();
  const users = await api("/api/admin/users");
  $("users-empty").hidden = users.length > 1;
  const act = (btn, fn, okMsg) => btn.addEventListener("click", async () => {
    try { await busy(btn, fn, "…"); toast(okMsg); } catch (err) { toast(err.message, false); }
    loadUsers();
  });
  for (const u of users) {
    const chips = el("div", { className: "chips" },
      el("span", { className: "chip", textContent: u.role === "admin" ? "administrador" : "usuario" }),
      el("span", { className: u.active ? "chip ok" : "chip off", textContent: u.active ? "activo" : "desactivado" }));
    if (u.must_change) chips.append(el("span", { className: "chip warn", textContent: "debe cambiar contraseña" }));
    else if (!u.has_password) chips.append(el("span", { className: "chip warn", textContent: "invitación pendiente" }));
    const li = el("li", { className: "user" }, el("strong", { textContent: u.email }));
    if (u.name) li.append(el("span", { className: "hint", textContent: u.name }));
    li.append(chips);
    if (u.id !== me.id) {
      const mk = (cls, text) => el("button", { type: "button", className: `btn ${cls}`, textContent: text });
      const rst = mk("ghost", "Reenviar contraseña"), rol = mk("ghost", u.role === "admin" ? "Hacer usuario" : "Hacer administrador");
      const tog = mk("ghost", u.active ? "Desactivar" : "Activar"), del = mk("danger", "Eliminar");
      act(rst, () => send("POST", `/api/admin/users/${u.id}/reset`), `Contraseña temporal enviada a ${u.email}.`);
      act(rol, () => send("PATCH", `/api/admin/users/${u.id}`, { role: u.role === "admin" ? "user" : "admin" }), "Rol actualizado.");
      act(tog, () => send("PATCH", `/api/admin/users/${u.id}`, { active: !u.active }), u.active ? "Usuario desactivado." : "Usuario activado.");
      del.addEventListener("click", async () => {
        if (!await confirmDialog(`¿Eliminar a ${u.email}?`, "Se borrarán su acceso y sus sesiones. No se puede deshacer.")) return;
        try { await send("DELETE", `/api/admin/users/${u.id}`); toast("Usuario eliminado."); } catch (err) { toast(err.message, false); }
        loadUsers();
      });
      li.append(el("div", { className: "actions" }, rst, rol, tog, del));
    }
    ul.append(li);
  }
}
$("adm-cemail").addEventListener("input", () => fieldErr($("adm-cemail"), ""));
$("admin-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!checkEmail($("adm-cemail"))) return $("adm-cemail").focus();
  try {
    await busy(e.submitter, () => post("/api/admin/users", { email: $("adm-cemail").value, name: $("adm-cname").value, role: $("adm-crole").value }));
    toast(`Usuario creado; contraseña temporal enviada a ${$("adm-cemail").value.trim()}.`); e.target.reset(); loadUsers();
  } catch (err) { toast(err.message, false); }
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

/* ---------- buscador (SoundCloud / YouTube) ---------- */
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("search-q").value.trim();
  if (q.length < 2) return say("search-msg", "Escriba al menos 2 letras.");
  const ul = $("search-results"); ul.replaceChildren();
  $("search-btn").disabled = true; say("search-msg", "Buscando…");
  try {
    const source = $("search-src").value;
    const { items } = await api(`/api/search?${new URLSearchParams({ q, source })}`);
    say("search-msg", items.length ? `${items.length} resultados` : "Sin resultados.", !!items.length);
    for (const it of items) {
      const li = el("li", { className: "result" });
      li.append(el("img", { src: it.thumb, alt: "", loading: "lazy" }));
      const info = el("div", {});
      info.append(el("h3", { textContent: it.name }), el("div", { className: "meta", textContent: `${it.channel} · ${mmss(it.duration)}` }));
      const go = el("button", { type: "button", className: "btn", textContent: "Generar carta" });
      go.setAttribute("aria-label", `Generar carta de ${it.name}`);
      go.addEventListener("click", async () => {
        const fd = new FormData();
        fd.append("url", it.url); fd.append("title", it.title); fd.append("artist", it.artist);
        go.disabled = true; go.textContent = "Enviando…";
        try { await api("/api/jobs", { method: "POST", body: fd }); go.textContent = "En cola ✓"; lastJobs = ""; refresh(); }
        catch (err) { go.disabled = false; go.textContent = "Generar carta"; alert(err.message); }
      });
      const acts = el("div", { className: "actions" }); acts.append(go); info.append(acts);
      li.append(info); ul.append(li);
    }
  } catch (err) { say("search-msg", err.message); }
  finally { $("search-btn").disabled = false; }
});

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
    if (j.status === "running" && (j.idle || 0) > 90) {
      li.append(el("span", { className: "msg", textContent: `Parece detenido: sin avance hace ${Math.round(j.idle / 60 * 10) / 10} min.` }));
      const go = el("button", { type: "button", className: "btn", textContent: "Continuar" });
      go.title = "Reinicia el paso actual reutilizando lo ya generado";
      go.addEventListener("click", async () => { go.disabled = true; await send("POST", `/api/jobs/${j.id}/resume`).catch((e) => alert(e.message)); lastJobs = ""; refresh(); });
      acts.append(go);
    }
    if (j.status === "queued" || j.status === "running") {
      const cn = el("button", { type: "button", className: "btn danger", textContent: "Cancelar" });
      cn.addEventListener("click", async () => {
        if (!confirm("¿Cancelar la creación de esta canción?")) return;
        cn.disabled = true; await send("POST", `/api/jobs/${j.id}/cancel`).catch((e) => alert(e.message)); lastJobs = ""; refresh();
      });
      acts.append(cn);
    }
    if (j.status === "error" && j.error === "Cancelado por el usuario") {
      const go = el("button", { type: "button", className: "btn", textContent: "Continuar" });
      go.addEventListener("click", async () => { go.disabled = true; await send("POST", `/api/jobs/${j.id}/resume`).catch((e) => alert(e.message)); lastJobs = ""; refresh(); });
      acts.append(go);
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

combo($("artist"), "artist");
combo($("title"), "title", () => $("artist").value.trim());
combo($("search-q"), "song");
$("search-q").addEventListener("pick", () => $("search-form").requestSubmit());
