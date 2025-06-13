
# Define system paths
PREFIX ?= /usr
BIN_DIR = $(PREFIX)/bin
APP_DIR = $(PREFIX)/share/applications

# Define source files
SCRIPT = xlap.py
DESKTOP_FILE = xlap-tiler.desktop

.PHONY: all install uninstall clean

all:
	@echo "Usage: sudo make [install|uninstall]"

# Installs xlap-tiler to the system
install:
	@echo "Installing Xlap-Tiler..."
	# Create directories if they don't exist
	install -d -m 755 "$(DESTDIR)$(BIN_DIR)"
	install -d -m 755 "$(DESTDIR)$(APP_DIR)"
	
	# Install the main script as 'xlap-tiler'
	install -m 755 $(SCRIPT) "$(DESTDIR)$(BIN_DIR)/xlap-tiler"
	
	# Install the desktop file
	install -m 644 $(DESKTOP_FILE) "$(DESTDIR)$(APP_DIR)/"
	
	@echo "\nInstallation complete."
	@echo "You can now run Xlap-Tiler from your application menu."

# Uninstalls xlap-tiler from the system
uninstall:
	@echo "Uninstalling Xlap-Tiler..."
	# Remove the installed files
	rm -f "$(DESTDIR)$(BIN_DIR)/xlap-tiler"
	rm -f "$(DESTDIR)$(APP_DIR)/$(DESKTOP_FILE)"

	@echo "\nUninstallation complete."

clean:
	@echo "Cleaning up..."

