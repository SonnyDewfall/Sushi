/* A pacing plugin for the synth research spike (#166): a measuring aid. It
 * passes its two channels through and, in run(), sleeps until the wall clock
 * has caught up with the audio it has passed. First on a track in Sushi's
 * offline frontend, which otherwise renders as fast as it can, it makes the
 * whole engine run at real time, so that a plugin which works on a thread of
 * its own (GuitarMidi-LV2's neural network) has the time it would have live.
 * Sleeping in run() is wrong anywhere but here.
 *
 * URI urn:sushi-rig:spike:pacer. Ports: two audio in, two audio out.
 */
#define _POSIX_C_SOURCE 200809L
#include <lv2/core/lv2.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    const float *in[2];
    float *out[2];
    double rate;
    long long frames;
    struct timespec start;
    int started;
} Pacer;

static LV2_Handle instantiate(const LV2_Descriptor *d, double rate, const char *path,
                              const LV2_Feature *const *features) {
    (void)d; (void)path; (void)features;
    Pacer *p = calloc(1, sizeof(Pacer));
    p->rate = rate;
    return p;
}

static void connect(LV2_Handle h, uint32_t port, void *data) {
    Pacer *p = h;
    if (port < 2) p->in[port] = data;
    else if (port < 4) p->out[port - 2] = data;
}

static void run(LV2_Handle h, uint32_t n) {
    Pacer *p = h;
    for (int c = 0; c < 2; c++)
        if (p->out[c] != p->in[c]) memmove(p->out[c], p->in[c], n * sizeof(float));
    if (!p->started) {
        clock_gettime(CLOCK_MONOTONIC, &p->start);
        p->started = 1;
    }
    p->frames += n;
    double t = p->frames / p->rate;
    struct timespec due = p->start;
    due.tv_sec += (time_t)t;
    due.tv_nsec += (long)((t - (double)(time_t)t) * 1e9);
    if (due.tv_nsec >= 1000000000L) { due.tv_sec++; due.tv_nsec -= 1000000000L; }
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &due, NULL);
}

static void cleanup(LV2_Handle h) { free(h); }

static const LV2_Descriptor descriptor = {"urn:sushi-rig:spike:pacer", instantiate, connect, NULL, run, NULL, cleanup, NULL};

LV2_SYMBOL_EXPORT const LV2_Descriptor *lv2_descriptor(uint32_t index) {
    return index == 0 ? &descriptor : NULL;
}
