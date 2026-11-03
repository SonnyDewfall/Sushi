/* LD_PRELOAD control for repro.sh: every operator new[] of exactly 1024 bytes
 * -- gx_detune_'s `fpb` and `expect`, fftFrameSize/2 = 256 floats each at its
 * default quality -- gets 64 bytes past its end that hold a known float, so the
 * reads one past the end find that instead of whatever the heap left there.
 * The float is GX_PAST_END (anything strtof reads: 0, nan, 1e30), 0 if unset.
 * Every other allocation is untouched. */
#include <stdlib.h>
void *_Znam(unsigned long n) {
    if (n != 1024)
        return malloc(n);
    float *p = calloc(1, n + 64);
    const char *value = getenv("GX_PAST_END");
    float past = value ? strtof(value, NULL) : 0.0f;
    for (int i = 0; i < 16; i++)
        p[n / sizeof(float) + i] = past;
    return p;
}
