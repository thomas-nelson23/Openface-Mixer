# Openface Mixer — common developer tasks. Run `make help`.
CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -Wno-unused-parameter
PW_FLAGS = $(shell pkg-config --cflags --libs libpipewire-0.3 libusb-1.0)
ENGINE   = engine/openface-mixer-engine

.PHONY: all engine run run-engine test lint install uninstall clean help

all: engine ## Build everything (just the engine; the GUI is plain Python)

engine: $(ENGINE) ## Build the C mixer engine

$(ENGINE): engine/openface-mixer-engine.c engine/digiface_usb.c engine/digiface_usb.h engine/shm_layout.h
	$(CC) $(CFLAGS) -pthread -o $@ engine/openface-mixer-engine.c engine/digiface_usb.c $(PW_FLAGS) -lm

run: engine ## Run the GUI from the source tree (starts the local engine build if none is running)
	python3 -m openface_mixer

run-engine: engine ## Run the engine in the foreground (stop the systemd service first)
	./$(ENGINE)

test: ## Run the unit tests (no audio hardware or display needed)
	python3 -m unittest discover -s tests -t . -v
	$(CC) $(CFLAGS) -o tests/test_digiface_usb tests/test_digiface_usb.c engine/digiface_usb.c \
		$(shell pkg-config --cflags --libs libusb-1.0) -lm
	./tests/test_digiface_usb

lint: ## Byte-compile everything to catch syntax errors
	python3 -m compileall -q openface_mixer tests

install: ## Install for the current user (~/.local) and enable the engine service
	./scripts/install.sh

uninstall: ## Remove the user install (keeps saved mixes)
	./scripts/uninstall.sh

clean: ## Remove build output
	rm -f $(ENGINE) tests/test_digiface_usb
	find . -name __pycache__ -prune -exec rm -rf {} +

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-11s %s\n", $$1, $$2}'
