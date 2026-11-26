/* A stand-in for ALSA's sequencer, preloaded into Sushi alone (#58's spike).
 *
 * Sushi's standalone factory always opens the ALSA sequencer for MIDI
 * (AlsaMidiFrontend), and fails to start on JACK without /dev/snd/seq,
 * which a container on a kernel with no ALSA does not have. This answers
 * the snd_seq_* calls Sushi 1.3.0 makes (nm -D sushi.bin) with success and
 * a sequencer that never has an event: one poll descriptor, a pipe's read
 * end, which never becomes readable. snd_midi_event_* need no device and
 * are left to libasound. MIDI plays no part in what the probe measures.
 *
 *     gcc -shared -fPIC -O2 -o alsa_seq_standin.so alsa_seq_standin.c
 */
#include <errno.h>
#include <poll.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>

static int handle_storage;
static int fds[2] = {-1, -1};
static unsigned int real_time[2];
static int next_port;

int snd_seq_open(void **seq, const char *name, int streams, int mode)
{
    (void)name; (void)streams; (void)mode;
    if (fds[0] < 0 && pipe(fds) != 0)
        return -errno;
    *seq = &handle_storage;
    return 0;
}
int snd_seq_close(void *seq) { (void)seq; return 0; }
int snd_seq_set_client_name(void *seq, const char *name) { (void)seq; (void)name; return 0; }
int snd_seq_alloc_queue(void *seq) { (void)seq; return 0; }
int snd_seq_free_queue(void *seq, int q) { (void)seq; (void)q; return 0; }
int snd_seq_control_queue(void *seq, int q, int type, int value, void *ev)
{ (void)seq; (void)q; (void)type; (void)value; (void)ev; return 0; }
int snd_seq_create_simple_port(void *seq, const char *name, unsigned int caps, unsigned int type)
{ (void)seq; (void)name; (void)caps; (void)type; return next_port++; }
size_t snd_seq_port_info_sizeof(void) { return 256; }
int snd_seq_get_port_info(void *seq, int port, void *info)
{ (void)seq; (void)port; memset(info, 0, 256); return 0; }
int snd_seq_set_port_info(void *seq, int port, void *info) { (void)seq; (void)port; (void)info; return 0; }
void snd_seq_port_info_set_timestamp_queue(void *info, int q) { (void)info; (void)q; }
void snd_seq_port_info_set_timestamping(void *info, int enable) { (void)info; (void)enable; }
void snd_seq_port_info_set_timestamp_real(void *info, int enable) { (void)info; (void)enable; }
size_t snd_seq_queue_status_sizeof(void) { return 256; }
int snd_seq_get_queue_status(void *seq, int q, void *status)
{ (void)seq; (void)q; memset(status, 0, 256); return 0; }
const void *snd_seq_queue_status_get_real_time(const void *status) { (void)status; return real_time; }
int snd_seq_nonblock(void *seq, int nonblock) { (void)seq; (void)nonblock; return 0; }
int snd_seq_poll_descriptors_count(void *seq, short events) { (void)seq; (void)events; return 1; }
int snd_seq_poll_descriptors(void *seq, struct pollfd *pfds, unsigned int space, short events)
{
    (void)seq;
    if (space < 1)
        return 0;
    pfds[0].fd = fds[0];
    pfds[0].events = events;
    pfds[0].revents = 0;
    return 1;
}
int snd_seq_event_input(void *seq, void **ev) { (void)seq; *ev = NULL; return -EAGAIN; }
int snd_seq_event_output(void *seq, void *ev) { (void)seq; (void)ev; return 0; }
int snd_seq_drain_output(void *seq) { (void)seq; return 0; }
int snd_seq_free_event(void *ev) { (void)ev; return 0; }
