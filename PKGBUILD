# Maintainer: KsmBL <katzen.sind.lecker69@gmail.com>
pkgname=smbmanager
pkgver=1.0.0
pkgrel=1
pkgdesc="GTK3 application to manage SMB shares and the Samba service"
arch=('any')
url="https://github.com/KsmBl/smbmanager"
license=('GPL3')
depends=('python' 'python-gobject' 'gtk3' 'polkit')
optdepends=('samba: the file server itself, installable from within the app')
source=()

package() {
    cd "$startdir"
    local site
    site=$(python3 -c "import sysconfig;print(sysconfig.get_paths()['purelib'])")
    install -d "$pkgdir$site/smbmanager"
    install -m 644 smbmanager/*.py "$pkgdir$site/smbmanager/"
    install -Dm 755 helper/smbmanager-helper "$pkgdir/usr/lib/smbmanager/smbmanager-helper"
    install -Dm 755 bin/smbmanager "$pkgdir/usr/bin/smbmanager"
    install -Dm 644 data/de.synthelicz.SmbManager.desktop \
        "$pkgdir/usr/share/applications/de.synthelicz.SmbManager.desktop"
    install -Dm 644 data/de.synthelicz.SmbManager.policy \
        "$pkgdir/usr/share/polkit-1/actions/de.synthelicz.SmbManager.policy"
}
