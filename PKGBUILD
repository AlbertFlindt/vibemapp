# Maintainer: Albert Flindt <Albert.Flindt@proton.me>
pkgname=vibemapp
pkgver=0.3.1
pkgrel=1
pkgdesc="GNOME-style companion app for ZSA keyboards with Smart Layers (app-aware layer switching)"
arch=('any')
url="https://github.com/albertflindt/vibemapp"
license=('GPL-3.0-or-later')
depends=('python' 'python-gobject' 'gtk4' 'libadwaita' 'glib2')
makedepends=('meson')
optdepends=('hyprland: Smart Layers focus tracking')
conflicts=('keymappforlinux')
replaces=('keymappforlinux')
install=vibemapp.install
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
  # PyGObject 3.58 calls asyncio APIs deprecated in Python 3.14; not our code.
  PYTHONPATH=src python -W "ignore::DeprecationWarning:gi.events" -m unittest discover -s tests -t .
}

package() {
  meson install -C build --destdir "$pkgdir"
}
