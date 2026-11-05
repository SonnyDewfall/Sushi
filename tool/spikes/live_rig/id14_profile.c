/* id14_profile -- see id14_profile.h. No libusb in this file, on purpose. */
#include "id14_profile.h"

#include <stdio.h>
#include <string.h>

/* [output][position]: Main Mix, Cue Mix, DAW. Audient's panel offers exactly
 * these three sources on this unit; MixiD's five columns (with Alt Spkr and
 * Cue B) belong to a larger iD and send wrong bytes for every column but DAW.
 * The DAW column is the rig's pairing, not Audient's -- see ID14_SRC_DAW_1. */
static const uint8_t route_table[ID14_ROUTE_OUTPUTS][3] = {
    {ID14_SRC_MAINMIX_MAIN_L, ID14_SRC_CUE_L, ID14_SRC_DAW_1},  /* 0 Main L */
    {ID14_SRC_MAINMIX_MAIN_R, ID14_SRC_CUE_R, ID14_SRC_DAW_2},  /* 1 Main R */
    {ID14_SRC_MAINMIX_HP_L,   ID14_SRC_CUE_L, ID14_SRC_DAW_1},  /* 2 HP L: not Audient's 0x02 */
    {ID14_SRC_MAINMIX_HP_R,   ID14_SRC_CUE_R, ID14_SRC_DAW_2},  /* 3 HP R: not Audient's 0x03, dead */
};

static const char *const out_names[ID14_ROUTE_OUTPUTS] = {"Main L", "Main R", "HP L", "HP R"};
static const char *const pos_names[3] = {"Main Mix", "Cue Mix", "DAW"};
static const char *const mix_out_names[ID14_MATRIX_OUTPUTS] = {"Main L", "Main R", "Cue L", "Cue R"};

void id14_db_bytes(double db, uint8_t out[2])
{
    double x = db * 256.0;
    long v = x < 0 ? (long)(x - 0.5) : (long)(x + 0.5);
    if (v < -32768)
        v = -32768;
    if (v > 32767)
        v = 32767;
    uint16_t u = (uint16_t)(int16_t)v;
    out[0] = u & 0xff;
    out[1] = (u >> 8) & 0xff;
}

int id14_matrix_channel(int input, int output)
{
    if (input < 0 || input >= ID14_MATRIX_INPUTS || output < 0 || output >= ID14_MATRIX_OUTPUTS)
        return -1;
    return input * ID14_MATRIX_OUTPUTS + output;
}

static void input_name(int input, char *buf, size_t n)
{
    if (input < 2)
        snprintf(buf, n, "Mic %d", input + 1);
    else if (input < ID14_IN_DAW1)
        snprintf(buf, n, "Digi %d", input - 1);
    else
        snprintf(buf, n, "DAW %d", input - ID14_IN_DAW1 + 1);
}

int id14_route_source(int output, int position)
{
    if (output < 0 || output >= ID14_ROUTE_OUTPUTS || position < 0 || position > 2)
        return -1;
    return route_table[output][position];
}

int id14_route_write(int output, int position, struct id14_write *w)
{
    int src = id14_route_source(output, position);
    if (src < 0)
        return -1;
    memset(w, 0, sizeof *w);
    w->entity = ID14_ENT_ROUTE;
    w->selector = ID14_SEL_ROUTE;
    w->channel = (uint8_t)output;
    w->len = 1;
    w->data[0] = (uint8_t)src;
    if (position == 2)
        snprintf(w->what, sizeof w->what, "route %s <- DAW %d", out_names[output], src + 1);
    else
        snprintf(w->what, sizeof w->what, "route %s <- %s", out_names[output], pos_names[position]);
    return 0;
}

/* A small builder, so the profiles below read as the list they are. */
struct list {
    struct id14_write *w;
    int n, max, overflow;
};

static struct id14_write *next(struct list *l)
{
    if (l->n >= l->max) {
        l->overflow = 1;
        return NULL;
    }
    struct id14_write *w = &l->w[l->n++];
    memset(w, 0, sizeof *w);
    return w;
}

static void add_route(struct list *l, int output, int position)
{
    struct id14_write *w = next(l);
    if (w)
        id14_route_write(output, position, w);
}

static void add_crosspoint(struct list *l, int input, int output, double db)
{
    struct id14_write *w = next(l);
    if (!w)
        return;
    char in[16];
    input_name(input, in, sizeof in);
    w->entity = ID14_ENT_MATRIX;
    w->selector = ID14_SEL_MATRIX;
    w->channel = (uint8_t)id14_matrix_channel(input, output);
    w->len = 2;
    id14_db_bytes(db, w->data);
    snprintf(w->what, sizeof w->what, "matrix %s -> %s %g dB", in, mix_out_names[output], db);
}

static void add_master(struct list *l, int channel, double db)
{
    struct id14_write *w = next(l);
    if (!w)
        return;
    w->entity = ID14_ENT_MIXMASTER;
    w->selector = ID14_SEL_MIXMASTER;
    w->channel = (uint8_t)channel;
    w->len = 2;
    id14_db_bytes(db, w->data);
    snprintf(w->what, sizeof w->what, "Main Mix master ch%d %g dB", channel, db);
}

int id14_profile(const char *name, struct id14_write *w, int max)
{
    struct list l = {w, 0, max, 0};

    if (!strcmp(name, "daw")) {
        /* What MixiD's DAW Mix on rows 1-4 does by hand today. DAW Thru does
         * not use the matrix, so nothing else is written. */
        for (int out = 0; out < ID14_ROUTE_OUTPUTS; out++)
            add_route(&l, out, 2);
    } else if (!strcmp(name, "mainmix")) {
        /* The ordering is the point. Until the last two writes the mains are
         * on whatever they were (DAW Thru after "daw", or the power-on
         * default), so nothing below is heard through them until the matrix
         * and the master already hold their final values. And within the
         * matrix, everything is silenced before anything is raised, so even
         * with the mains already on Main Mix no intermediate state is louder
         * than the final one. Every crosspoint is written, rather than relying
         * on defaults: the write-up found cold-start state unexplained. */
        for (int in = 0; in < ID14_MATRIX_INPUTS; in++)
            for (int out = 0; out < ID14_MATRIX_OUTPUTS; out++)
                add_crosspoint(&l, in, out, -128.0);
        add_master(&l, 1, 0.0);
        add_master(&l, 2, 0.0);
        add_crosspoint(&l, ID14_IN_DAW1, ID14_MIX_MAIN_L, 0.0);
        add_crosspoint(&l, ID14_IN_DAW2, ID14_MIX_MAIN_R, 0.0);
        add_route(&l, ID14_OUT_HP_L, 2);
        add_route(&l, ID14_OUT_HP_R, 2);
        add_route(&l, ID14_OUT_MAIN_L, 0);
        add_route(&l, ID14_OUT_MAIN_R, 0);
    } else {
        return -1;
    }
    return l.overflow ? -2 : l.n;
}

void id14_format_write(const struct id14_write *w, char *buf, size_t n)
{
    char bytes[3 * ID14_MAX_LEN + 1] = "";
    int len = w->len > ID14_MAX_LEN ? ID14_MAX_LEN : w->len;
    size_t pos = 0;
    for (int i = 0; i < len; i++)
        pos += snprintf(bytes + pos, sizeof bytes - pos, i ? " %02x" : "%02x", w->data[i]);
    snprintf(buf, n, "entity 0x%02x selector 0x%02x channel 0x%02x  bytes %-5s  %s",
             w->entity, w->selector, w->channel, bytes, w->what);
}
