/* idctl -- minimal scriptable control of an Audient iD interface.
 *
 * MixiD needs a GUI and a human; this needs neither, so a routing change can sit
 * inside a measurement loop. It speaks the same requests: UAC2 SET_CUR/GET_CUR on
 * interface 0, wValue = (selector << 8) | channel, wIndex = (entity << 8) | iface.
 *
 *   idctl route <chan 0-5> <pos 0-4>   set an output's source
 *   idctl set <entity> <sel> <ch> <len> <value>
 *   idctl get <entity> <sel> <ch> <len>
 *   idctl scan                         GET_CUR across entities and selectors
 */
#include <libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VID 0x2708
#define PID 0x0002
#define TIMEOUT 1000

/* [channel][position]; positions are Main Mix, Alt Spkr, Cue A, Cue B, DAW Mix.
 * Taken from MixiD's driver.h, which is where this protocol is documented at all. */
static const unsigned char route[6][5] = {
    {0x1b, 0x1d, 0x19, 0x1a, 0x00},  /* 1 Main L  */
    {0x1c, 0x1e, 0x19, 0x1a, 0x01},  /* 2 Main R  */
    {0x1b, 0x1d, 0x19, 0x1a, 0x02},  /* 3         */
    {0x1c, 0x1e, 0x19, 0x1a, 0x03},  /* 4         */
    {0x1b, 0x1d, 0x19, 0x1a, 0x04},  /* 5 HP L    */
    {0x1c, 0x1e, 0x19, 0x1a, 0x05},  /* 6 HP R    */
};

/* Which interface carries the requests. Interface 0 is the AudioControl one, so
 * claiming it detaches snd-usb-audio -- and on this unit the ALSA card does not
 * reliably come back when it is reattached, which costs you the sound card until
 * a replug. Interface 3 is the spare DFU/vendor one: claiming that leaves the
 * audio interfaces alone, so writes can sit inside a measurement loop.
 * Set IDCTL_IFACE to choose; default 3. */
static int iface = 0;
/* These are interface-targeted class requests, so the recipient has to be the
 * AudioControl interface: the spare DFU interface returns timeouts and empty
 * reads, and sending them without claiming returns LIBUSB_ERROR_IO because the
 * kernel owns the interface. That leaves claiming interface 0, which detaches
 * snd-usb-audio -- and the card's PCM devices do not come back on a plain
 * reattach. libusb_reset_device before releasing is what rebuilds them, which is
 * how MixiD gets away with the same trick. Set IDCTL_CLAIM=0 to skip claiming. */
static int do_claim = 1;

static libusb_device_handle *dev;

static int open_device(void)
{
    if (libusb_init(NULL) != LIBUSB_SUCCESS)
        return fprintf(stderr, "libusb_init failed\n"), -1;
    dev = libusb_open_device_with_vid_pid(NULL, VID, PID);
    if (!dev)
        return fprintf(stderr, "no %04x:%04x, or no permission (udev rule?)\n", VID, PID), -1;
    /* Taking interface 0 detaches snd-usb-audio, so the card leaves ALSA until we
     * release it. Reattached in close_device, which is why this never daemonises. */
    const char *env = getenv("IDCTL_IFACE");
    if (env)
        iface = atoi(env);
    if (getenv("IDCTL_CLAIM"))
        do_claim = atoi(getenv("IDCTL_CLAIM"));
    if (!do_claim)
        return 0;
    if (iface == 0 && libusb_set_auto_detach_kernel_driver(dev, 1) < 0)
        fprintf(stderr, "warning: auto-detach unavailable\n");
    int err = libusb_claim_interface(dev, iface);
    if (err < 0)
        return fprintf(stderr, "claim failed: %s\n", libusb_error_name(err)), -1;
    return 0;
}

static void close_device(void)
{
    if (do_claim) {
        if (iface == 0)
            libusb_reset_device(dev);   /* without this the card loses its PCMs */
        libusb_release_interface(dev, iface);
        if (iface == 0)
            libusb_attach_kernel_driver(dev, iface);
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

int main(int argc, char **argv)
{
    if (argc < 2)
        return fprintf(stderr, "usage: %s route|set|get|scan ...\n", argv[0]), 2;
    if (open_device() < 0)
        return 1;

    int rc = 0;
    unsigned char buf[64] = {0};

    if (!strcmp(argv[1], "route") && argc == 4) {
        int c = atoi(argv[2]), p = atoi(argv[3]);
        if (c < 0 || c > 5 || p < 0 || p > 4) {
            fprintf(stderr, "channel 0-5, position 0-4\n");
            rc = 2;
        } else {
            buf[0] = route[c][p];
            int err = xfer(1, 0x33, 0x06, c, 1, buf);
            printf("route ch%d pos%d (0x%02x): %s\n", c + 1, p, buf[0],
                   err < 0 ? libusb_error_name(err) : "ok");
            rc = err < 0;
        }
    } else if (!strcmp(argv[1], "set") && argc == 7) {
        int len = atoi(argv[5]);
        long v = strtol(argv[6], NULL, 0);
        for (int i = 0; i < len && i < (int)sizeof buf; i++)
            buf[i] = (v >> (8 * i)) & 0xff;
        int err = xfer(1, strtol(argv[2], NULL, 0), strtol(argv[3], NULL, 0),
                       atoi(argv[4]), len, buf);
        printf("set: %s\n", err < 0 ? libusb_error_name(err) : "ok");
        rc = err < 0;
    } else if (!strcmp(argv[1], "get") && argc == 6) {
        int len = atoi(argv[5]);
        int err = xfer(0, strtol(argv[2], NULL, 0), strtol(argv[3], NULL, 0),
                       atoi(argv[4]), len, buf);
        if (err < 0) {
            printf("get: %s\n", libusb_error_name(err));
            rc = 1;
        } else {
            printf("get: %d byte(s):", err);
            for (int i = 0; i < err; i++)
                printf(" %02x", buf[i]);
            printf("\n");
        }
    } else if (!strcmp(argv[1], "scan")) {
        /* Which (entity, selector) pairs answer a read at all. Read-only. */
        static const int ents[] = {0x0a, 0x0b, 0x0c, 0x28, 0x32, 0x33, 0x36, 0x37, 0x3c};
        for (unsigned e = 0; e < sizeof ents / sizeof *ents; e++)
            for (int sel = 0; sel <= 0x20; sel++)
                for (int ch = 0; ch <= 2; ch++) {
                    int err = xfer(0, ents[e], sel, ch, 8, buf);
                    if (err > 0) {
                        printf("entity 0x%02x sel 0x%02x ch%d -> %d byte(s):", ents[e], sel, ch, err);
                        for (int i = 0; i < err; i++)
                            printf(" %02x", buf[i]);
                        printf("\n");
                    }
                }
        printf("scan done\n");
    } else {
        fprintf(stderr, "bad arguments\n");
        rc = 2;
    }

    close_device();
    return rc;
}
