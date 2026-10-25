// SH Installer: se vincula a una cuenta del servidor Autochart por QR e instala en la SD las canciones
// que se envían desde la web (también permite instalar a mano).
#include <switch.h>
#include <curl/curl.h>

#include <sys/stat.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "json.hpp"
#include "qrcodegen.hpp"

using json = nlohmann::json;

static const char* CONFIG_PATH = "sdmc:/switch/sh-installer/config.ini";
static const char* SONGS_DIR = "sdmc:/switch/switch-hero/songs";
static const char* CA_PATH = "romfs:/cacert.pem";

static const char* DEFAULT_SERVER = "https://switchhero.jarayacontreras.cl";
static const int REFRESH_SEC = 10;

struct Config {
    std::string server = DEFAULT_SERVER, token;      // token = sesión de la cuenta vinculada
};

struct FileInfo {
    std::string name;
    long long bytes = 0;
};

struct Song {
    std::string id, name, artist, folder;
    std::vector<FileInfo> files;
    bool pending = false;       // enviada desde la web
};

// La consola de libnx solo dibuja ASCII: se translitera lo común y el resto pasa a '?'.
static std::string ascii(const std::string& in) {
    static const std::map<std::string, char> tr = {
        {"á", 'a'}, {"é", 'e'}, {"í", 'i'}, {"ó", 'o'}, {"ú", 'u'}, {"ü", 'u'}, {"ñ", 'n'},
        {"Á", 'A'}, {"É", 'E'}, {"Í", 'I'}, {"Ó", 'O'}, {"Ú", 'U'}, {"Ü", 'U'}, {"Ñ", 'N'},
        {"à", 'a'}, {"è", 'e'}, {"ê", 'e'}, {"ç", 'c'}, {"ö", 'o'}, {"ä", 'a'}, {"â", 'a'}, {"ô", 'o'}};
    std::string out;
    for (size_t i = 0; i < in.size();) {
        unsigned char c = in[i];
        if (c >= 0x20 && c < 0x7F) { out += (char)c; i++; continue; }
        size_t n = c >= 0xF0 ? 4 : c >= 0xE0 ? 3 : c >= 0xC0 ? 2 : 1;
        auto it = tr.find(in.substr(i, n));
        out += it != tr.end() ? it->second : '?';
        i += n;
    }
    return out;
}

static std::string clip(const std::string& s, size_t n) { return s.size() <= n ? s : s.substr(0, n - 3) + "..."; }

static std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}

static bool loadConfig(Config& c) {
    std::ifstream in(CONFIG_PATH);
    if (!in) return false;
    c.server.clear();
    std::string line;
    while (std::getline(in, line)) {
        auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string k = trim(line.substr(0, eq)), v = trim(line.substr(eq + 1));
        if (k == "server") c.server = v;
        else if (k == "token") c.token = v;
    }
    while (!c.server.empty() && c.server.back() == '/') c.server.pop_back();
    if (c.server.empty()) c.server = DEFAULT_SERVER;
    return !c.token.empty();
}

static void saveConfig(const Config& c) {
    mkdir("sdmc:/switch", 0777);
    mkdir("sdmc:/switch/sh-installer", 0777);
    std::ofstream out(CONFIG_PATH, std::ios::trunc);
    out << "server=" << c.server << "\ntoken=" << c.token << "\n";
}

// Si la carpeta o el archivo ya existe con el tamaño correcto.
static long long fileSize(const std::string& p) {
    struct stat st;
    return stat(p.c_str(), &st) == 0 ? (long long)st.st_size : -1;
}

static bool installed(const Song& s) {
    if (s.files.empty()) return false;
    for (auto& f : s.files)
        if (fileSize(std::string(SONGS_DIR) + "/" + s.folder + "/" + f.name) != f.bytes) return false;
    return true;
}

static std::string safeSeg(const std::string& s) {
    // un nombre de archivo o carpeta, sin separadores ni "..": defensa ante un servidor hostil
    if (s.empty() || s == "." || s == ".." || s.find('/') != std::string::npos ||
        s.find('\\') != std::string::npos || s.find(':') != std::string::npos)
        return "";
    return s;
}

static size_t toString(char* p, size_t sz, size_t n, void* ud) {
    static_cast<std::string*>(ud)->append(p, sz * n);
    return sz * n;
}
static size_t toFile(char* p, size_t sz, size_t n, void* ud) {
    return fwrite(p, sz, n, static_cast<FILE*>(ud)) * sz;
}

// La red nunca debe congelar la consola: cada transferencia atiende a la consola (HOME, +) y se puede cancelar.
static PadState* g_pad = nullptr;
static bool g_stop = false;              // el usuario pidio salir (+ o HOME) durante una transferencia
static bool g_showProgress = false;
static std::string g_progName;
static u64 g_progTick = 0;

static int progressCb(void*, curl_off_t dltotal, curl_off_t dlnow, curl_off_t, curl_off_t) {
    if (!appletMainLoop()) { g_stop = true; return 1; }
    if (g_pad) {
        padUpdate(g_pad);
        if (padGetButtonsDown(g_pad) & HidNpadButton_Plus) { g_stop = true; return 1; }
    }
    if (g_showProgress && dltotal > 0) {
        u64 now = armTicksToNs(armGetSystemTick()) / 1000000ULL;
        if (now - g_progTick >= 200) {
            g_progTick = now;
            printf("\r  %3d%%  %-50.50s", (int)(dlnow * 100 / dltotal), g_progName.c_str());
            consoleUpdate(NULL);
        }
    }
    return 0;
}

static CURL* newHandle(const Config& c, const std::string& path, curl_slist** hdr) {
    CURL* h = curl_easy_init();
    if (!h) return nullptr;
    std::string url = c.server + path;
    *hdr = nullptr;
    if (!c.token.empty()) *hdr = curl_slist_append(*hdr, ("Authorization: Bearer " + c.token).c_str());
    *hdr = curl_slist_append(*hdr, "Content-Type: application/json");
    curl_easy_setopt(h, CURLOPT_URL, url.c_str());
    curl_easy_setopt(h, CURLOPT_HTTPHEADER, *hdr);
    curl_easy_setopt(h, CURLOPT_CAINFO, CA_PATH);
    curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT, 8L);
    curl_easy_setopt(h, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(h, CURLOPT_NOPROGRESS, 0L);
    curl_easy_setopt(h, CURLOPT_XFERINFOFUNCTION, progressCb);
    curl_easy_setopt(h, CURLOPT_LOW_SPEED_LIMIT, 1024L);
    curl_easy_setopt(h, CURLOPT_LOW_SPEED_TIME, 30L);
    return h;
}

static std::string urlEscape(CURL* h, const std::string& s) {
    char* e = curl_easy_escape(h, s.c_str(), (int)s.size());
    std::string r = e ? e : "";
    if (e) curl_free(e);
    return r;
}

// Devuelve "" si todo bien; si no, el mensaje de error.
static std::string fetchSongs(const Config& c, std::vector<Song>& out, bool& authFail) {
    authFail = false;
    curl_slist* hdr = nullptr;
    CURL* h = newHandle(c, "/api/switch/songs", &hdr);
    if (!h) return "No se pudo iniciar curl";
    std::string body;
    curl_easy_setopt(h, CURLOPT_TIMEOUT, 20L);
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, toString);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &body);
    CURLcode rc = curl_easy_perform(h);
    long code = 0;
    curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(h);
    curl_slist_free_all(hdr);
    if (g_stop) return "Cancelado";
    if (rc != CURLE_OK) return std::string("Red: ") + curl_easy_strerror(rc);
    if (code == 401) { authFail = true; return "La sesión venció; hay que vincular la Switch de nuevo"; }
    if (code == 403) return "Cambie su contraseña en la web para continuar";
    if (code != 200) return "HTTP " + std::to_string(code);
    try {
        json j = json::parse(body);
        for (auto& e : j) {
            Song s;
            s.id = e.value("id", "");
            s.name = e.value("name", "");
            s.artist = e.value("artist", "");
            s.folder = safeSeg(e.value("folder", ""));
            s.pending = e.value("pending", false);
            for (auto& f : e["files"]) {
                FileInfo fi;
                fi.name = safeSeg(f.value("name", ""));
                fi.bytes = f.value("bytes", 0LL);
                if (!fi.name.empty()) s.files.push_back(fi);
            }
            if (!s.id.empty() && !s.folder.empty() && !s.files.empty()) out.push_back(s);
        }
    } catch (const std::exception& e) {
        return std::string("Respuesta inválida: ") + e.what();
    }
    return "";
}

static std::string installSong(const Config& c, const Song& s) {
    mkdir("sdmc:/switch", 0777);
    mkdir("sdmc:/switch/switch-hero", 0777);
    mkdir(SONGS_DIR, 0777);
    std::string dir = std::string(SONGS_DIR) + "/" + s.folder;
    mkdir(dir.c_str(), 0777);
    for (auto& f : s.files) {
        std::string finalPath = dir + "/" + f.name, part = finalPath + ".part";
        if (fileSize(finalPath) == f.bytes) continue;
        printf("  %s (%lld KB)\n", ascii(f.name).c_str(), f.bytes / 1024);
        consoleUpdate(NULL);
        FILE* fp = fopen(part.c_str(), "wb");
        if (!fp) return "No se pudo escribir " + part;
        curl_slist* hdr = nullptr;
        CURL* probe = curl_easy_init();
        std::string path = "/api/switch/songs/" + s.id + "/" + urlEscape(probe, f.name);
        curl_easy_cleanup(probe);
        CURL* h = newHandle(c, path, &hdr);
        if (!h) { fclose(fp); remove(part.c_str()); return "No se pudo iniciar curl"; }
        curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, toFile);
        curl_easy_setopt(h, CURLOPT_WRITEDATA, fp);
        g_progName = ascii(f.name); g_progTick = 0; g_showProgress = true;
        CURLcode rc = curl_easy_perform(h);
        g_showProgress = false;
        printf("\r  100%%  %-50.50s\n", g_progName.c_str());
        long code = 0;
        curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_cleanup(h);
        curl_slist_free_all(hdr);
        fclose(fp);
        std::string err;
        if (g_stop) err = "Cancelado";
        else if (rc != CURLE_OK) err = std::string("Red: ") + curl_easy_strerror(rc);
        else if (code != 200) err = "HTTP " + std::to_string(code) + (code == 401 || code == 403 ? " (sesión inválida)" : "");
        else if (fileSize(part) != f.bytes) err = "Tamaño incorrecto en " + f.name;
        if (!err.empty()) { remove(part.c_str()); return err; }
        if (rename(part.c_str(), finalPath.c_str()) != 0) { remove(part.c_str()); return "No se pudo renombrar " + f.name; }
    }
    return "";
}

// POST con cuerpo JSON; devuelve el código HTTP (0 si falló la red) y la respuesta en `resp`.
static long postJson(const Config& c, const std::string& path, const std::string& body, std::string& resp) {
    curl_slist* hdr = nullptr;
    CURL* h = newHandle(c, path, &hdr);
    if (!h) return 0;
    resp.clear();
    curl_easy_setopt(h, CURLOPT_POST, 1L);
    curl_easy_setopt(h, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(h, CURLOPT_TIMEOUT, 15L);
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, toString);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &resp);
    CURLcode rc = curl_easy_perform(h);
    long code = 0;
    curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(h);
    curl_slist_free_all(hdr);
    return rc == CURLE_OK ? code : 0;
}

static void ackSong(const Config& c, const Song& s) {
    std::string resp;
    postJson(c, "/api/switch/songs/" + s.id + "/ack", "{}", resp);
}

// QR en la consola: módulos claros = fondo blanco, oscuros = fondo negro (2 columnas por módulo).
static void printQr(const std::string& text) {
    using qrcodegen::QrCode;
    QrCode qr = QrCode::encodeText(text.c_str(), QrCode::Ecc::LOW);
    for (int y = -1; y <= qr.getSize(); y++) {
        std::string row;
        for (int x = -1; x <= qr.getSize(); x++)
            row += qr.getModule(x, y) ? "  " : "\x1b[47m  \x1b[0m";
        printf("%s\n", row.c_str());
    }
}

// El bloque de transferencia de sockets (bsd) queda prestado al sistema hasta que este cierra el cliente,
// y con conexiones recien cerradas eso tarda mas que nuestra salida; libnx lo devuelve a malloc sin esperar.
// hbloader carga el siguiente NRO al INICIO del heap y sphaira reserva mucha memoria al arrancar: si el
// bloque queda ahi, cae (2347-0018 o Data Abort). Se reserva temporalmente casi todo el heap para que libnx
// pida ese bloque al FINAL, donde ni hbloader ni el menu llegan, y luego se libera la reserva.
static const size_t TAIL = 8u << 20;
static u64 g_tmemAddr = 0, g_tmemSize = 0;      // bloque de sockets aparcado (se excluye del chequeo de salida)

static void* g_plugs[64];
static char  g_parkLog[160] = "";

static Result initSocketsAtHeapTail() {
    u64 hs = envHasHeapOverride() ? envGetHeapOverrideSize() : 0;
    void* big = nullptr;
    int np = 0;
    if (hs > TAIL * 4) {
        big = malloc(hs - TAIL - (4u << 20));
        // malloc reutiliza primero los huecos libres de abajo: se rellenan con tapones del tamano del bloque de sockets
        // hasta que una reserva cae por encima de `big` (ya no quedan huecos); esa ultima se libera para que la tome libnx.
        if (big) {
            u64 top = (u64)big + (hs - TAIL - (4u << 20));
            while (np < 64) {
                void* q = malloc(0x240000);
                if (!q) break;
                if ((u64)q >= top) { free(q); break; }
                g_plugs[np++] = q;
            }
        }
        snprintf(g_parkLog, sizeof g_parkLog, "big=%p tapones=%d", big, np);
    }
    Result rc = socketInitializeDefault();
    for (int i = 0; i < np; i++) free(g_plugs[i]);
    free(big);
    if (hs) {
        u64 a = (u64)envGetHeapOverrideAddr(), end = a + hs, from = end - TAIL - (16u << 20);
        for (u64 q = from; q < end;) {
            MemoryInfo mi; u32 pi;
            if (R_FAILED(svcQueryMemory(&mi, &pi, q)) || mi.size == 0) break;
            if ((mi.attr & MemAttr_IsBorrowed) && mi.addr >= from) { g_tmemAddr = mi.addr; g_tmemSize = mi.size; break; }
            q = mi.addr + mi.size;
        }
    }
    return rc;
}

// 1 = al salir, libnx cierra este applet (ExitProcessAndReturn) y vuelve al HOME en vez de regresar al
// loader. Definicion fuerte que reemplaza a la debil de libnx; se activa solo como plan B (ver salida).
extern "C" { u32 __nx_applet_exit_mode = 0; }

// Paginas del heap que siguen prestadas o mapeadas por un servicio (attr != 0) o que no son heap normal.
// Si fp != NULL, las registra. Devuelve cuantas hay (max 64).
static int heapAnomalies(FILE* fp) {
    if (!envHasHeapOverride()) return 0;
    u64 a = (u64)envGetHeapOverrideAddr(), end = a + envGetHeapOverrideSize();
    int bad = 0;
    while (a < end && bad < 64) {
        MemoryInfo mi; u32 pi;
        if (R_FAILED(svcQueryMemory(&mi, &pi, a)) || mi.size == 0) break;
        bool parked = g_tmemSize && mi.addr >= g_tmemAddr && mi.addr < g_tmemAddr + g_tmemSize && mi.attr == MemAttr_IsBorrowed;
        if (!parked && (mi.attr != 0 || (mi.type & 0xFF) != MemType_Heap)) {
            if (fp) fprintf(fp, "%016llx +%llx type=%x attr=%x perm=%x ipc=%u dev=%u\n", (unsigned long long)mi.addr,
                            (unsigned long long)mi.size, mi.type, mi.attr, mi.perm, mi.ipc_refcount, mi.device_refcount);
            bad++;
        }
        a = mi.addr + mi.size;
    }
    return bad;
}

// La memoria de transferencia de sockets (bsd) queda prestada al sistema hasta que el servicio procesa el
// cierre de sesion; libnx la devuelve a malloc sin esperar. Si volvemos al loader con esa pagina aun
// prestada, el menu (sphaira) se carga sobre ella y cae (2347-0018 o Data Abort). Se espera hasta maxMs.
static int waitHeapClean(int maxMs) {
    u64 t0 = armTicksToNs(armGetSystemTick()) / 1000000ULL;
    for (;;) {
        if (heapAnomalies(NULL) == 0) return (int)(armTicksToNs(armGetSystemTick()) / 1000000ULL - t0);
        if ((int)(armTicksToNs(armGetSystemTick()) / 1000000ULL - t0) >= maxMs) return -1;
        svcSleepThread(2000000ULL);     // 2 ms
    }
}

static void logExit(int waitedMs) {
    FILE* fp = fopen("sdmc:/switch/sh-installer/salida.log", "w");
    if (!fp) return;
    fprintf(fp, "heap %p +%llx sockets aparcados en %016llx +%llx\n", envGetHeapOverrideAddr(),
            (unsigned long long)envGetHeapOverrideSize(), (unsigned long long)g_tmemAddr, (unsigned long long)g_tmemSize);
    fprintf(fp, "%s\n", g_parkLog);
    fprintf(fp, waitedMs >= 0 ? "limpio tras %d ms: vuelve al menu\n" : "anomalias ajenas (timeout): plan B, salir al HOME\n", waitedMs);
    int bad = heapAnomalies(fp);
    fprintf(fp, "anomalias=%d\n", bad);
    fclose(fp);
}

static u64 nowSec() { return armGetSystemTick() / armGetSystemTickFreq(); }

// Muestra el QR y espera a que el teléfono confirme. Devuelve false si el usuario sale (+).
static bool pairFlow(Config& cfg, PadState& pad) {
    while (appletMainLoop() && !g_stop) {
        consoleClear();
        printf("\x1b[1mSH Installer - vincular esta Switch\x1b[0m\n\nPidiendo codigo al servidor...\n");
        consoleUpdate(NULL);
        std::string resp;
        long code = postJson(cfg, "/api/switch/pair", "{}", resp);
        if (code != 200) {
            printf("\x1b[31mNo se pudo contactar al servidor\x1b[0m\n%s%s\n\nRevise la conexion a internet de la Switch.\n\n  A reintentar    + salir\n", cfg.server.c_str(),
                   code ? (" (HTTP " + std::to_string(code) + ")").c_str() : "");
            consoleUpdate(NULL);
            while (appletMainLoop()) {
                padUpdate(&pad);
                u64 d = padGetButtonsDown(&pad);
                if (d & HidNpadButton_Plus) return false;
                if (d & HidNpadButton_A) break;
                consoleUpdate(NULL);
            }
            continue;
        }
        std::string url, secret, pcode;
        long ttl = 600;
        try {
            json j = json::parse(resp);
            url = j.value("url", ""); secret = j.value("secret", ""); pcode = j.value("code", "");
            ttl = j.value("expires_in", 600);
        } catch (...) { continue; }
        consoleClear();
        printf("\x1b[1mSH Installer - vincular esta Switch\x1b[0m                         + salir\n");
        printf("1) Escanee el QR con el telefono   2) Inicie sesion   3) Pulse \"Vincular\"\n");
        printf("Codigo: \x1b[1;33m%s\x1b[0m   Esperando confirmacion...\n", pcode.c_str());
        printQr(url);
        consoleUpdate(NULL);
        u64 start = nowSec(), last = 0;
        while (appletMainLoop() && !g_stop && nowSec() - start < (u64)ttl) {
            padUpdate(&pad);
            if (padGetButtonsDown(&pad) & HidNpadButton_Plus) return false;
            if (nowSec() - last >= 3) {
                last = nowSec();
                std::string body = json{{"secret", secret}}.dump(), r;
                long c = postJson(cfg, "/api/switch/pair/poll", body, r);
                if (c == 200) {
                    try {
                        json j = json::parse(r);
                        if (j.value("status", "") == "ok") {
                            cfg.token = j.value("token", "");
                            if (!cfg.token.empty()) { saveConfig(cfg); return true; }
                        }
                    } catch (...) {}
                } else if (c == 410) break;     // venció: pedir otro código
            }
            consoleUpdate(NULL);
        }
    }
    return false;
}

static void drawMain(const Config& cfg, const std::string& status, const std::string& info,
                     const std::vector<Song>& songs, size_t sel) {
    const size_t ROWS = 36;                   // alto util de la lista en una consola de 45 filas
    consoleClear();
    std::string host = cfg.server;
    auto p = host.find("://");
    if (p != std::string::npos) host = host.substr(p + 3);
    printf("\x1b[1;36mSH Installer 2.0.6\x1b[0m  %s\n", ascii(host).c_str());
    if (status.empty())
        printf("\x1b[1;32m[ CONECTADA ]\x1b[0m esperando envios desde la web (revisa cada %d s)\n", REFRESH_SEC);
    else
        printf("\x1b[1;31m[ SIN CONEXION ]\x1b[0m %s\n", clip(ascii(status), 62).c_str());
    printf("%s\n", info.empty() ? " " : clip(ascii(info), 78).c_str());
    printf("--------------------------------------------------------------------------------\n");
    if (songs.empty()) {
        if (status.empty())
            printf("\nSu cuenta no tiene canciones listas.\nGenere una en la web y pulse \"Enviar a la Switch\".\n");
    } else {
        size_t first = sel > ROWS / 2 ? sel - ROWS / 2 : 0;
        if (first + ROWS > songs.size()) first = songs.size() > ROWS ? songs.size() - ROWS : 0;
        for (size_t i = first; i < songs.size() && i < first + ROWS; i++) {
            const Song& s = songs[i];
            const char* mark = installed(s) ? "\x1b[32m[x]\x1b[0m" : s.pending ? "\x1b[33m[>]\x1b[0m" : "[ ]";
            std::string name = clip(ascii(s.artist + " - " + s.name), 66);
            printf("%s %s %s%s%s\n", i == sel ? "\x1b[7m" : "", mark, name.c_str(),
                   s.pending && !installed(s) ? "  (en cola)" : "", "\x1b[0m");
        }
        if (songs.size() > ROWS) printf("\x1b[90m%zu/%zu\x1b[0m\n", sel + 1, songs.size());
    }
    printf("\x1b[45;1H\x1b[90mA instalar  Y faltantes  B recargar  X otra cuenta  + salir\x1b[0m");
}

static void waitA(PadState& pad) {
    printf("\nPulse A para continuar.\n");
    consoleUpdate(NULL);
    while (appletMainLoop()) {
        padUpdate(&pad);
        if (padGetButtonsDown(&pad) & HidNpadButton_A) break;
        consoleUpdate(NULL);
    }
}

int main() {
    consoleInit(NULL);
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    PadState pad;
    padInitializeDefault(&pad);
    g_pad = &pad;
    initSocketsAtHeapTail();
    romfsInit();
    curl_global_init(CURL_GLOBAL_DEFAULT);

    Config cfg;
    std::vector<Song> songs;
    std::string status;
    bool ok = loadConfig(cfg);
    if (!ok) ok = pairFlow(cfg, pad);

    size_t sel = 0;
    bool redraw = true, quit = !ok, refresh = ok;
    u64 lastFetch = 0;
    std::string info;
    while (appletMainLoop() && !quit && !g_stop) {
        padUpdate(&pad);
        u64 down = padGetButtonsDown(&pad);
        if (down & HidNpadButton_Plus) break;
        if (down & HidNpadButton_Up && sel > 0) { sel--; redraw = true; }
        if (down & HidNpadButton_Down && sel + 1 < songs.size()) { sel++; redraw = true; }
        if (down & HidNpadButton_B) refresh = true;             // recargar ya
        if (down & HidNpadButton_X) {                           // vincular otra cuenta
            cfg.token.clear();
            if (!pairFlow(cfg, pad)) break;
            refresh = true; redraw = true;
        }
        if (!refresh && nowSec() - lastFetch >= (u64)REFRESH_SEC) refresh = true;
        if (refresh) {
            refresh = false;
            bool authFail = false;
            std::vector<Song> fresh;
            status = fetchSongs(cfg, fresh, authFail);
            lastFetch = nowSec();
            if (g_stop) break;
            if (authFail) {
                cfg.token.clear();
                if (!pairFlow(cfg, pad)) break;
                refresh = true; redraw = true;
                continue;
            }
            if (status.empty()) {
                songs = fresh;
                if (sel >= songs.size()) sel = 0;
                // lo enviado desde la web se instala solo
                for (auto& s : songs) {
                    if (!s.pending) continue;
                    consoleClear();
                    printf("\x1b[1;33mRecibiendo desde la web\x1b[0m\n%s - %s\n\n", ascii(s.artist).c_str(), ascii(s.name).c_str());
                    std::string err = installSong(cfg, s);
                    if (err.empty()) { ackSong(cfg, s); info = "Instalada: " + s.artist + " - " + s.name; }
                    else { info = "ERROR en " + s.name + ": " + err; break; }
                }
            }
            redraw = true;
        }
        if (status.empty() && !songs.empty() && (down & (HidNpadButton_A | HidNpadButton_Y))) {
            bool all = down & HidNpadButton_Y;
            consoleClear();
            for (size_t i = 0; i < songs.size(); i++) {
                if (!all && i != sel) continue;
                if (all && installed(songs[i])) continue;
                printf("\x1b[1;33mInstalando\x1b[0m %s - %s\n", ascii(songs[i].artist).c_str(), ascii(songs[i].name).c_str());
                std::string err = installSong(cfg, songs[i]);
                if (!err.empty()) { printf("\x1b[31mERROR: %s\x1b[0m\n", ascii(err).c_str()); break; }
            }
            printf("\x1b[32mListo.\x1b[0m\n");
            waitA(pad);
            redraw = true;
        }
        if (redraw) {
            drawMain(cfg, status, info, songs, sel);
            redraw = false;
        }
        consoleUpdate(NULL);
    }
    curl_global_cleanup();
    socketExit();                 // primero: el sistema tarda en devolver la memoria de sockets
    romfsExit();
    consoleExit(NULL);
    int waited = waitHeapClean(500);          // el bloque de sockets aparcado no cuenta
    logExit(waited);
    if (waited < 0) __nx_applet_exit_mode = 1;    // plan B: cerrar el applet y volver al HOME
    return 0;
}
