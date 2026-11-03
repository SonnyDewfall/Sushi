/* A minimal LV2 host for one plugin, run the way Sushi 1.3.0's offline
 * frontend runs one: 64-frame chunks, flush-to-zero and denormals-are-zero on,
 * minBlockLength = maxBlockLength = 64, a worker that runs at once.
 *
 *   host URI in.f32 out.f32 CHANNELS [PORT=VALUE ...]
 *
 * in.f32 and out.f32 are raw interleaved float32 with CHANNELS channels. A
 * port is named by its symbol or its name. Values are in the port's own units,
 * or, with NORM=1 in the environment, normalised 0..1 and mapped as Sushi
 * maps them (min + v * (max - min)). A plugin with fewer outputs than
 * CHANNELS leaves the rest silent.
 *
 * Built and run by repro.sh, against the system's LV2 plugins.
 */
#include <lilv/lilv.h>
#include <lv2/urid/urid.h>
#include <lv2/worker/worker.h>
#include <lv2/options/options.h>
#include <lv2/buf-size/buf-size.h>
#include <lv2/atom/atom.h>
#include <lv2/core/lv2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xmmintrin.h>
#include <pmmintrin.h>

#define CHUNK 64

static char *uris[1024];
static int nuris;

static LV2_URID map_uri(LV2_URID_Map_Handle h, const char *uri) {
    (void)h;
    for (int i = 0; i < nuris; i++) if (!strcmp(uris[i], uri)) return i + 1;
    uris[nuris] = strdup(uri);
    return ++nuris;
}

static const LV2_Worker_Interface *worker_iface;
static LilvInstance *instance;

static LV2_Worker_Status respond(LV2_Worker_Respond_Handle h, uint32_t size, const void *data) {
    (void)h; (void)size; (void)data;
    return LV2_WORKER_SUCCESS;
}

static int scheduled;

static LV2_Worker_Status schedule(LV2_Worker_Schedule_Handle h, uint32_t size, const void *data) {
    (void)h;
    scheduled++;
    if (worker_iface)
        worker_iface->work(lilv_instance_get_handle(instance), respond, NULL, size, data);
    return LV2_WORKER_SUCCESS;
}

int main(int argc, char **argv) {
    if (argc < 5) { fprintf(stderr, "usage: minihost URI in out channels [SYM=VAL...]\n"); return 2; }
    _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON);
    _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON);
    const char *uri = argv[1];
    int nch = atoi(argv[4]);

    FILE *f = fopen(argv[2], "rb");
    fseek(f, 0, SEEK_END); long bytes = ftell(f); fseek(f, 0, SEEK_SET);
    long frames = bytes / (sizeof(float) * nch);
    float *in = malloc(bytes), *out = calloc(frames * nch, sizeof(float));
    if (fread(in, 1, bytes, f) != (size_t)bytes) return 3;
    fclose(f);

    LilvWorld *world = lilv_world_new();
    lilv_world_load_all(world);
    LilvNode *u = lilv_new_uri(world, uri);
    const LilvPlugin *plugin = lilv_plugins_get_by_uri(lilv_world_get_all_plugins(world), u);
    if (!plugin) { fprintf(stderr, "no plugin %s\n", uri); return 4; }

    LV2_URID_Map map = {NULL, map_uri};
    LV2_Feature map_f = {LV2_URID__map, &map};
    LV2_Worker_Schedule sched = {NULL, schedule};
    LV2_Feature sched_f = {LV2_WORKER__schedule, &sched};
    int32_t block = CHUNK, seq = 4096;
    float rate = 48000.0f;
    LV2_Options_Option opts[] = {
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, LV2_BUF_SIZE__minBlockLength), sizeof(int32_t), map_uri(NULL, LV2_ATOM__Int), &block},
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, LV2_BUF_SIZE__maxBlockLength), sizeof(int32_t), map_uri(NULL, LV2_ATOM__Int), &block},
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, LV2_BUF_SIZE__sequenceSize), sizeof(int32_t), map_uri(NULL, LV2_ATOM__Int), &seq},
        {LV2_OPTIONS_INSTANCE, 0, map_uri(NULL, "http://lv2plug.in/ns/ext/parameters#sampleRate"), sizeof(float), map_uri(NULL, LV2_ATOM__Float), &rate},
        {LV2_OPTIONS_INSTANCE, 0, 0, 0, 0, NULL}};
    LV2_Feature opts_f = {LV2_OPTIONS__options, opts};
    LV2_Feature bounded = {LV2_BUF_SIZE__boundedBlockLength, NULL};
    LV2_Feature fixed = {LV2_BUF_SIZE__fixedBlockLength, NULL};
    LV2_Feature pow2 = {LV2_BUF_SIZE__powerOf2BlockLength, NULL};
    const LV2_Feature *features[] = {&map_f, &sched_f, &opts_f, &bounded, &fixed, &pow2, NULL};

    instance = lilv_plugin_instantiate(plugin, 48000.0, features);
    if (!instance) { fprintf(stderr, "instantiate failed\n"); return 5; }
    worker_iface = lilv_instance_get_extension_data(instance, LV2_WORKER__interface);

    uint32_t nports = lilv_plugin_get_num_ports(plugin);
    float *controls = calloc(nports, sizeof(float));
    float *mins = calloc(nports, sizeof(float)), *maxs = calloc(nports, sizeof(float)), *defs = calloc(nports, sizeof(float));
    lilv_plugin_get_port_ranges_float(plugin, mins, maxs, defs);
    LilvNode *audio = lilv_new_uri(world, LV2_CORE__AudioPort);
    LilvNode *control = lilv_new_uri(world, LV2_CORE__ControlPort);
    LilvNode *input = lilv_new_uri(world, LV2_CORE__InputPort);
    static float abuf[16][CHUNK];
    int ain[8], aout[8], nin = 0, nout = 0;
    static char atom_dummy[8192];
    for (uint32_t i = 0; i < nports; i++) {
        const LilvPort *p = lilv_plugin_get_port_by_index(plugin, i);
        if (lilv_port_is_a(plugin, p, audio)) {
            if (lilv_port_is_a(plugin, p, input)) ain[nin++] = i; else aout[nout++] = i;
        } else if (lilv_port_is_a(plugin, p, control)) {
            controls[i] = defs[i] != defs[i] ? 0.0f : defs[i];
            const char *sym = lilv_node_as_string(lilv_port_get_symbol(plugin, p));
            LilvNode *pname = lilv_port_get_name(plugin, p);
            const char *name = lilv_node_as_string(pname);
            for (int a = 5; a < argc; a++) {
                const char *eq = strrchr(argv[a], '=');
                if (!eq) continue;
                size_t n = eq - argv[a];
                if ((strlen(sym) == n && !strncmp(argv[a], sym, n)) || (strlen(name) == n && !strncmp(argv[a], name, n))) {
                    float v = atof(eq + 1);
                    /* NORM=1: values are Sushi's normalised 0..1, mapped as Sushi maps them */
                    controls[i] = getenv("NORM") ? mins[i] + v * (maxs[i] - mins[i]) : v;
                    if (getenv("VERBOSE")) fprintf(stderr, "%s = %g\n", name, controls[i]);
                }
            }
            lilv_instance_connect_port(instance, i, &controls[i]);
        } else {
            lilv_instance_connect_port(instance, i, atom_dummy);
        }
    }
    int k = 0;
    for (int i = 0; i < nin; i++) lilv_instance_connect_port(instance, ain[i], abuf[k++]);
    for (int i = 0; i < nout; i++) lilv_instance_connect_port(instance, aout[i], abuf[k++]);

    lilv_instance_activate(instance);
    for (long pos = 0; pos + CHUNK <= frames; pos += CHUNK) {
        for (int i = 0; i < nin && i < nch; i++)
            for (int s = 0; s < CHUNK; s++) abuf[i][s] = in[(pos + s) * nch + i];
        lilv_instance_run(instance, CHUNK);
        for (int c = 0; c < nch; c++)
            for (int s = 0; s < CHUNK; s++)
                out[(pos + s) * nch + c] = c < nout ? abuf[nin + c][s] : 0.0f;
    }
    lilv_instance_deactivate(instance);
    lilv_instance_free(instance);
    f = fopen(argv[3], "wb");
    fwrite(out, sizeof(float), frames * nch, f);
    fclose(f);
    fprintf(stderr, "ins %d outs %d scheduled %d\n", nin, nout, scheduled);
    return 0;
}
