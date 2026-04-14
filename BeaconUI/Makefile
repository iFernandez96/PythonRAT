# BeaconUI C Implant — Makefile
#
# Install dependencies first:
#   sudo apt install libcurl4-openssl-dev libssl-dev
#
# Usage:
#   make              build release binary
#   make debug        build with AddressSanitizer + debug symbols
#   make clean        remove built binary
#   make run          build and run (useful for quick test; expects C2 on localhost)

TARGET  := implant_beacon
SRC     := implant_beacon.c

CC      := gcc

# Prefer pkg-config; fall back to bare -lcurl -lssl -lcrypto when headers
# are installed but pkg-config metadata is missing (Ubuntu libcurl4-openssl-dev).
CURL_CFLAGS  := $(shell pkg-config --cflags libcurl  2>/dev/null)
CURL_LIBS    := $(shell pkg-config --libs   libcurl  2>/dev/null || echo "-lcurl")
SSL_CFLAGS   := $(shell pkg-config --cflags openssl  2>/dev/null)
SSL_LIBS     := $(shell pkg-config --libs   openssl  2>/dev/null || echo "-lssl -lcrypto")

CFLAGS  := -Wall -Wextra -O2 $(CURL_CFLAGS) $(SSL_CFLAGS)
LDFLAGS := $(CURL_LIBS) $(SSL_LIBS) -lpthread -lm

# Test port matches _test_port in test_beacon.py (9446)
TEST_PORT   := 9446
TEST_CFLAGS := -DC2_URL_0='"https://localhost:$(TEST_PORT)"' \
               -DDEFAULT_INTERVAL=1 -DJITTER_FACTOR=0.0

.PHONY: all debug test_build clean run

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)
	@echo "[+] Built: $(TARGET)  ($(shell du -sh $(TARGET) | cut -f1) on disk)"

test_build: $(SRC)
	$(CC) $(CFLAGS) $(TEST_CFLAGS) -o $(TARGET)_test $< $(LDFLAGS)
	@echo "[+] Built: $(TARGET)_test  (test binary — points to localhost:$(TEST_PORT), interval=1s)"

debug: $(SRC)
	$(CC) -Wall -Wextra -g -fsanitize=address,undefined \
	      $(shell pkg-config --cflags $(PKGS)) \
	      -o $(TARGET)_debug $< \
	      $(shell pkg-config --libs $(PKGS)) -lpthread -lm
	@echo "[+] Built: $(TARGET)_debug (ASAN+UBSAN)"

clean:
	rm -f $(TARGET) $(TARGET)_debug $(TARGET)_test

run: $(TARGET)
	./$(TARGET)
