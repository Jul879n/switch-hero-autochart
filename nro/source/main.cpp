// SH Installer: se vincula a una cuenta del servidor Autochart por QR e instala en la SD las canciones
// que se envían desde la web (también permite instalar a mano).
#include <switch.h>
#include <curl/curl.h>

#include <sys/stat.h>
#include <cstdio>
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
    curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT, 15L);
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
    curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, toString);
    curl_easy_setopt(h, CURLOPT_WRITEDATA, &body);
    CURLcode rc = curl_easy_perform(h);
    long code = 0;
    curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(h);
    curl_slist_free_all(hdr);
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
        printf("  %s (%lld KB)\n", f.name.c_str(), f.bytes / 1024);
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
        CURLcode rc = curl_easy_perform(h);
        long code = 0;
        curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_cleanup(h);
        curl_slist_free_all(hdr);
        fclose(fp);
        std::string err;
        if (rc != CURLE_OK) err = std::string("Red: ") + curl_easy_strerror(rc);
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
    curl_easy_setopt(h, CURLOPT_TIMEOUT, 20L);
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

static u64 nowSec() { return armGetSystemTick() / armGetSystemTickFreq(); }

// Muestra el QR y espera a que el teléfono confirme. Devuelve false si el usuario sale (+).
static bool pairFlow(Config& cfg, PadState& pad) {
    while (appletMainLoop()) {
        consoleClear();
        printf("Vincular esta Switch a su cuenta\n\n");
        consoleUpdate(NULL);
        std::string resp;
        long code = postJson(cfg, "/api/switch/pair", "{}", resp);
        if (code != 200) {
            printf("No se pudo contactar al servidor (%s)%s\n\nA reintentar   + salir\n", cfg.server.c_str(),
                   code ? (" HTTP " + std::to_string(code)).c_str() : "");
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
        printf("Escanee con el teléfono, inicie sesión y pulse «Vincular».   + salir\n");
        printf("Código: %s   (%s)\n\n", pcode.c_str(), url.c_str());
        printQr(url);
        consoleUpdate(NULL);
        u64 start = nowSec(), last = 0;
        while (appletMainLoop() && nowSec() - start < (u64)ttl) {
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
    socketInitializeDefault();
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
    std::string info = "Conectando…";
    while (appletMainLoop() && !quit) {
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
                    printf("Recibiendo %s - %s\n", s.artist.c_str(), s.name.c_str());
                    std::string err = installSong(cfg, s);
                    if (err.empty()) { ackSong(cfg, s); info = "Instalada: " + s.name; }
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
                printf("Instalando %s - %s\n", songs[i].artist.c_str(), songs[i].name.c_str());
                std::string err = installSong(cfg, songs[i]);
                if (!err.empty()) { printf("ERROR: %s\n", err.c_str()); break; }
            }
            printf("Listo.\n");
            waitA(pad);
            redraw = true;
        }
        if (redraw) {
            consoleClear();
            printf("SH Installer  |  A instalar  Y faltantes  B recargar  X otra cuenta  + salir\n");
            printf("%s\n\n", status.empty() ? ("Conectada · esperando envíos desde la web. " + info).c_str() : status.c_str());
            if (status.empty() && songs.empty()) printf("Su cuenta no tiene canciones listas.\n");
            for (size_t i = 0; i < songs.size(); i++)
                printf("%s %s %s - %s\n", i == sel ? ">" : " ", installed(songs[i]) ? "[x]" : "[ ]",
                       songs[i].artist.c_str(), songs[i].name.c_str());
            redraw = false;
        }
        consoleUpdate(NULL);
    }
    curl_global_cleanup();
    romfsExit();
    socketExit();
    consoleExit(NULL);
    return 0;
}
