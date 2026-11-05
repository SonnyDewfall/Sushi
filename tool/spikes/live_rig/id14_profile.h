/* id14_profile -- what idctl writes to an Audient iD14 MKI, without touching USB.
 *
 * Everything here is plain C with no libusb, so the bytes can be tested on a
 * machine with no iD14 (test_id14_profile.c). idctl.c is the only thing that
 * sends them.
 *
 * Protocol from RESULTS-id14-windows-2026-11-04.md: UAC2 SET_CUR on interface 0,
 * wValue = (selector << 8) | channel, wIndex = (entity << 8) | interface.
 */
#ifndef ID14_PROFILE_H
#define ID14_PROFILE_H

#include <stddef.h>
#include <stdint.h>

/* Routing: one byte per output, channels 0-3 only. Cannot be read back (GET_CUR
 * STALLs) and resets on every USB disconnect. */
#define ID14_ENT_ROUTE      0x33
#define ID14_SEL_ROUTE      0x06
#define ID14_OUT_MAIN_L     0
#define ID14_OUT_MAIN_R     1
#define ID14_OUT_HP_L       2
#define ID14_OUT_HP_R       3
#define ID14_ROUTE_OUTPUTS  4

/* Routing sources. Main Mix and Cue Mix are Audient's values, read off the
 * capture of its panel. Note that Main Mix differs per output pair and Cue Mix
 * does not. */
#define ID14_SRC_MAINMIX_MAIN_L 0x14
#define ID14_SRC_MAINMIX_MAIN_R 0x15
#define ID14_SRC_MAINMIX_HP_L   0x18
#define ID14_SRC_MAINMIX_HP_R   0x19
#define ID14_SRC_CUE_L          0x10
#define ID14_SRC_CUE_R          0x11

/* DAW Thru. The routing values here are iD14-MKI-specific and deliberately NOT
 * Audient's. Audient's panel sends 00 01 to the mains and 02 03 to the
 * headphones (DAW 3+4, for a four-output DAW rig), but on this unit source 0x03
 * acknowledges the write and passes nothing: shown by elimination on Linux,
 * 2026-11-04, on an output proven good moments before. So every output takes
 * stream channels 1 and 2, and the headphones carry the same pair as the mains:
 * 00 01 00 01. Do not "correct" the headphones to 02 03. */
#define ID14_SRC_DAW_1          0x00
#define ID14_SRC_DAW_2          0x01

/* Mixer matrix: 2-byte levels, channel = input * 4 + output. Second-hand, from
 * the Windows write-up: confirmed there by sweeps of DAW 1-4, and "the rest
 * follows the pattern". */
#define ID14_ENT_MATRIX     0x3C
#define ID14_SEL_MATRIX     0x01
#define ID14_MATRIX_INPUTS  14  /* 0-1 Mic, 2-9 Digital, 10-13 DAW 1-4 */
#define ID14_MATRIX_OUTPUTS 4   /* 0 Main L, 1 Main R, 2 Cue L, 3 Cue R */
#define ID14_IN_DAW1        10
#define ID14_IN_DAW2        11
#define ID14_MIX_MAIN_L     0
#define ID14_MIX_MAIN_R     1

/* Main Mix master, channels 1 and 2 (UAC2 channel 0 is the master channel and
 * is not what the panel writes). */
#define ID14_ENT_MIXMASTER  0x0C
#define ID14_SEL_MIXMASTER  0x02

/* The longest profile is mainmix: 56 + 2 + 2 + 4 = 64 writes. */
#define ID14_MAX_WRITES     80
#define ID14_MAX_LEN        8

struct id14_write {
    uint8_t entity;
    uint8_t selector;
    uint8_t channel;
    uint8_t len;        /* 1 to ID14_MAX_LEN; the profiles use 1 and 2 */
    uint8_t data[ID14_MAX_LEN];    /* little-endian */
    char what[48];      /* human description, for the listing */
};

/* UAC2 signed 8.8 fixed-point dB, little-endian: 0 dB is 00 00, -5 dB is 00 fb,
 * -128 dB (silence) is 00 80. Rounds to the nearest 1/256 dB and clamps to the
 * representable range, -128 to +127.996. */
void id14_db_bytes(double db, uint8_t out[2]);

/* Matrix channel for (input, output), or -1 if either is out of range. */
int id14_matrix_channel(int input, int output);

/* Fill w[] with the writes for a named profile, in the order they must be sent.
 * Returns how many, -1 for an unknown name, -2 if max is too small.
 *   "daw"      all four outputs on DAW Thru, 00 01 00 01 (what the rig uses)
 *   "mainmix"  mains on Main Mix fed by DAW 1 -> L and DAW 2 -> R at 0 dB,
 *              headphones as in daw; matrix and master written before the
 *              mains switch, so a half-applied profile is never loud */
int id14_profile(const char *name, struct id14_write *w, int max);

/* One line describing a write, without a newline:
 *   entity 0x33 selector 0x06 channel 0x00  bytes 00     route Main L <- DAW 1 */
void id14_format_write(const struct id14_write *w, char *buf, size_t n);

/* A single routing write, for "idctl route". position 0 Main Mix, 1 Cue Mix,
 * 2 DAW. Returns 0, or -1 if output or position is out of range. */
int id14_route_write(int output, int position, struct id14_write *w);

/* The byte that position means on that output, or -1 if out of range. */
int id14_route_source(int output, int position);

/* Strict number parsing for the command line: base 0 (so 10, 0x0a and 012 all
 * work), the whole string must be the number, and it must lie in [lo, hi].
 * Returns 0, or -1 with *out untouched. */
int id14_parse_long(const char *s, long lo, long hi, long *out);

/* A value for "idctl set": a non-negative number, strict as above, that fits in
 * len bytes (1 to ID14_MAX_LEN), stored little-endian in out. Returns 0 or -1. */
int id14_parse_value(const char *s, int len, uint8_t out[ID14_MAX_LEN]);

/* idctl's exit status after a run of n writes, of which done were attempted:
 *   1        a write failed (whether or not a signal also arrived)
 *   128+sig  a signal stopped the run before every write was sent
 *   0        every write was ok -- even if a signal arrived after the last one,
 *            because the device then holds the whole profile */
int id14_exit_status(int n, int done, int failed, int sig);

#endif
