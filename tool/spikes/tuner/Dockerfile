# The spike's image: CI's rig image (ci/Dockerfile, tagged sushi-rig-ci) and
# what the probe needs beside it. CI's image has libjack but no JACK server,
# and Sushi on JACK needs one; gcc builds alsa_seq_standin.so (README.md).
#
#   docker build -t sushi-rig-ci-jackd tool/spikes/tuner
FROM sushi-rig-ci
RUN apt-get update && apt-get install -y --no-install-recommends jackd2 gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
