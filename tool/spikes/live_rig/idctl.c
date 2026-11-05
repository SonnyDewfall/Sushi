/* idctl -- minimal scriptable control of an Audient iD14 MKI (2708:0002).
 *
 * MixiD needs a GUI and a human; this needs neither, so a routing change can sit
 * inside a measurement loop or run at connect. It speaks the same requests: UAC2
 * SET_CUR/GET_CUR on interface 0, wValue = (selector << 8) | channel,
 * wIndex = (entity << 8) | iface. What it writes comes from id14_profile.c,
 * which has no libusb in it and is tested by test_id14_profile.c.
 *
 *   idctl [--dry-run] [--gap-ms N] apply [daw|mainmix]   claim once, every write, tear down once
 *   idctl [--dry-run] route <output 0-3> <position 0-2>  0 Main Mix, 1 Cue Mix, 2 DAW
 *   idctl [--dry-run] set <entity> <sel> <ch> <len 1-8> <value>
 *   idctl get <entity> <sel> <ch> <len 1-64>
 *   idctl scan                                           GET_CUR across entities and selectors
 *
 * Numbers take any C base (10, 0x0a, 012) and must be nothing but the number;
 * anything else is refused with exit 2 before the device is touched.
 *
 * Every invocation that opens the device claims interface 0 once and tears down
 * once. --dry-run prints the writes without opening anything. Each write prints
 * its entity, selector, channel and bytes, then ok or the libusb error; the
 * first failure stops the remaining writes (so a half-applied profile stops
 * where it is, rather than carrying on into a louder state), the teardown still
 * runs, and the exit status is 1. SIGINT, SIGTERM, SIGHUP and SIGQUIT stop the
 * writes and finish the teardown: exit 128 + signal if writes were left unsent,
 * 0 if the signal came after the last write had already succeeded. SIGKILL
 * cannot be caught: that one costs the sound card until a rebind or a replug.
 */
#include <libusb.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "id14_profile.h"

#define VID 0x2708
#define PID 0x0002
#define TIMEOUT 1000
#define MAX_GAP_MS 60000

/* Which interface carries the requests. These are interface-targeted class
 * requests, so the recipient has to be the AudioControl interface, 0: the spare
 * DFU interface returns timeouts and empty reads, and sending them without
 * claiming returns LIBUSB_ERROR_IO because the kernel owns the interface. That
 * leaves claiming interface 0, which detaches snd-usb-audio -- and the card's
 * PCM devices do not come back on a plain reattach. libusb_reset_device before
 * releasing is what rebuilds them, which is how MixiD gets away with the same
 * trick. IDCTL_IFACE overrides the interface (default 0); IDCTL_CLAIM=0 skips
 * claiming. */
static int iface = 0;
static int do_claim = 1;

static libusb_device_handle *dev;

/* Set by the signal handler, read between writes. Nothing else happens in the
 * handler: libusb is not async-signal-safe, so the teardown runs in the normal
 * flow once the transfer in progress (bounded by TIMEOUT) has returned. */
static volatile sig_atomic_t stop_sig;

static void on_signal(int sig)
{
    if (!stop_sig)
        stop_sig = sig;
}

static void install_handlers(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    static const int sigs[] = {SIGINT, SIGTERM, SIGHUP, SIGQUIT};
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++)
        sigaction(sigs[i], &sa, NULL);
    /* A closed stdout (the reader of a pipe gone) must not kill us mid-claim. */
    signal(SIGPIPE, SIG_IGN);
}

/* The sysfs name of the device, e.g. 3-1.2.1, so the journal says which one. */
static void print_device(void)
{
    libusb_device *d = libusb_get_device(dev);
    uint8_t ports[8];
    int np = libusb_get_port_numbers(d, ports, sizeof ports);
    printf("opened %04x:%04x at %d-", VID, PID, libusb_get_bus_number(d));
    for (int i = 0; i < np; i++)
        printf(i ? ".%d" : "%d", ports[i]);
    printf("\n");
}

/* An environment override, parsed strictly. Returns 0 (absent or ok) or -1. */
static int env_int(const char *name, long lo, long hi, int *out)
{
    const char *s = getenv(name);
    long v;
    if (!s)
        return 0;
    if (id14_parse_long(s, lo, hi, &v) < 0) {
        fprintf(stderr, "%s=%s: want a number %ld-%ld\n", name, s, lo, hi);
        return -1;
    }
    *out = (int)v;
    return 0;
}

/* After a failed claim, or a refusal after the open: there is no claimed
 * interface to release, but the card must not be left without its driver.
 * With auto-detach on, libusb can detach snd-usb-audio and then fail the claim
 * (its fallback on kernels without USBDEVFS_DISCONNECT_CLAIM does exactly
 * that). So if interface 0 has no kernel driver now, hand it back; then close
 * and exit, as the normal teardown does. libusb reports "no kernel driver"
 * also when another program (MixiD) holds the interface through usbfs; the
 * attach then fails with LIBUSB_ERROR_BUSY and takes nothing from it. */
static void abandon_device(int claim_attempted)
{
    if (claim_attempted && iface == 0) {
        int active = libusb_kernel_driver_active(dev, 0);
        if (active == 1) {
            printf("after the failed claim: snd-usb-audio is still bound to interface 0\n");
        } else if (active == 0) {
            int r = libusb_attach_kernel_driver(dev, 0);
            printf("after the failed claim: interface 0 had no kernel driver; reattach %s\n",
                   libusb_error_name(r));
        } else {
            printf("after the failed claim: cannot check interface 0's driver: %s\n",
                   libusb_error_name(active));
        }
    }
    libusb_close(dev);
    libusb_exit(NULL);
}

/* The claim. The sequence (auto-detach, claim) is MixiD's driver_init on
 * rig/id14-mki-interface0, and is kept exactly as it was. Every failure after
 * libusb_init cleans up before returning. */
static int open_device(void)
{
    if (libusb_init(NULL) != LIBUSB_SUCCESS)
        return fprintf(stderr, "libusb_init failed\n"), -1;
    dev = libusb_open_device_with_vid_pid(NULL, VID, PID);
    if (!dev) {
        fprintf(stderr, "no %04x:%04x, or no permission (udev rule?)\n", VID, PID);
        libusb_exit(NULL);
        return -1;
    }
    print_device();
    /* Taking interface 0 detaches snd-usb-audio, so the card leaves ALSA until we
     * release it. Reattached in close_device, which is why this never daemonises. */
    if (env_int("IDCTL_IFACE", 0, 255, &iface) < 0 || env_int("IDCTL_CLAIM", 0, 1, &do_claim) < 0) {
        abandon_device(0);
        return -1;
    }
    if (!do_claim)
        return 0;
    if (iface == 0 && libusb_set_auto_detach_kernel_driver(dev, 1) < 0)
        fprintf(stderr, "warning: auto-detach unavailable\n");
    int err = libusb_claim_interface(dev, iface);
    if (err < 0) {
        fprintf(stderr, "claim failed: %s\n", libusb_error_name(err));
        abandon_device(1);
        return -1;
    }
    printf("claimed interface %d\n", iface);
    return 0;
}

/* The teardown. The sequence (reset, release, reattach, close, exit) is MixiD's
 * driver_shutdown, and is kept exactly as it was; only the return codes are now
 * printed. With auto-detach on, the release itself reattaches snd-usb-audio, so
 * the explicit attach after it normally reports LIBUSB_ERROR_BUSY (driver
 * already bound) -- harmless, and MixiD ignores it the same way. */
static void close_device(void)
{
    if (do_claim) {
        int r_reset = 0, r_attach = 0;
        if (iface == 0)
            r_reset = libusb_reset_device(dev);   /* without this the card loses its PCMs */
        int r_release = libusb_release_interface(dev, iface);
        if (iface == 0)
            r_attach = libusb_attach_kernel_driver(dev, iface);
        printf("teardown: reset %s, release %s, attach %s\n",
               iface == 0 ? libusb_error_name(r_reset) : "skipped",
               libusb_error_name(r_release),
               iface == 0 ? libusb_error_name(r_attach) : "skipped");
    }
    libusb_close(dev);
    libusb_exit(NULL);
}

static int xfer(int out, int entity, int sel, int ch, int len, unsigned char *buf)
{
    return libusb_control_transfer(dev,
        out ? 0x21 : 0xA1, out ? 0x01 : 0x81,
        (sel << 8) | ch, (entity << 8) | iface, buf, len, TIMEOUT);
}

static void gap(int ms)
{
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
    nanosleep(&ts, NULL);   /* a signal cuts it short, which is what we want */
}

static void report_signal(int all_sent)
{
    if (all_sent)
        printf("signal %d (%s) arrived after the last write; every write was ok\n",
               (int)stop_sig, strsignal(stop_sig));
    else
        printf("interrupted by signal %d (%s)\n", (int)stop_sig, strsignal(stop_sig));
}

/* Claim once, send every write in order, tear down once. */
static int run_writes(const struct id14_write *w, int n, int dry, int gap_ms)
{
    char line[160];
    int failed = 0, done = 0;

    if (dry) {
        printf("dry run: %d write(s), device not opened\n", n);
        /* For test_idctl_cli.sh, which signals only after seeing this. */
        fprintf(stderr, "idctl: signal handlers installed\n");
    } else if (open_device() < 0) {
        return 1;
    }

    for (int i = 0; i < n && !stop_sig; i++) {
        if (i && gap_ms > 0) {
            gap(gap_ms);
            if (stop_sig)
                break;
        }
        id14_format_write(&w[i], line, sizeof line);
        done++;
        if (dry) {
            printf("%2d/%d  %s\n", i + 1, n, line);
            continue;
        }
        unsigned char buf[ID14_MAX_LEN];
        memcpy(buf, w[i].data, sizeof buf);
        int r = xfer(1, w[i].entity, w[i].selector, w[i].channel, w[i].len, buf);
        if (r == w[i].len) {
            printf("%2d/%d  %s: ok\n", i + 1, n, line);
        } else {
            if (r < 0)
                printf("%2d/%d  %s: %s\n", i + 1, n, line, libusb_error_name(r));
            else
                printf("%2d/%d  %s: short write, %d of %d byte(s)\n", i + 1, n, line, r, w[i].len);
            failed = 1;
            break;
        }
    }
    if (done < n)
        printf("skipped the remaining %d write(s): %s\n", n - done,
               failed ? "the write above failed" : "signal");

    if (!dry)
        close_device();
    if (stop_sig)
        report_signal(done == n && !failed);
    if (failed)
        printf("FAILED\n");
    return id14_exit_status(n, done, failed, stop_sig);
}

static int usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s [--dry-run] [--gap-ms N] apply [daw|mainmix]\n"
        "       %s [--dry-run] route <output 0-3> <position 0-2>   (0 Main Mix, 1 Cue Mix, 2 DAW)\n"
        "       %s [--dry-run] set <entity> <sel> <ch> <len 1-%d> <value>\n"
        "       %s get <entity> <sel> <ch> <len 1-64>\n"
        "       %s scan\n", argv0, argv0, argv0, ID14_MAX_LEN, argv0, argv0);
    return 2;
}

/* A command-line number, or exit 2 naming it. */
#define ARG(var, str, lo, hi, what) do {                                        \
        long v_;                                                                \
        if (id14_parse_long((str), (lo), (hi), &v_) < 0) {                      \
            fprintf(stderr, "%s: '%s' is not a number %ld-%ld\n", (what), (str),\
                    (long)(lo), (long)(hi));                                    \
            return 2;                                                           \
        }                                                                       \
        (var) = (int)v_;                                                        \
    } while (0)

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IOLBF, 0);   /* line by line into the journal, not at exit */

    /* Options may come anywhere; strip them, keep the rest in order. */
    int dry = 0, gap_ms = 0, gap_given = 0, ac = 0;
    char *av[16];
    for (int i = 0; i < argc; i++) {
        if (i && !strcmp(argv[i], "--dry-run")) {
            dry = 1;
        } else if (i && !strcmp(argv[i], "--gap-ms")) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--gap-ms needs a number of milliseconds, 0-%d\n", MAX_GAP_MS);
                return 2;
            }
            ARG(gap_ms, argv[i + 1], 0, MAX_GAP_MS, "--gap-ms");
            gap_given = 1;
            i++;
        } else if (ac < (int)(sizeof av / sizeof *av)) {
            av[ac++] = argv[i];
        } else {
            return usage(argv[0]);
        }
    }
    if (ac < 2)
        return usage(argv[0]);
    const char *cmd = av[1];
    if (gap_given && strcmp(cmd, "apply")) {
        fprintf(stderr, "--gap-ms is for apply only\n");
        return 2;
    }

    install_handlers();

    static struct id14_write w[ID14_MAX_WRITES];

    if (!strcmp(cmd, "apply") && ac <= 3) {
        const char *name = ac == 3 ? av[2] : "daw";
        int n = id14_profile(name, w, ID14_MAX_WRITES);
        if (n < 0) {
            fprintf(stderr, "unknown profile '%s': daw or mainmix\n", name);
            return 2;
        }
        printf("profile %s\n", name);
        return run_writes(w, n, dry, gap_ms);
    }
    if (!strcmp(cmd, "route") && ac == 4) {
        int out, pos;
        ARG(out, av[2], 0, ID14_ROUTE_OUTPUTS - 1, "output (0 Main L, 1 Main R, 2 HP L, 3 HP R)");
        ARG(pos, av[3], 0, 2, "position (0 Main Mix, 1 Cue Mix, 2 DAW)");
        id14_route_write(out, pos, &w[0]);
        return run_writes(w, 1, dry, 0);
    }
    if (!strcmp(cmd, "set") && ac == 7) {
        int e, s, c, len;
        ARG(e, av[2], 0, 255, "entity");
        ARG(s, av[3], 0, 255, "selector");
        ARG(c, av[4], 0, 255, "channel");
        ARG(len, av[5], 1, ID14_MAX_LEN, "len");
        memset(&w[0], 0, sizeof w[0]);
        if (id14_parse_value(av[6], len, w[0].data) < 0) {
            fprintf(stderr, "value: '%s' is not a non-negative number that fits in %d byte(s)\n", av[6], len);
            return 2;
        }
        w[0].entity = (uint8_t)e;
        w[0].selector = (uint8_t)s;
        w[0].channel = (uint8_t)c;
        w[0].len = (uint8_t)len;
        snprintf(w[0].what, sizeof w[0].what, "set");
        return run_writes(w, 1, dry, 0);
    }

    /* Reads need the device; there is nothing to dry-run. */
    if (dry) {
        fprintf(stderr, "--dry-run is for apply, route and set\n");
        return 2;
    }
    int rc = 0;
    unsigned char buf[64] = {0};

    if (!strcmp(cmd, "get") && ac == 6) {
        int e, s, c, len;
        ARG(e, av[2], 0, 255, "entity");
        ARG(s, av[3], 0, 255, "selector");
        ARG(c, av[4], 0, 255, "channel");
        ARG(len, av[5], 1, (int)sizeof buf, "len");
        if (open_device() < 0)
            return 1;
        int err = xfer(0, e, s, c, len, buf);
        if (err < 0) {
            printf("get: %s\n", libusb_error_name(err));
            rc = 1;
        } else {
            printf("get: %d byte(s):", err);
            for (int i = 0; i < err; i++)
                printf(" %02x", buf[i]);
            printf("\n");
        }
        close_device();
        if (stop_sig)
            report_signal(1);   /* the one read was already done */
        return rc;
    }
    if (!strcmp(cmd, "scan") && ac == 2) {
        if (open_device() < 0)
            return 1;
        /* Which (entity, selector) pairs answer a read at all. Read-only. */
        static const int ents[] = {0x0a, 0x0b, 0x0c, 0x28, 0x32, 0x33, 0x36, 0x37, 0x3c};
        for (unsigned e = 0; e < sizeof ents / sizeof *ents && !stop_sig; e++)
            for (int sel = 0; sel <= 0x20 && !stop_sig; sel++)
                for (int ch = 0; ch <= 2 && !stop_sig; ch++) {
                    int err = xfer(0, ents[e], sel, ch, 8, buf);
                    if (err > 0) {
                        printf("entity 0x%02x sel 0x%02x ch%d -> %d byte(s):", ents[e], sel, ch, err);
                        for (int i = 0; i < err; i++)
                            printf(" %02x", buf[i]);
                        printf("\n");
                    }
                }
        int stopped = stop_sig;
        printf(stopped ? "scan stopped\n" : "scan done\n");
        close_device();
        if (stopped) {
            report_signal(0);
            return 128 + stopped;
        }
        return 0;
    }
    return usage(argv[0]);
}
