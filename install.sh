#!/usr/bin/env bash
# Install SMB Manager system wide.  Run with sudo, or use ./install.sh --uninstall
set -euo pipefail

PREFIX=${PREFIX:-/usr}
DESTDIR=${DESTDIR:-}
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

py_site() {
    python3 -c "import sysconfig,sys; print(sysconfig.get_paths()['purelib'])"
}

uninstall() {
    rm -rf "${DESTDIR}$(py_site)/smbmanager"
    rm -rf "${DESTDIR}${PREFIX}/lib/smbmanager"
    rm -f  "${DESTDIR}${PREFIX}/bin/smbmanager"
    rm -f  "${DESTDIR}${PREFIX}/share/applications/de.synthelicz.SmbManager.desktop"
    rm -f  "${DESTDIR}${PREFIX}/share/polkit-1/actions/de.synthelicz.SmbManager.policy"
    echo "SMB Manager removed.  /etc/samba/smbmanager.conf was kept."
}

if [[ ${1:-} == "--uninstall" ]]; then
    uninstall
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "Please run this as root:  sudo ./install.sh" >&2
    exit 1
fi

site=$(py_site)
install -d "${DESTDIR}${site}/smbmanager"
install -m 644 "$here"/smbmanager/*.py "${DESTDIR}${site}/smbmanager/"

install -Dm 755 "$here/helper/smbmanager-helper" \
    "${DESTDIR}${PREFIX}/lib/smbmanager/smbmanager-helper"
install -Dm 755 "$here/bin/smbmanager" "${DESTDIR}${PREFIX}/bin/smbmanager"
install -Dm 644 "$here/data/de.synthelicz.SmbManager.desktop" \
    "${DESTDIR}${PREFIX}/share/applications/de.synthelicz.SmbManager.desktop"
install -Dm 644 "$here/data/de.synthelicz.SmbManager.policy" \
    "${DESTDIR}${PREFIX}/share/polkit-1/actions/de.synthelicz.SmbManager.policy"

echo "SMB Manager installed.  Start it from your application menu or run: smbmanager"
