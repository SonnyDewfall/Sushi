/* A minimal monophonic pitch-to-MIDI tracker, written for the synth research
 * spike (#166). It is not a candidate for the rig: it exists so that Sushi's
 * MIDI routing can be tested with a tracker whose behaviour is known, and so
 * that "how quickly can a plain mono tracker name a guitar note" has a number.
 *
 * URI urn:sushi-rig:spike:pitch2midi. Ports: audio in, audio out (the input,
 * unchanged), MIDI out (atom), threshold (dB), transpose (semitones), and an
 * output control holding the note it is playing (-1 for none).
 *
 * How it works: the input is low-passed and decimated to 24 kHz; every 128
 * input samples (two of Sushi's chunks) YIN (de Cheveigne and Kawahara 2002)
 * looks for a period between 70 Hz and 1.5 kHz over a 343-sample window.
 * A note starts when the level is over the threshold and two estimates in a
 * row agree within half a semitone; it ends when the level falls 6 dB under
 * the threshold, and is replaced when a new pick (a fast rise in level) or a
 * stable new pitch (a hammer-on, a slide) is found. No pitch bend is sent:
 * a bend is heard as the note it reaches, not the glide.
 */
#include <lv2/atom/atom.h>
#include <lv2/atom/util.h>
#include <lv2/core/lv2.h>
#include <lv2/midi/midi.h>
#include <lv2/urid/urid.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define TRACKER_URI "urn:sushi-rig:spike:pitch2midi"
#define RING 4096
#define MAXLAG 343      /* 24 kHz / 70 Hz */
#define MINLAG 16       /* 24 kHz / 1.5 kHz */
#define WIN 343
#define HOP 128         /* input samples between analyses */

enum { P_IN, P_OUT, P_MIDI, P_THRESHOLD, P_TRANSPOSE, P_NOTE };

typedef struct {
    const float *in;
    float *out;
    LV2_Atom_Sequence *midi;
    const float *threshold, *transpose;
    float *note_out;
    LV2_URID midi_event, sequence;
    uint32_t capacity;
    double rate;
    /* decimating low-pass: two biquads at 2 kHz, Butterworth */
    float b0, b1, b2, a1, a2, z[2][2];
    int phase;
    float ring[RING];
    int head;
    int since;
    /* levels */
    float env_fast, env_slow;
    int onset_pending;
    /* tracker state */
    int note;              /* MIDI note sounding, or -1 */
    float last_pitch;      /* last estimate, in MIDI note units, or -1 */
    int agree;
} Tracker;

static LV2_Handle instantiate(const LV2_Descriptor *d, double rate, const char *path,
                              const LV2_Feature *const *features) {
    (void)d; (void)path;
    LV2_URID_Map *map = NULL;
    for (int i = 0; features[i]; i++)
        if (!strcmp(features[i]->URI, LV2_URID__map)) map = features[i]->data;
    if (!map) return NULL;
    Tracker *t = calloc(1, sizeof(Tracker));
    t->midi_event = map->map(map->handle, LV2_MIDI__MidiEvent);
    t->sequence = map->map(map->handle, LV2_ATOM__Sequence);
    t->rate = rate;
    double w = 2 * M_PI * 2000.0 / rate, alpha = sin(w) / (2 * 0.7071), a0 = 1 + alpha;
    t->b0 = (1 - cos(w)) / 2 / a0; t->b1 = (1 - cos(w)) / a0; t->b2 = t->b0;
    t->a1 = -2 * cos(w) / a0; t->a2 = (1 - alpha) / a0;
    t->note = -1;
    t->last_pitch = -1;
    return t;
}

static void connect(LV2_Handle h, uint32_t port, void *data) {
    Tracker *t = h;
    switch (port) {
    case P_IN: t->in = data; break;
    case P_OUT: t->out = data; break;
    case P_MIDI: t->midi = data; break;
    case P_THRESHOLD: t->threshold = data; break;
    case P_TRANSPOSE: t->transpose = data; break;
    case P_NOTE: t->note_out = data; break;
    }
}

/* The YIN pitch of the newest WIN + MAXLAG samples, in MIDI note units, or -1. */
static float yin(Tracker *t) {
    static float x[WIN + MAXLAG];
    static float d[MAXLAG + 1];
    int n = WIN + MAXLAG;
    for (int i = 0; i < n; i++) x[i] = t->ring[(t->head - n + i + RING) & (RING - 1)];
    float cum = 0;
    d[0] = 1;
    int best = -1;
    for (int tau = 1; tau <= MAXLAG; tau++) {
        float s = 0;
        for (int j = 0; j < WIN; j++) {
            float v = x[j] - x[j + tau];
            s += v * v;
        }
        cum += s;
        d[tau] = cum > 0 ? s * tau / cum : 1;
    }
    for (int tau = MINLAG; tau < MAXLAG; tau++) {
        if (d[tau] < 0.15f) {
            while (tau + 1 < MAXLAG && d[tau + 1] < d[tau]) tau++;
            best = tau;
            break;
        }
    }
    if (best < 0) return -1;
    float a = d[best - 1], b = d[best], c = d[best + 1], den = a - 2 * b + c;
    float period = best + (den != 0 ? 0.5f * (a - c) / den : 0);
    float hz = 24000.0f / period;
    return 69.0f + 12.0f * log2f(hz / 440.0f);
}

static int put(Tracker *t, uint32_t frame, uint8_t status, uint8_t a, uint8_t b) {
    struct { LV2_Atom_Event ev; uint8_t msg[3]; } e;
    e.ev.time.frames = frame;
    e.ev.body.type = t->midi_event;
    e.ev.body.size = 3;
    e.msg[0] = status; e.msg[1] = a; e.msg[2] = b;
    return lv2_atom_sequence_append_event(t->midi, t->capacity, &e.ev) != NULL;
}

static void run(LV2_Handle h, uint32_t n) {
    Tracker *t = h;
    /* The host says how much room there is in the output's size (Sushi: 4096 bytes). */
    t->capacity = t->midi->atom.size;
    t->midi->atom.type = t->sequence;
    t->midi->atom.size = sizeof(LV2_Atom_Sequence_Body);
    t->midi->body.unit = 0;
    t->midi->body.pad = 0;
    float thr = powf(10.0f, *t->threshold / 20.0f);
    float fast_k = expf(-1.0f / (0.001f * t->rate)), slow_k = expf(-1.0f / (0.030f * t->rate));
    for (uint32_t i = 0; i < n; i++) {
        float v = t->in[i];
        t->out[i] = v;
        float a = fabsf(v);
        t->env_fast = a > t->env_fast ? a : t->env_fast * fast_k;
        float prev_slow = t->env_slow;
        t->env_slow = t->env_fast > t->env_slow ? t->env_slow + (t->env_fast - t->env_slow) * 0.02f
                                                : t->env_slow * slow_k;
        if (t->env_fast > 2.5f * prev_slow && t->env_fast > thr) t->onset_pending = 1;
        /* low-pass, then keep every other sample */
        float y = v;
        for (int s = 0; s < 2; s++) {
            float o = t->b0 * y + t->z[s][0];
            t->z[s][0] = t->b1 * y - t->a1 * o + t->z[s][1];
            t->z[s][1] = t->b2 * y - t->a2 * o;
            y = o;
        }
        if ((t->phase ^= 1)) {
            t->ring[t->head] = y;
            t->head = (t->head + 1) & (RING - 1);
        }
        if (++t->since < HOP) continue;
        t->since = 0;
        float level = t->env_slow;
        if (t->note >= 0 && level < 0.5f * thr) {
            put(t, i, 0x80, (uint8_t)t->note, 0);
            t->note = -1;
            t->agree = 0;
        }
        if (level < thr) { t->last_pitch = -1; t->agree = 0; continue; }
        float p = yin(t);
        if (p < 0) { t->agree = 0; t->last_pitch = -1; continue; }
        t->agree = (t->last_pitch >= 0 && fabsf(p - t->last_pitch) < 0.5f) ? t->agree + 1 : 0;
        t->last_pitch = p;
        if (t->agree < 1) continue;
        int target = (int)lrintf(p + *t->transpose);
        if (target < 0 || target > 127) continue;
        int retrigger = t->onset_pending;
        if (t->note == target && !retrigger) continue;
        if (t->note >= 0) put(t, i, 0x80, (uint8_t)t->note, 0);
        float vel = 40 + 87 * fminf(1.0f, level / (thr * 30));
        put(t, i, 0x90, (uint8_t)target, (uint8_t)vel);
        t->note = target;
        t->onset_pending = 0;
    }
    *t->note_out = (float)t->note;
}

static void cleanup(LV2_Handle h) { free(h); }

static const LV2_Descriptor descriptor = {TRACKER_URI, instantiate, connect, NULL, run, NULL, cleanup, NULL};

LV2_SYMBOL_EXPORT const LV2_Descriptor *lv2_descriptor(uint32_t index) {
    return index == 0 ? &descriptor : NULL;
}
