#!/bin/sh
# Installs the button watcher on a router, from a computer: install.sh root@192.168.8.1 [--serial]

HOST=$1
HERE=$(dirname "$0")
if [ -z "$HOST" ]; then
	echo "Usage: install.sh root@router-address [--serial]"
	exit 2
fi

ssh "$HOST" 'cat > /usr/bin/rukebox-button && chmod 755 /usr/bin/rukebox-button' <"$HERE/rukebox-button" || exit 1
ssh "$HOST" 'cat > /etc/init.d/rukebox-button && chmod 755 /etc/init.d/rukebox-button' <"$HERE/rukebox-button.init" || exit 1
# A settings file already there stays as it is.
ssh "$HOST" '[ -e /etc/rukebox-button.conf ] || { cat > /etc/rukebox-button.conf; chmod 600 /etc/rukebox-button.conf; }' \
	<"$HERE/rukebox-button.conf" || exit 1

if [ "$2" = "--serial" ]; then
	# Each board speaks through one of these chips; a router's feed may lack some.
	ssh "$HOST" 'opkg update
		for module in kmod-usb-acm kmod-usb-serial-ch341 kmod-usb-serial-cp210x kmod-usb-serial-ftdi; do
			opkg install "$module" >/dev/null 2>&1 && echo "$module: ok" || echo "$module: NOT available"
		done'
fi

ssh "$HOST" '/etc/init.d/rukebox-button enable; /etc/init.d/rukebox-button restart'
echo "Settings are in /etc/rukebox-button.conf. Press the button, then approve it on the radio's page."
