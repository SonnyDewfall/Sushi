/* Round-trip latency over a loopback, measured by cross-correlation, plus the
 * check that decides whether the result means anything.
 *
 * Playback and capture are opened on one card and snd_pcm_link'd, so a single
 * start is meant to run both off the same trigger: output frame N and input
 * frame N simultaneous, making the round trip the shift between them with no
 * process-start skew to correct for. That assumption is the whole measurement,
 * so this program also reports each stream's trigger timestamp. If the two
 * differ, the difference is a constant offset in every figure here and must be
 * subtracted -- see report_triggers.
 *
 * A chirp is emitted at a known absolute output frame. This program only
 * records; the shift is found offline by correlating the capture against the
 * same chirp. An earlier version detected the burst by thresholding absolute
 * sample value and gave nonsense -- a negative latency once, and, in another
 * run, a threshold sitting above the burst's own amplitude so it could never
 * fire at all. Correlation needs no threshold and tolerates a noisy input.
 *
 * Usage:
 *   rtlat <dev> <period> <periods> <dbfs> <prefix> [rate] [play_ch] [cap_ch] [fmt]
 *   fmt is S16_LE (default) or S32_LE.
 *
 * Playback and capture channel counts are separate because they differ on real
 * devices: the Nanocore is stereo both ways, the iD14 has 4 out and 10 in.
 *
 * Writes <prefix>.cap (raw capture, native format, all channels interleaved)
 * and <prefix>.ref (the chirp as raw S16 mono), and prints what the analysis
 * needs as key=value lines.
 */
#include <alsa/asoundlib.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHIRP_N 2048
#define F0      400.0
#define F1      6000.0

static unsigned RATE = 44100;
static snd_pcm_format_t FMT = SND_PCM_FORMAT_S16_LE;
static int SAMPLE_BYTES = 2;

static int cfg(snd_pcm_t *pcm, snd_pcm_uframes_t *period, unsigned periods,
               unsigned *channels, const char *what)
{
    snd_pcm_hw_params_t *hw;
    snd_pcm_sw_params_t *sw;
    snd_pcm_uframes_t buffer = *period * periods;
    unsigned rate = RATE;
    int err;

    snd_pcm_hw_params_alloca(&hw);
    if ((err = snd_pcm_hw_params_any(pcm, hw)) < 0) goto bad;
    if ((err = snd_pcm_hw_params_set_access(pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0) goto bad;
    if ((err = snd_pcm_hw_params_set_format(pcm, hw, FMT)) < 0) goto bad;
    if ((err = snd_pcm_hw_params_set_channels_near(pcm, hw, channels)) < 0) goto bad;
    if ((err = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate, 0)) < 0) goto bad;
    if ((err = snd_pcm_hw_params_set_period_size_near(pcm, hw, period, 0)) < 0) goto bad;
    if ((err = snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer)) < 0) goto bad;
    if ((err = snd_pcm_hw_params(pcm, hw)) < 0) goto bad;

    snd_pcm_sw_params_alloca(&sw);
    if ((err = snd_pcm_sw_params_current(pcm, sw)) < 0) goto bad;
    /* Never auto-start: the linked pair is started explicitly, together. */
    if ((err = snd_pcm_sw_params_set_start_threshold(pcm, sw, 0x7fffffff)) < 0) goto bad;
    if ((err = snd_pcm_sw_params_set_tstamp_mode(pcm, sw, SND_PCM_TSTAMP_ENABLE)) < 0) goto bad;
    if ((err = snd_pcm_sw_params_set_tstamp_type(pcm, sw, SND_PCM_TSTAMP_TYPE_MONOTONIC)) < 0) goto bad;
    if ((err = snd_pcm_sw_params(pcm, sw)) < 0) goto bad;

    snd_pcm_hw_params_current(pcm, hw);
    snd_pcm_hw_params_get_period_size(hw, period, 0);
    snd_pcm_hw_params_get_buffer_size(hw, &buffer);
    printf("%s_channels=%u\n%s_period=%lu\n%s_buffer=%lu\n%s_rate=%u\n",
           what, *channels, what, (unsigned long)*period,
           what, (unsigned long)buffer, what, rate);
    return 0;
bad:
    fprintf(stderr, "%s: %s\n", what, snd_strerror(err));
    return err;
}

/* The start instant of each stream, as the driver recorded it.
 *
 * snd_pcm_link reports success on snd-usb-audio, but success only means the
 * two substreams share a trigger call -- an isochronous stream still begins at
 * a USB frame boundary, and the two directions need not begin at the same one.
 * Any skew here is a constant offset in every round trip this program reports.
 * Zero means output frame N and input frame N really are simultaneous and the
 * raw figure stands; a non-zero value must be subtracted from it. */
static void report_triggers(snd_pcm_t *play, snd_pcm_t *cap)
{
    snd_pcm_status_t *st;
    snd_htimestamp_t tp, tc;
    snd_pcm_status_alloca(&st);

    if (snd_pcm_status(play, st) < 0) { printf("trigger=unavailable\n"); return; }
    snd_pcm_status_get_trigger_htstamp(st, &tp);
    if (snd_pcm_status(cap, st) < 0) { printf("trigger=unavailable\n"); return; }
    snd_pcm_status_get_trigger_htstamp(st, &tc);

    double us_p = tp.tv_sec * 1e6 + tp.tv_nsec / 1e3;
    double us_c = tc.tv_sec * 1e6 + tc.tv_nsec / 1e3;
    printf("trigger_play_us=%.0f\ntrigger_cap_us=%.0f\n"
           "trigger_skew_us=%.1f\ntrigger_skew_frames=%.1f\n",
           us_p, us_c, us_c - us_p, (us_c - us_p) * RATE / 1e6);
}

/* What the driver itself believes is in flight: a second opinion, from a
 * different source than the correlation. These cover the ALSA buffer and the
 * URB queue but nothing inside the device, so a delay_sum far below the
 * measured round trip puts the remainder inside the hardware. */
static void report_delays(snd_pcm_t *play, snd_pcm_t *cap)
{
    snd_pcm_sframes_t dp = 0, dc = 0;
    if (snd_pcm_delay(play, &dp) < 0) dp = -1;
    if (snd_pcm_delay(cap, &dc) < 0) dc = -1;
    printf("delay_play_frames=%ld\ndelay_cap_frames=%ld\ndelay_sum_ms=%.2f\n",
           (long)dp, (long)dc, 1000.0 * (dp + dc) / RATE);
}

/* Chirp sample -> the device's format, written to channels 0 and 1 only. */
static void put(void *buf, long frame, unsigned channels, double v)
{
    for (unsigned c = 0; c < channels && c < 2; c++) {
        long i = frame * channels + c;
        if (SAMPLE_BYTES == 2) ((short *)buf)[i] = (short)(v * 32767.0);
        else                   ((int   *)buf)[i] = (int)(v * 2147483647.0);
    }
}

int main(int argc, char **argv)
{
    const char *dev    = argc > 1 ? argv[1] : "hw:4,0";
    snd_pcm_uframes_t period = argc > 2 ? (snd_pcm_uframes_t)atoi(argv[2]) : 128;
    unsigned periods   = argc > 3 ? (unsigned)atoi(argv[3]) : 3;
    double dbfs        = argc > 4 ? atof(argv[4]) : -12.0;
    const char *prefix = argc > 5 ? argv[5] : "rt";
    if (argc > 6) RATE = (unsigned)atoi(argv[6]);
    unsigned ch_play   = argc > 7 ? (unsigned)atoi(argv[7]) : 2;
    unsigned ch_cap    = argc > 8 ? (unsigned)atoi(argv[8]) : 2;
    if (argc > 9 && strcmp(argv[9], "S32_LE") == 0) {
        FMT = SND_PCM_FORMAT_S32_LE;
        SAMPLE_BYTES = 4;
    }

    snd_pcm_t *play, *cap;
    int err;
    if ((err = snd_pcm_open(&play, dev, SND_PCM_STREAM_PLAYBACK, 0)) < 0) {
        fprintf(stderr, "open playback %s: %s\n", dev, snd_strerror(err)); return 1; }
    if ((err = snd_pcm_open(&cap, dev, SND_PCM_STREAM_CAPTURE, 0)) < 0) {
        fprintf(stderr, "open capture %s: %s\n", dev, snd_strerror(err)); return 1; }

    snd_pcm_uframes_t p_play = period, p_cap = period;
    if (cfg(play, &p_play, periods, &ch_play, "playback") < 0) return 1;
    if (cfg(cap,  &p_cap,  periods, &ch_cap,  "capture")  < 0) return 1;
    int linked = snd_pcm_link(cap, play) >= 0;
    printf("linked=%d\n", linked);
    if (!linked) { fprintf(stderr, "link failed -- timing would be meaningless\n"); return 1; }

    /* Chirp: broadband, so correlation has one sharp unambiguous peak. */
    double *chirp = calloc(CHIRP_N, sizeof(double));
    short *chirp16 = calloc(CHIRP_N, sizeof(short));
    double amp = pow(10.0, dbfs / 20.0);
    for (int n = 0; n < CHIRP_N; n++) {
        double t = (double)n / RATE, T = (double)CHIRP_N / RATE;
        double phase = 2 * M_PI * (F0 * t + (F1 - F0) * t * t / (2 * T));
        double w = 0.5 - 0.5 * cos(2 * M_PI * n / CHIRP_N);  /* no clicks */
        chirp[n] = amp * w * sin(phase);
        chirp16[n] = (short)(chirp[n] * 32767.0);
    }

    long settle  = RATE * 3 / 4;                              /* ~0.75 s first */
    long total   = RATE * 2;                                  /* ~2 s captured */
    long emit_at = ((settle + p_play - 1) / p_play) * p_play;  /* period boundary */

    void *out = calloc(p_play * ch_play, SAMPLE_BYTES);
    void *in  = calloc(p_cap  * ch_cap,  SAMPLE_BYTES);
    char capname[512], refname[512];
    snprintf(capname, sizeof capname, "%s.cap", prefix);
    snprintf(refname, sizeof refname, "%s.ref", prefix);
    FILE *fc = fopen(capname, "wb"), *fr = fopen(refname, "wb");
    if (!fc || !fr) { fprintf(stderr, "cannot write %s/%s\n", capname, refname); return 1; }
    fwrite(chirp16, sizeof(short), CHIRP_N, fr);
    fclose(fr);

    long written = 0;
    for (unsigned i = 0; i < periods; i++) {   /* prefill, else instant underrun */
        snd_pcm_writei(play, out, p_play);
        written += p_play;
    }
    if ((err = snd_pcm_start(cap)) < 0) {
        fprintf(stderr, "start: %s\n", snd_strerror(err)); return 1; }

    long read_frames = 0, xruns = 0;
    int reported = 0;
    while (read_frames < total) {
        memset(out, 0, p_play * ch_play * SAMPLE_BYTES);
        if (written <= emit_at && emit_at < written + (long)p_play) {
            for (long n = 0; n < CHIRP_N; n++) {
                long idx = emit_at - written + n;
                if (idx < 0 || idx >= (long)p_play) continue;
                put(out, idx, ch_play, chirp[n]);
            }
        }
        snd_pcm_sframes_t w = snd_pcm_writei(play, out, p_play);
        if (w < 0) { xruns++; snd_pcm_recover(play, w, 1); }
        written += p_play;

        snd_pcm_sframes_t got = snd_pcm_readi(cap, in, p_cap);
        if (got < 0) { xruns++; snd_pcm_recover(cap, got, 1); continue; }
        fwrite(in, SAMPLE_BYTES, got * ch_cap, fc);
        read_frames += got;

        if (!reported && read_frames >= (long)(p_cap * 4)) {
            report_triggers(play, cap);
            report_delays(play, cap);
            reported = 1;
        }
    }
    fclose(fc);

    printf("device=%s\nformat=%s\nchirp_n=%d\nchirp_dbfs=%.1f\nemit_at=%ld\n"
           "frames_captured=%ld\nxruns=%ld\nrate=%u\n",
           dev, SAMPLE_BYTES == 2 ? "S16_LE" : "S32_LE", CHIRP_N, dbfs,
           emit_at, read_frames, xruns, RATE);
    snd_pcm_close(play);
    snd_pcm_close(cap);
    return 0;
}
