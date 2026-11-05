#!/bin/sh
# Публикация сайта: свежие файлы приложения из main (сбор мог стартовать до последней правки) и номер версии,
# по которому открытое приложение понимает, что пора перезагрузиться.
git fetch -q --depth 1 origin main && git checkout FETCH_HEAD -- docs/index.html docs/img docs/manifest.webmanifest 2>/dev/null || true
v=$(git rev-parse --short FETCH_HEAD 2>/dev/null || git rev-parse --short HEAD)
sed -i "s/__BUILD__/$v/" docs/index.html
printf %s "$v" > docs/version.txt
echo "Версия сайта: $v"
