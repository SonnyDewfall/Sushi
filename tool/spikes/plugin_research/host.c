/* A bench LV2 host for one plugin, built to answer "would Sushi 1.3.0 load
 * this, and what does it do to a signal" without a Sushi.
 *
 *   host URI in.f32 out.f32 CHANNELS [PORT=VALUE ...]
 *
 * in.f32 and out.f32 are raw interleaved float32 at 48 kHz with CHANNELS
 * channels: the track's width. A port is named by its symbol, in its own
 * units. One JSON line goes to stdout: the plugin's ports, whether Sushi would
 * refuse it, the final value of every output control port (the latency port
 * among them), and the time spent inside run().
 *
 * What it copies from Sushi 1.3.0 (src/library/lv2/, the 1.3.0 tag):
 *   - the host features, and nothing else: urid map and unmap, log,
 *     worker schedule, state makePath, options, state loadDefaultState and
 *     the three buf-size ones (lv2_model.cpp `static_features` and
 *     `_initialize_host_feature_list`), plus lv2core isLive, which
 *     `_feature_is_supported` accepts by name. A required feature outside
 *     that list is what Sushi refuses a plugin for (`_check_for_required_features`);
 *     this host refuses it the same way and says which.
 *   - the plugin's default state applied after instantiation
 *     (`lilv_state_new_from_world`, as `Model::load_plugin` does);
 *   - 64-frame chunks, min = max block length 64, flush-to-zero on;
 *   - channels: a plugin gets min(its ports, the track's channels) of each;
 *     extra inputs read silence; track channels it does not write stay silent
 *     (plugin-ports.txt says why);
 *   - atom input ports get an empty sequence every chunk (Sushi writes MIDI
 *     and transport there, and the rig sends neither to an effect); atom
 *     outputs are cleared for the plugin to write into;
 *   - a CV port is refused: Sushi 1.3.0 has no CV support (lv2_wrapper.cpp,
 *     `case PortType::TYPE_CV: // CV Support not yet implemented.`).
 *
 * What it does not copy: Sushi runs worker jobs on a separate thread and
 * delivers the answers at the start of a later chunk; here a job runs at once
 * inside schedule() and its answer is delivered after the same run(). For a
 * plugin that works at all, that is the kindest case.
 */
#include <lilv/lilv.h>
#include <lv2/atom/atom.h>
#include <lv2/buf-size/buf-size.h>
#include <lv2/core/lv2.h>
#include <lv2/log/log.h>
#include <lv2/options/options.h>
#include <lv2/state/state.h>
#include <lv2/urid/urid.h>
#include <lv2/worker/worker.h>
#include <pmmintrin.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <xmmintrin.h>

#define CHUNK 64
#define MAXCH 16
#define ATOM_CAP 32768

static const char *SUSHI_FEATURES[] = {
    LV2_URID__map, LV2_URID__unmap, LV2_LOG__log, LV2_WORKER__schedule,
    LV2_STATE__makePath, LV2_OPTIONS__options, LV2_STATE__loadDefaultState,
    LV2_BUF_SIZE__powerOf2BlockLength, LV2_BUF_SIZE__fixedBlockLength,
    LV2_BUF_SIZE__boundedBlockLength, "http://lv2plug.in/ns/lv2core#isLive", NULL};

static char *uris[4096];
static int nuris;

static LV2_URID map_uri(LV2_URID_Map_Handle h, const char *uri) {
    (void)h;
    for (int i = 0; i < nuris; i++)
        if (!strcmp(uris[i], uri)) return i + 1;
    uris[nuris] = strdup(uri);
    return ++nuris;
}

static const char *unmap_uri(LV2_URID_Unmap_Handle h, LV2_URID u) {
    (void)h;
    return (u >= 1 && (int)u <= nuris) ? uris[u - 1] : NULL;
}

static int log_lines;
static int log_printf(LV2_Log_Handle h, LV2_URID type, const char *fmt, ...) {
    (void)h; (void)type;
    if (log_lines++ > 20) return 0;
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "plugin log: ");
    int r = vfprintf(stderr, fmt, ap);
    va_end(ap);
    return r;
}
static int log_vprintf(LV2_Log_Handle h, LV2_URID type, const char *fmt, va_list ap) {
    (void)h; (void)type;
    if (log_lines++ > 20) return 0;
    fprintf(stderr, "plugin log: ");
    return vfprintf(stderr, fmt, ap);
}

static char *make_path(LV2_State_Make_Path_Handle h, const char *path) {
    (void)h;
    char *out = malloc(strlen(path) + 32);
    sprintf(out, "/tmp/lv2-bench-%s", path);
    return out;
}

static const LV2_Worker_Interface *worker_iface;
static LilvInstance *instance;
static int jobs;
static unsigned char responses[64][4096];
static uint32_t response_sizes[64];
static int nresponses;

static LV2_Worker_Status respond(LV2_Worker_Respond_Handle h, uint32_t size, const void *data) {
    (void)h;
    if (nresponses >= 64 || size > 4096) return LV2_WORKER_ERR_NO_SPACE;
    memcpy(responses[nresponses], data, size);
    response_sizes[nresponses++] = size;
    return LV2_WORKER_SUCCESS;
}

static LV2_Worker_Status schedule(LV2_Worker_Schedule_Handle h, uint32_t size, const void *data) {
    (void)h;
    jobs++;
    if (worker_iface) worker_iface->work(lilv_instance_get_handle(instance), respond, NULL, size, data);
    return LV2_WORKER_SUCCESS;
}

static uint32_t nports;
static float *controls;
static const LilvPlugin *plugin;

static void set_port_value(const char *symbol, void *ud, const void *value, uint32_t size, uint32_t type) {
    (void)ud; (void)size;
    if (type != map_uri(NULL, LV2_ATOM__Float)) return;
    for (uint32_t i = 0; i < nports; i++) {
        const LilvPort *p = lilv_plugin_get_port_by_index(plugin, i);
        if (!strcmp(lilv_node_as_string(lilv_port_get_symbol(plugin, p)), symbol)) controls[i] = *(const float *)value;
    }
}

/* The report goes to the file HOST_JSON names, or stdout: plugins print to
 * stdout too, and would land in the middle of it. */
static FILE *js;

static void json_str(const char *s) {
    fputc('"', js);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') fputc('\\', js);
        fputc(*s, js);
    }
    fputc('"', js);
}

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: host URI in.f32 out.f32 CHANNELS [SYMBOL=VALUE...]\n");
        return 2;
    }
    _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON);
    _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON);
    js = getenv("HOST_JSON") ? fopen(getenv("HOST_JSON"), "w") : stdout;
    const char *uri = argv[1];
    int nch = atoi(argv[4]);

    LilvWorld *world = lilv_world_new();
    lilv_world_load_all(world);
    LilvNode *u = lilv_new_uri(world, uri);
    plugin = lilv_plugins_get_by_uri(lilv_world_get_all_plugins(world), u);
    if (!plugin) {
        fprintf(js, "{\"uri\": \"%s\", \"error\": \"not installed\"}\n", uri);
        return 4;
    }

    /* Would Sushi load it? */
    const char *refused = NULL;
    LilvNodes *req = lilv_plugin_get_required_features(plugin);
    LILV_FOREACH(nodes, it, req) {
        const char *f = lilv_node_as_uri(lilv_nodes_get(req, it));
        int ok = 0;
        for (int k = 0; SUSHI_FEATURES[k]; k++) ok |= !strcmp(f, SUSHI_FEATURES[k]);
        if (!ok) refused = strdup(f);
    }

    LilvNode *audio = lilv_new_uri(world, LV2_CORE__AudioPort);
    LilvNode *control = lilv_new_uri(world, LV2_CORE__ControlPort);
    LilvNode *cv = lilv_new_uri(world, LV2_CORE__CVPort);
    LilvNode *atom = lilv_new_uri(world, LV2_ATOM__AtomPort);
    LilvNode *input = lilv_new_uri(world, LV2_CORE__InputPort);
    LilvNode *optional = lilv_new_uri(world, LV2_CORE__connectionOptional);
    nports = lilv_plugin_get_num_ports(plugin);
    int ain[MAXCH], aout[MAXCH], nin = 0, nout = 0, natom_in = 0, natom_out = 0, ncv = 0;
    for (uint32_t i = 0; i < nports; i++) {
        const LilvPort *p = lilv_plugin_get_port_by_index(plugin, i);
        int in = lilv_port_is_a(plugin, p, input);
        if (lilv_port_is_a(plugin, p, audio)) {
            if (in) ain[nin++] = i; else aout[nout++] = i;
        } else if (lilv_port_is_a(plugin, p, atom)) {
            if (in) natom_in++; else natom_out++;
        } else if (lilv_port_is_a(plugin, p, cv)) {
            if (!lilv_port_has_property(plugin, p, optional)) ncv++;
        }
    }
    if (!refused && ncv) refused = "a CV port (Sushi 1.3.0 connects none)";

    fprintf(js, "{\"uri\": \"%s\", \"audio_in\": %d, \"audio_out\": %d, \"atom_in\": %d, \"atom_out\": %d, \"cv\": %d",
           uri, nin, nout, natom_in, natom_out, ncv);
    if (refused) {
        fprintf(js, ", \"sushi_refuses\": ");
        json_str(refused);
        fprintf(js, "}\n");
        return 6;
    }

    FILE *f = fopen(argv[2], "rb");
    if (!f) return 3;
    fseek(f, 0, SEEK_END);
    long bytes = ftell(f);
    fseek(f, 0, SEEK_SET);
    long frames = bytes / (sizeof(float) * nch);
    float *inbuf = malloc(bytes), *outbuf = calloc(frames * nch, sizeof(float));
    if (fread(inbuf, 1, bytes, f) != (size_t)bytes) return 3;
    fclose(f);

    LV2_URID_Map map = {NULL, map_uri};
    LV2_URID_Unmap unmap = {NULL, unmap_uri};
    LV2_Log_Log log = {NULL, log_printf, log_vprintf};
    LV2_Worker_Schedule sched = {NULL, schedule};
    LV2_State_Make_Path mkpath = {NULL, make_path};
    int32_t block = CHUNK, seq = ATOM_CAP;
    float rate = 48000.0f;
    LV2_Options_Option opts[] = {
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, LV2_BUF_SIZE__minBlockLength), sizeof(int32_t), map_uri(NULL, LV2_ATOM__Int), &block},
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, LV2_BUF_SIZE__maxBlockLength), sizeof(int32_t), map_uri(NULL, LV2_ATOM__Int), &block},
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, LV2_BUF_SIZE__sequenceSize), sizeof(int32_t), map_uri(NULL, LV2_ATOM__Int), &seq},
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, "http://lv2plug.in/ns/ext/parameters#sampleRate"), sizeof(float), map_uri(NULL, LV2_ATOM__Float), &rate},
        {LV2_OPTIONS_INSTANCE, 0, 0, 0, 0, NULL}};
    LV2_Feature f_map = {LV2_URID__map, &map}, f_unmap = {LV2_URID__unmap, &unmap},
                f_log = {LV2_LOG__log, &log}, f_sched = {LV2_WORKER__schedule, &sched},
                f_mkpath = {LV2_STATE__makePath, &mkpath}, f_opts = {LV2_OPTIONS__options, opts},
                f_lds = {LV2_STATE__loadDefaultState, NULL}, f_p2 = {LV2_BUF_SIZE__powerOf2BlockLength, NULL},
                f_fixed = {LV2_BUF_SIZE__fixedBlockLength, NULL}, f_bounded = {LV2_BUF_SIZE__boundedBlockLength, NULL};
    const LV2_Feature *features[] = {&f_map, &f_unmap, &f_log, &f_sched, &f_mkpath, &f_opts,
                                     &f_lds, &f_p2, &f_fixed, &f_bounded, NULL};

    instance = lilv_plugin_instantiate(plugin, 48000.0, features);
    if (!instance) {
        fprintf(js, ", \"error\": \"instantiate failed\"}\n");
        return 5;
    }
    worker_iface = lilv_instance_get_extension_data(instance, LV2_WORKER__interface);

    controls = calloc(nports, sizeof(float));
    float *mins = calloc(nports, sizeof(float)), *maxs = calloc(nports, sizeof(float)), *defs = calloc(nports, sizeof(float));
    lilv_plugin_get_port_ranges_float(plugin, mins, maxs, defs);
    static float abuf[2 * MAXCH + 2][CHUNK];
    static float silence[CHUNK], scratch[CHUNK];
    LV2_Atom_Sequence *atoms[64];
    int atom_is_in[64], natoms = 0;
    LV2_URID seq_urid = map_uri(NULL, LV2_ATOM__Sequence), chunk_urid = map_uri(NULL, LV2_ATOM__Chunk);
    for (uint32_t i = 0; i < nports; i++) {
        const LilvPort *p = lilv_plugin_get_port_by_index(plugin, i);
        if (lilv_port_is_a(plugin, p, control)) {
            controls[i] = defs[i] != defs[i] ? 0.0f : defs[i];
            lilv_instance_connect_port(instance, i, &controls[i]);
        } else if (lilv_port_is_a(plugin, p, atom)) {
            atoms[natoms] = calloc(1, ATOM_CAP);
            atom_is_in[natoms] = lilv_port_is_a(plugin, p, input);
            lilv_instance_connect_port(instance, i, atoms[natoms++]);
        } else if (lilv_port_is_a(plugin, p, cv)) {
            lilv_instance_connect_port(instance, i, NULL);
        }
    }
    /* The plugin's default state, as Model::load_plugin applies it. */
    LilvState *state = lilv_state_new_from_world(world, &map, u);
    if (state) {
        lilv_state_restore(state, instance, set_port_value, NULL, 0, features);
        lilv_state_free(state);
    }
    /* Then the settings asked for, by symbol; an unknown one is an error. */
    for (int a = 5; a < argc; a++) {
        const char *eq = strrchr(argv[a], '=');
        int found = 0;
        for (uint32_t i = 0; eq && i < nports; i++) {
            const LilvPort *p = lilv_plugin_get_port_by_index(plugin, i);
            const char *sym = lilv_node_as_string(lilv_port_get_symbol(plugin, p));
            if (lilv_port_is_a(plugin, p, control) && strlen(sym) == (size_t)(eq - argv[a]) && !strncmp(argv[a], sym, eq - argv[a])) {
                controls[i] = atof(eq + 1);
                found = 1;
            }
        }
        if (!found) {
            fprintf(js, ", \"error\": \"no control port %s\"}\n", argv[a]);
            return 7;
        }
    }
    int k = 0;
    for (int i = 0; i < nin; i++) lilv_instance_connect_port(instance, ain[i], i < nch ? abuf[k++] : silence);
    int out_base = k;
    for (int i = 0; i < nout; i++) lilv_instance_connect_port(instance, aout[i], i < nch ? abuf[k++] : scratch);

    struct timespec t0, t1;
    double busy = 0.0;
    int nonfinite_at = -1;
    lilv_instance_activate(instance);
    for (long pos = 0; pos + CHUNK <= frames; pos += CHUNK) {
        for (int i = 0; i < nin && i < nch; i++)
            for (int s = 0; s < CHUNK; s++) abuf[i][s] = inbuf[(pos + s) * nch + i];
        for (int a = 0; a < natoms; a++) {
            if (atom_is_in[a]) {
                atoms[a]->atom.type = seq_urid;
                atoms[a]->atom.size = sizeof(LV2_Atom_Sequence_Body);
                atoms[a]->body.unit = 0;
                atoms[a]->body.pad = 0;
            } else {
                atoms[a]->atom.type = chunk_urid;
                atoms[a]->atom.size = ATOM_CAP - sizeof(LV2_Atom);
            }
        }
        clock_gettime(CLOCK_MONOTONIC, &t0);
        lilv_instance_run(instance, CHUNK);
        while (nresponses && worker_iface && worker_iface->work_response) {
            int n = nresponses;
            nresponses = 0;
            for (int r = 0; r < n; r++)
                worker_iface->work_response(lilv_instance_get_handle(instance), response_sizes[r], responses[r]);
        }
        if (worker_iface && worker_iface->end_run) worker_iface->end_run(lilv_instance_get_handle(instance));
        clock_gettime(CLOCK_MONOTONIC, &t1);
        busy += (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
        for (int c = 0; c < nch; c++)
            for (int s = 0; s < CHUNK; s++) {
                float v = c < nout ? abuf[out_base + c][s] : 0.0f;
                if (nonfinite_at < 0 && !(v == v && v - v == 0.0f)) nonfinite_at = pos + s;
                outbuf[(pos + s) * nch + c] = v;
            }
    }
    lilv_instance_deactivate(instance);

    fprintf(js, ", \"worker\": %s, \"worker_jobs\": %d, \"frames\": %ld, \"run_seconds\": %.6f, \"nonfinite_from\": %d, \"outputs\": {",
           worker_iface ? "true" : "false", jobs, frames, busy, nonfinite_at);
    int first = 1;
    for (uint32_t i = 0; i < nports; i++) {
        const LilvPort *p = lilv_plugin_get_port_by_index(plugin, i);
        if (lilv_port_is_a(plugin, p, control) && !lilv_port_is_a(plugin, p, input)) {
            fprintf(js, "%s", first ? "" : ", ");
            json_str(lilv_node_as_string(lilv_port_get_symbol(plugin, p)));
            fprintf(js, ": %g", controls[i]);
            first = 0;
        }
    }
    fprintf(js, "}}\n");
    lilv_instance_free(instance);
    f = fopen(argv[3], "wb");
    fwrite(outbuf, sizeof(float), frames * nch, f);
    fclose(f);
    return 0;
}
