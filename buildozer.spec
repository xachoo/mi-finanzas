# Este archivo de configuración .spec le indica a Buildozer los requisitos para compilar la aplicación.
# Sigue principalmente la sintaxis de un archivo .ini.

[app]

# (str) Título de tu aplicación
title = Finanzas

# (str) Nombre del paquete (sin espacios ni caracteres especiales)
package.name = finanzas

# (str) Dominio del paquete (necesario para el empaquetado de Android/iOS)
package.domain = com.finanzas

# (str) Código fuente donde reside el archivo main.py
source.dir = .

# (list) Extensiones de archivos fuente a incluir (añade json, csv, kv, etc., según lo que use tu app)
source.include_exts = py,png,jpg,kv,atlas,json

# (list) Lista de inclusiones usando patrones de coincidencia
#source.include_patterns = assets/*,images/*.png

# (list) Extensiones de archivos fuente a excluir
#source.exclude_exts = spec

# (list) Lista de directorios a excluir
#source.exclude_dirs = tests, bin, venv

# (str) Versionado de la aplicación
version = 0.1

# (list) Requisitos de la aplicación
# Si tu app de Finanzas usa bases de datos o librerías externas (ej. sqlite3, requests), agrégalas aquí separadas por comas.
requirements = python3,kivy

# (str) Pantalla de carga (presplash) de la aplicación
#presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icono de la aplicación
#icon.filename = %(source.dir)s/data/icon.png

# (list) Orientaciones soportadas (landscape, portrait, etc.)
orientation = portrait

#
# Específico para OSX
#

osx.kivy_version = 2.2.0

#
# Específico para Android
#

# (bool) Indica si la aplicación debe ejecutarse en pantalla completa o no
fullscreen = 0

# (list) Permisos requeridos por la aplicación (agrega INTERNET u otros si tu app consume APIs/servicios)
android.permissions = INTERNET

# (int) API de Android objetivo (Target Android API)
android.api = 33

# (int) API mínima soportada por tu APK / AAB
android.minapi = 24

# (bool) Acepta automáticamente la licencia del SDK (necesario para GitHub Actions / main.yml)
android.accept_sdk_license = True

# (bool) Habilita el soporte para AndroidX
android.enable_androidx = True

# (list) Arquitecturas Android para las que se va a compilar
android.archs = arm64-v8a, armeabi-v7a

# (bool) Habilita la función de copia de seguridad automática de Android
android.allow_backup = True

# (str) Formato utilizado para empaquetar la app en modo release (aab)
android.release_artifact = aab

# (str) Formato utilizado para empaquetar la app en modo debug (apk)
android.debug_artifact = apk

#
# Específico para Python for Android (p4a)
#

# (str) Rama de python-for-android a utilizar (coincide con la configuración de tu main.yml)
p4a.branch = develop

#
# Específico para iOS
#

ios.kivy_ios_url = https://github.com/kivy/kivy-ios
ios.kivy_ios_branch = master
ios.ios_deploy_url = https://github.com/phonegap/ios-deploy
ios.ios_deploy_branch = 1.12.2
ios.codesign.allowed = false

[buildozer]

# (int) Nivel de registro (2 = debug/salida detallada de comandos)
log_level = 2

# (int) Muestra una advertencia si Buildozer se ejecuta como usuario root
warn_on_root = 1
