# Maintainer: Albert Flindt <Albert.Flindt@proton.me>
pkgname=keymappforlinux
pkgver=0.1.0
pkgrel=1
pkgdesc="GNOME-style companion app for ZSA keyboards with Smart Layers (app-aware layer switching)"
arch=('any')
url="https://github.com/albertflindt/keyMappForLinux"
license=('GPL-3.0-or-later')
depends=('python' 'python-gobject' 'gtk4' 'libadwaita' 'glib2')
makedepends=('meson')
optdepends=('hyprland: Smart Layers focus tracking')
install=keymappforlinux.install
source=()
sha256sums=()

# Built from the local checkout this PKGBUILD lives in.
_srcroot="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

build() {
  arch-meson "$_srcroot" build
  meson compile -C build
}

check() {
  cd "$_srcroot"
  PYTHONPATH=src python -m unittest discover -s tests -t .
}

package() {
  meson install -C build --destdir "$pkgdir"
}
