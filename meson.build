project('vibemapp',
  version: '1.9.0',
  meson_version: '>= 1.0.0',
  license: 'GPL-3.0-or-later',
)

app_id = 'io.github.albertflindt.Vibemapp'

python = import('python').find_installation('python3')
gnome = import('gnome')
i18n = import('i18n')

prefix = get_option('prefix')
bindir = get_option('bindir')
datadir = get_option('datadir')
pkgdatadir = datadir / meson.project_name()

# Python package -> /usr/share/vibemapp/vibemapp
install_subdir('src/vibemapp',
  install_dir: pkgdatadir,
  exclude_directories: ['__pycache__', 'backend/__pycache__', 'ui/__pycache__', 'backend/focus/__pycache__', 'tray/__pycache__'],
)

# Launcher -> /usr/bin/vibemapp
conf = configuration_data()
conf.set('PYTHON', python.full_path())
conf.set('PKGDATADIR', prefix / pkgdatadir)
conf.set('VERSION', meson.project_version())
configure_file(
  input: 'build-aux/vibemapp.in',
  output: 'vibemapp',
  configuration: conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

subdir('data')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
