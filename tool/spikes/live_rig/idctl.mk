# idctl and its tests. From this directory:
#   make -f idctl.mk          build idctl
#   make -f idctl.mk check    unit tests, the dry-run listings, the signal path, the connect script
# Needs libusb-1.0-0-dev for idctl itself; the unit test needs only a C compiler.

CC      ?= cc
CFLAGS  ?= -O2 -g
CFLAGS  += -std=gnu11 -Wall -Wextra -Werror
USB_CFLAGS := $(shell pkg-config --cflags libusb-1.0)
USB_LIBS   := $(shell pkg-config --libs libusb-1.0)

all: idctl

idctl: idctl.c id14_profile.c id14_profile.h
	$(CC) $(CFLAGS) $(USB_CFLAGS) -o $@ idctl.c id14_profile.c $(USB_LIBS)

test_id14_profile: test_id14_profile.c id14_profile.c id14_profile.h
	$(CC) $(CFLAGS) -o $@ test_id14_profile.c id14_profile.c

check: test_id14_profile idctl
	./test_id14_profile
	./test_idctl_cli.sh
	./test_id14_route_on_connect.sh

clean:
	rm -f idctl test_id14_profile

.PHONY: all check clean
