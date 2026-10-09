fs = import('fs')

install_data('io.github.albertflindt.KeyMappForLinux.desktop',
  install_dir: datadir / 'applications',
)

install_data('io.github.albertflindt.KeyMappForLinux.metainfo.xml',
  install_dir: datadir / 'metainfo',
)

install_data('io.github.albertflindt.KeyMappForLinux.gschema.xml',
  install_dir: datadir / 'glib-2.0' / 'schemas',
)

install_subdir('icons/hicolor',
  install_dir: datadir / 'icons',
)

desktop_validate = find_program('desktop-file-validate', required: false)
if desktop_validate.found()
  test('Validate desktop file', desktop_validate,
    args: [files('io.github.albertflindt.KeyMappForLinux.desktop')])
endif

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('Validate metainfo', appstreamcli,
    args: ['validate', '--no-net', '--explain',
           files('io.github.albertflindt.KeyMappForLinux.metainfo.xml')])
endif

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test('Validate schema', compile_schemas,
    args: ['--strict', '--dry-run', meson.current_source_dir()])
endif

# udev rules are owned by the backend (data/udev/).
if fs.exists('udev/meson.build')
  subdir('udev')
elif fs.exists('udev/50-zsa.rules')
  install_data('udev/50-zsa.rules',
    install_dir: get_option('prefix') / 'lib' / 'udev' / 'rules.d',
  )
endif
