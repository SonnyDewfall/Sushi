/* A MIDI-to-audio probe for the synth research spike (#166): a measuring
 * instrument, not a synth. Put where a synth would go in a Sushi chain, it
 * turns the MIDI that reaches it into a sine per held note at a frequency
 * that names the note, 300 Hz + 100 Hz x (note - 24), so that the render can
 * be read back as a piano roll (midi_synth.py `piano_roll`): which notes,
 * when, how many at once. 100 Hz apart, the notes stay apart even when they
 * are an octave or a semitone apart on a guitar.
 *
 * URI urn:sushi-rig:spike:midiprobe. Ports: MIDI in (atom), audio out.
 * Each note is 0.02 in amplitude whatever its velocity, switched with a
 * 1 ms ramp; note 24 (C1) to 119 (B7) only.
 */
#include <lv2/atom/atom.h>
#include <lv2/atom/util.h>
#include <lv2/core/lv2.h>
#include <lv2/midi/midi.h>
#include <lv2/urid/urid.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define PROBE_URI "urn:sushi-rig:spike:midiprobe"
#define LOW 24
#define HIGH 119

typedef struct {
    const LV2_Atom_Sequence *in;
    float *out;
    LV2_URID midi_event;
    double rate;
    int held[HIGH + 1];
    double phase[HIGH + 1];
    float gain[HIGH + 1];
} Probe;

static LV2_Handle instantiate(const LV2_Descriptor *d, double rate, const char *path,
                              const LV2_Feature *const *features) {
    (void)d; (void)path;
    LV2_URID_Map *map = NULL;
    for (int i = 0; features[i]; i++)
        if (!strcmp(features[i]->URI, LV2_URID__map)) map = features[i]->data;
    if (!map) return NULL;
    Probe *p = calloc(1, sizeof(Probe));
    p->midi_event = map->map(map->handle, LV2_MIDI__MidiEvent);
    p->rate = rate;
    return p;
}

static void connect(LV2_Handle h, uint32_t port, void *data) {
    Probe *p = h;
    if (port == 0) p->in = data;
    else if (port == 1) p->out = data;
}

static void render(Probe *p, uint32_t from, uint32_t to) {
    float step = 1.0f / (0.001f * (float)p->rate);
    for (uint32_t i = from; i < to; i++) {
        float s = 0;
        for (int n = LOW; n <= HIGH; n++) {
            float target = p->held[n] ? 0.02f : 0.0f;
            if (p->gain[n] < target) p->gain[n] = fminf(target, p->gain[n] + 0.02f * step);
            else if (p->gain[n] > target) p->gain[n] = fmaxf(target, p->gain[n] - 0.02f * step);
            if (p->gain[n] > 0) {
                double f = 300.0 + 100.0 * (n - LOW);
                p->phase[n] += 2 * M_PI * f / p->rate;
                if (p->phase[n] > 2 * M_PI) p->phase[n] -= 2 * M_PI;
                s += p->gain[n] * (float)sin(p->phase[n]);
            }
        }
        p->out[i] = s;
    }
}

static void run(LV2_Handle h, uint32_t n) {
    Probe *p = h;
    uint32_t at = 0;
    LV2_ATOM_SEQUENCE_FOREACH(p->in, ev) {
        if (ev->body.type != p->midi_event) continue;
        const uint8_t *m = (const uint8_t *)(ev + 1);
        uint32_t t = (uint32_t)ev->time.frames;
        if (t > n) t = n;
        render(p, at, t);
        at = t;
        uint8_t kind = m[0] & 0xF0;
        if (m[1] >= LOW && m[1] <= HIGH) {
            if (kind == 0x90 && m[2] > 0) p->held[m[1]] = 1;
            else if (kind == 0x80 || (kind == 0x90 && m[2] == 0)) p->held[m[1]] = 0;
        }
        if (kind == 0xB0 && (m[1] == 123 || m[1] == 120)) memset(p->held, 0, sizeof p->held);
    }
    render(p, at, n);
}

static void cleanup(LV2_Handle h) { free(h); }

static const LV2_Descriptor descriptor = {PROBE_URI, instantiate, connect, NULL, run, NULL, cleanup, NULL};

LV2_SYMBOL_EXPORT const LV2_Descriptor *lv2_descriptor(uint32_t index) {
    return index == 0 ? &descriptor : NULL;
}
