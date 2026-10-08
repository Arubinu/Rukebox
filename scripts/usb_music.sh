#!/bin/sh
# Mounts a USB key where the radio reads its music from, and takes it away.
#
# Everything that needs root happens here: the interface only ever passes a
# device path, and this script is what checks it, mounts read-only and refuses
# anything that is not a partition of a removable disk.
set -eu

MOUNT_POINT="${RUKEBOX_USB_MOUNT:-/media/rukebox-usb}"

usage() {
    echo "usage: rukebox-usb-music mount /dev/sda1 | umount" >&2
    exit 2
}

mounted() {
    grep -qs " $MOUNT_POINT " /proc/mounts
}

case "${1:-}" in
    mount)
        device="${2:-}"
        # One partition of one disk: no path of its own, no metacharacter, no
        # symlink - and the character device has to be there.
        case "$device" in
            /dev/sd[a-z][0-9]|/dev/sd[a-z][0-9][0-9]|/dev/mmcblk[0-9]p[0-9]) ;;
            *) usage ;;
        esac
        if [ ! -b "$device" ]; then
            echo "not a block device: $device" >&2
            exit 1
        fi
        if mounted; then
            echo "already mounted: $MOUNT_POINT" >&2
            exit 0
        fi
        mkdir -p "$MOUNT_POINT"
        # Read-only, and nothing on the key can be executed or used as a
        # device: pulling it out at any moment is then harmless.
        exec mount -o ro,nosuid,nodev,noexec "$device" "$MOUNT_POINT"
        ;;
    umount)
        if ! mounted; then
            echo "nothing mounted at $MOUNT_POINT" >&2
            exit 0
        fi
        # A reader that is a moment behind (ffprobe on a track) must not keep
        # the key busy: the lazy unmount is what makes hot unplugging work.
        umount "$MOUNT_POINT" 2>/dev/null || umount -l "$MOUNT_POINT"
        ;;
    *)
        usage
        ;;
esac
