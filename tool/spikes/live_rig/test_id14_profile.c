/* Tests for id14_profile: the bytes idctl sends, checked without an iD14.
 *
 * The expected values are written out literally from the write-up and the
 * Linux results, not computed with the functions under test, so a mistake in
 * the arithmetic cannot agree with itself.
 *
 *   make -f idctl.mk check
 */
#include "id14_profile.h"

#include <stdio.h>
#include <string.h>

static int failures, checks;

#define CHECK(cond, ...) do {                                   \
        checks++;                                               \
        if (!(cond)) {                                          \
            failures++;                                         \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);         \
            printf(__VA_ARGS__);                                \
            printf("\n");                                       \
        }                                                       \
    } while (0)

/* One expected write: entity, selector, channel, len, bytes. */
struct want {
    int e, s, c, len, b0, b1;
};

static void check_write(const char *profile, int i, const struct id14_write *got, const struct want *w)
{
    CHECK(got->entity == w->e && got->selector == w->s && got->channel == w->c && got->len == w->len,
          "%s write %d: got %02x/%02x/%02x len %d, want %02x/%02x/%02x len %d", profile, i + 1,
          got->entity, got->selector, got->channel, got->len, w->e, w->s, w->c, w->len);
    CHECK(got->data[0] == w->b0, "%s write %d: byte 0 %02x, want %02x", profile, i + 1, got->data[0], w->b0);
    if (w->len == 2)
        CHECK(got->data[1] == w->b1, "%s write %d: byte 1 %02x, want %02x", profile, i + 1, got->data[1], w->b1);
    for (int k = w->len; k < ID14_MAX_LEN; k++)
        CHECK(got->data[k] == 0, "%s write %d: stray byte %d = %02x", profile, i + 1, k, got->data[k]);
}

static void test_db_encoding(void)
{
    static const struct { double db; int lo, hi; } cases[] = {
        {0, 0x00, 0x00},        /* from the capture */
        {-5, 0x00, 0xfb},
        {-10, 0x00, 0xf6},
        {-20, 0x00, 0xec},
        {-30, 0x00, 0xe2},
        {-40, 0x00, 0xd8},
        {-60, 0x00, 0xc4},
        {-80, 0x00, 0xb0},
        {-128, 0x00, 0x80},     /* silence */
        {-6, 0x00, 0xfa},       /* the panel's centre pan */
        {-200, 0x00, 0x80},     /* clamped to -128 */
        {6, 0x00, 0x06},        /* the faders' +6 dB top */
        {-0.06, 0xf1, 0xff},    /* 0x36/0x06 at rest, 0xfff1 = -15/256 dB */
    };
    for (unsigned i = 0; i < sizeof cases / sizeof *cases; i++) {
        uint8_t b[2];
        id14_db_bytes(cases[i].db, b);
        CHECK(b[0] == cases[i].lo && b[1] == cases[i].hi,
              "%g dB -> %02x %02x, want %02x %02x", cases[i].db, b[0], b[1], cases[i].lo, cases[i].hi);
    }
}

static void test_matrix_arithmetic(void)
{
    CHECK(id14_matrix_channel(10, 0) == 0x28, "DAW1 -> Main L = 0x%02x, want 0x28", id14_matrix_channel(10, 0));
    CHECK(id14_matrix_channel(11, 1) == 0x2D, "DAW2 -> Main R = 0x%02x, want 0x2D", id14_matrix_channel(11, 1));
    /* The rest of the write-up's DAW table. */
    CHECK(id14_matrix_channel(10, 3) == 0x2B, "DAW1 -> Cue R");
    CHECK(id14_matrix_channel(12, 0) == 0x30, "DAW3 -> Main L");
    CHECK(id14_matrix_channel(13, 3) == 0x37, "DAW4 -> Cue R, the last crosspoint");
    CHECK(id14_matrix_channel(0, 0) == 0x00, "Mic1 -> Main L, the first crosspoint");

    int seen[256] = {0}, count = 0;
    for (int in = 0; in < 14; in++)
        for (int out = 0; out < 4; out++) {
            int ch = id14_matrix_channel(in, out);
            CHECK(ch >= 0 && ch < 56, "input %d output %d -> %d out of 0-55", in, out, ch);
            if (ch >= 0 && ch < 256 && !seen[ch]++)
                count++;
        }
    CHECK(count == 56, "%d distinct crosspoints, want 56 (14 inputs x 4 outputs)", count);
    CHECK(id14_matrix_channel(14, 0) == -1, "input 14 must be out of range");
    CHECK(id14_matrix_channel(0, 4) == -1, "output 4 must be out of range");
    CHECK(id14_matrix_channel(-1, 0) == -1, "input -1 must be out of range");
}

static void test_route_table(void)
{
    /* [output][position]: Main Mix, Cue Mix, DAW. DAW is the rig's 00 01 00 01. */
    static const int want[4][3] = {
        {0x14, 0x10, 0x00},
        {0x15, 0x11, 0x01},
        {0x18, 0x10, 0x00},
        {0x19, 0x11, 0x01},
    };
    for (int o = 0; o < 4; o++)
        for (int p = 0; p < 3; p++)
            CHECK(id14_route_source(o, p) == want[o][p], "route output %d position %d = 0x%02x, want 0x%02x",
                  o, p, id14_route_source(o, p), want[o][p]);
    /* Source 0x03 is dead on this unit: nothing may ever send it. */
    for (int o = 0; o < 4; o++)
        for (int p = 0; p < 3; p++)
            CHECK(id14_route_source(o, p) != 0x03, "output %d position %d sends dead source 0x03", o, p);
    CHECK(id14_route_source(4, 0) == -1, "output 4 does not exist on this unit");
    CHECK(id14_route_source(0, 3) == -1, "there are only three sources");
}

static void test_daw(void)
{
    static const struct want want[] = {
        {0x33, 0x06, 0, 1, 0x00, 0},
        {0x33, 0x06, 1, 1, 0x01, 0},
        {0x33, 0x06, 2, 1, 0x00, 0},
        {0x33, 0x06, 3, 1, 0x01, 0},
    };
    struct id14_write w[ID14_MAX_WRITES];
    int n = id14_profile("daw", w, ID14_MAX_WRITES);
    CHECK(n == 4, "daw: %d writes, want 4", n);
    for (int i = 0; i < n && i < 4; i++)
        check_write("daw", i, &w[i], &want[i]);
}

static void test_mainmix(void)
{
    struct want want[64];
    int k = 0;
    /* 1. Every crosspoint silent, channel order 0x00-0x37. */
    for (int ch = 0; ch < 56; ch++)
        want[k++] = (struct want){0x3C, 0x01, ch, 2, 0x00, 0x80};
    /* 2. Main Mix master, channels 1 and 2, 0 dB. */
    want[k++] = (struct want){0x0C, 0x02, 1, 2, 0x00, 0x00};
    want[k++] = (struct want){0x0C, 0x02, 2, 2, 0x00, 0x00};
    /* 3. DAW 1 -> Main L and DAW 2 -> Main R at 0 dB. */
    want[k++] = (struct want){0x3C, 0x01, 0x28, 2, 0x00, 0x00};
    want[k++] = (struct want){0x3C, 0x01, 0x2D, 2, 0x00, 0x00};
    /* 4. Headphones as in daw, then 5. the mains to Main Mix, last. */
    want[k++] = (struct want){0x33, 0x06, 2, 1, 0x00, 0};
    want[k++] = (struct want){0x33, 0x06, 3, 1, 0x01, 0};
    want[k++] = (struct want){0x33, 0x06, 0, 1, 0x14, 0};
    want[k++] = (struct want){0x33, 0x06, 1, 1, 0x15, 0};

    struct id14_write w[ID14_MAX_WRITES];
    int n = id14_profile("mainmix", w, ID14_MAX_WRITES);
    CHECK(n == 64, "mainmix: %d writes, want 64", n);
    for (int i = 0; i < n && i < 64; i++)
        check_write("mainmix", i, &w[i], &want[i]);

    /* The final state, whatever the order: each crosspoint's last write. */
    int last[56];
    for (int ch = 0; ch < 56; ch++)
        last[ch] = -1;
    for (int i = 0; i < n; i++)
        if (w[i].entity == 0x3C && w[i].channel < 56)
            last[w[i].channel] = w[i].data[0] | w[i].data[1] << 8;
    for (int ch = 0; ch < 56; ch++) {
        int target = (ch == 0x28 || ch == 0x2D) ? 0x0000 : 0x8000;
        CHECK(last[ch] == target, "crosspoint 0x%02x ends at %04x, want %04x", ch, last[ch], target);
    }

    /* Never loud: the mains switch to Main Mix only after every matrix and
     * master write, and nothing is raised in the matrix until all is silent. */
    int first_main_route = -1, last_mix_write = -1, first_raise = -1, last_silence = -1;
    for (int i = 0; i < n; i++) {
        if (w[i].entity == 0x33 && w[i].channel <= 1 && first_main_route < 0)
            first_main_route = i;
        if (w[i].entity == 0x3C || w[i].entity == 0x0C)
            last_mix_write = i;
        if (w[i].entity == 0x3C && w[i].data[1] != 0x80 && first_raise < 0)
            first_raise = i;
        if (w[i].entity == 0x3C && w[i].data[1] == 0x80)
            last_silence = i;
    }
    CHECK(first_main_route > last_mix_write, "mains switched (write %d) before the mix was set (write %d)",
          first_main_route + 1, last_mix_write + 1);
    CHECK(first_raise > last_silence, "a crosspoint raised (write %d) before all were silent (write %d)",
          first_raise + 1, last_silence + 1);
}

static void test_profile_errors(void)
{
    struct id14_write w[ID14_MAX_WRITES];
    CHECK(id14_profile("nope", w, ID14_MAX_WRITES) == -1, "unknown profile must be -1");
    CHECK(id14_profile("mainmix", w, 10) == -2, "too small a buffer must be -2");
    CHECK(id14_profile("daw", w, 4) == 4, "daw fits exactly 4");
}

static void test_format(void)
{
    struct id14_write w;
    char line[160];
    id14_route_write(3, 2, &w);
    id14_format_write(&w, line, sizeof line);
    const char *want = "entity 0x33 selector 0x06 channel 0x03  bytes 01     route HP R <- DAW 2";
    CHECK(!strcmp(line, want), "format:\n  got  '%s'\n  want '%s'", line, want);

    /* Two-byte values must show both bytes, low first: the listing is what
     * Sonny checks by eye before anything is sent. */
    struct id14_write m[ID14_MAX_WRITES];
    id14_profile("mainmix", m, ID14_MAX_WRITES);
    id14_format_write(&m[0], line, sizeof line);
    want = "entity 0x3c selector 0x01 channel 0x00  bytes 00 80  matrix Mic 1 -> Main L -128 dB";
    CHECK(!strcmp(line, want), "format:\n  got  '%s'\n  want '%s'", line, want);
    id14_format_write(&m[58], line, sizeof line);
    want = "entity 0x3c selector 0x01 channel 0x28  bytes 00 00  matrix DAW 1 -> Main L 0 dB";
    CHECK(!strcmp(line, want), "format:\n  got  '%s'\n  want '%s'", line, want);

    /* Longer values, as "idctl set" makes: 4 bytes, sample rate 44100. */
    memset(&w, 0, sizeof w);
    w.entity = 0x29; w.selector = 0x01; w.len = 4;
    w.data[0] = 0x44; w.data[1] = 0xac;
    id14_format_write(&w, line, sizeof line);
    want = "entity 0x29 selector 0x01 channel 0x00  bytes 44 ac 00 00  ";
    CHECK(!strcmp(line, want), "format:\n  got  '%s'\n  want '%s'", line, want);
}

static void test_parse(void)
{
    long v = -99;
    /* Accepted: any C base, the whole string. */
    CHECK(id14_parse_long("2", 0, 3, &v) == 0 && v == 2, "'2' -> %ld", v);
    CHECK(id14_parse_long("0x2D", 0, 255, &v) == 0 && v == 0x2D, "'0x2D' -> %ld", v);
    CHECK(id14_parse_long("010", 0, 255, &v) == 0 && v == 8, "'010' is octal 8, got %ld", v);
    /* Refused, with v untouched. */
    static const char *bad[] = {"HP_L", "", "2x", "2 ", " x", "0x", "4", "-1", "99999999999999999999"};
    for (unsigned i = 0; i < sizeof bad / sizeof *bad; i++) {
        v = -99;
        CHECK(id14_parse_long(bad[i], 0, 3, &v) == -1 && v == -99, "'%s' must be refused for 0-3", bad[i]);
    }
    CHECK(id14_parse_long(NULL, 0, 3, &v) == -1, "NULL must be refused");

    uint8_t b[ID14_MAX_LEN];
    CHECK(id14_parse_value("0xac44", 4, b) == 0 && b[0] == 0x44 && b[1] == 0xac && b[2] == 0 && b[3] == 0,
          "0xac44 in 4 bytes -> %02x %02x %02x %02x", b[0], b[1], b[2], b[3]);
    CHECK(id14_parse_value("0x8000", 2, b) == 0 && b[0] == 0x00 && b[1] == 0x80, "0x8000 in 2 bytes");
    CHECK(id14_parse_value("255", 1, b) == 0 && b[0] == 0xff, "255 in 1 byte");
    CHECK(id14_parse_value("256", 1, b) == -1, "256 does not fit in 1 byte");
    CHECK(id14_parse_value("0x10000", 2, b) == -1, "0x10000 does not fit in 2 bytes");
    CHECK(id14_parse_value("0xffffffffffffffff", 8, b) == 0 && b[7] == 0xff, "8 bytes of ff");
    CHECK(id14_parse_value("-1", 2, b) == -1, "negative values are refused");
    CHECK(id14_parse_value(" -1", 8, b) == -1, "' -1' is refused: strtoull would skip the space and wrap it");
    CHECK(id14_parse_value(" 5", 1, b) == -1, "leading whitespace is refused");
    CHECK(id14_parse_value("+5", 1, b) == -1, "a leading '+' is refused");
    CHECK(id14_parse_long(" 5", 0, 9, &v) == -1, "leading whitespace is refused by parse_long");
    CHECK(id14_parse_long("+5", 0, 9, &v) == -1, "a leading '+' is refused by parse_long");
    CHECK(id14_parse_long("-5", 0, 9, &v) == -1, "'-5' is refused for an unsigned range");
    CHECK(id14_parse_long("-5", -9, 9, &v) == 0 && v == -5, "'-5' is accepted for a signed range");
    CHECK(id14_parse_value("12abc", 2, b) == -1, "trailing garbage is refused");
    CHECK(id14_parse_value("1", 9, b) == -1, "len 9 is beyond ID14_MAX_LEN");
    CHECK(id14_parse_value("1", 0, b) == -1, "len 0 is refused");
}

static void test_exit_status(void)
{
    CHECK(id14_exit_status(4, 4, 0, 0) == 0, "all ok");
    CHECK(id14_exit_status(4, 4, 0, 15) == 0, "signal after the last ok write is success");
    CHECK(id14_exit_status(64, 6, 0, 15) == 143, "signal mid-run is 128+15");
    CHECK(id14_exit_status(64, 2, 0, 2) == 130, "SIGINT mid-run is 128+2");
    CHECK(id14_exit_status(4, 2, 1, 0) == 1, "a failed write is 1");
    CHECK(id14_exit_status(4, 2, 1, 15) == 1, "a failed write is 1 even with a signal");
    CHECK(id14_exit_status(4, 4, 1, 0) == 1, "a failed last write is 1");
}

int main(void)
{
    test_db_encoding();
    test_matrix_arithmetic();
    test_route_table();
    test_daw();
    test_mainmix();
    test_profile_errors();
    test_format();
    test_parse();
    test_exit_status();
    printf("%d checks, %d failures\n", checks, failures);
    return failures != 0;
}
