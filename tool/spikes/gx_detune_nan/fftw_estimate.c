/* LD_PRELOAD control for repro.sh: every fftwf_plan_dft_1d is planned with
 * FFTW_ESTIMATE whatever the caller asked for, so the plan does not depend on
 * timing. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
typedef void *plan;
plan fftwf_plan_dft_1d(int n, void *in, void *out, int sign, unsigned flags) {
    static plan (*real)(int, void *, void *, int, unsigned);
    if (!real) {
        void *lib = dlopen("libfftw3f.so.3", RTLD_NOW | RTLD_GLOBAL);
        real = dlsym(lib, "fftwf_plan_dft_1d");
    }
    return real(n, in, out, sign, 1U << 6 /* FFTW_ESTIMATE */);
}
