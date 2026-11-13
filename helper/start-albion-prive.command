#!/bin/bash
# Albion Tools - prive data (Mac)
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
# macOS zet downloads in "quarantaine"; haal die alleen van deze map af
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null

if [ "$(uname -m)" = "arm64" ]; then HELPER="$DIR/albion-helper-mac-arm64"; else HELPER="$DIR/albion-helper-mac-intel"; fi
chmod +x "$HELPER"

# 1. Albion Data Client zoeken
CLIENT=""
for c in "$DIR/albiondata-client" "$DIR/albiondata-client/albiondata-client" \
         "/Applications/Albion Data Client.app/Contents/MacOS/albiondata-client" "$HOME/Applications/Albion Data Client.app/Contents/MacOS/albiondata-client" \
         "$HOME/Downloads/albiondata-client/albiondata-client" "$HOME/Downloads/albiondata-client-amd64-mac/albiondata-client" \
         "$HOME/Applications/albiondata-client/albiondata-client" "/Applications/albiondata-client/albiondata-client"; do
  if [ -f "$c" ]; then CLIENT="$c"; break; fi
done
if [ -z "$CLIENT" ] && [ -f "$DIR/client-pad.txt" ]; then CLIENT="$(cat "$DIR/client-pad.txt")"; fi
if [ -z "$CLIENT" ] || [ ! -f "$CLIENT" ]; then
  echo ""
  echo "De Albion Data Client is niet gevonden."
  echo "Sleep het bestand 'albiondata-client' (uit de map van de client) in dit venster en druk op Enter."
  echo "Heb je een app (Albion Data Client.app)? Sleep dan de app erin."
  read -r CLIENT
  CLIENT="${CLIENT%\"}"; CLIENT="${CLIENT#\"}"; CLIENT="${CLIENT%\'}"; CLIENT="${CLIENT#\'}"; CLIENT="${CLIENT% }"
  CLIENT="${CLIENT//\\ / }"
  if [ -d "$CLIENT" ] && [ -d "$CLIENT/Contents/MacOS" ]; then CLIENT="$(ls "$CLIENT/Contents/MacOS/"* | head -1)"; fi
  if [ ! -f "$CLIENT" ]; then echo "Niet gevonden: $CLIENT"; read -r -p "Druk op Enter om te sluiten."; exit 1; fi
  echo "$CLIENT" > "$DIR/client-pad.txt"
fi
xattr -d com.apple.quarantine "$CLIENT" 2>/dev/null
chmod +x "$CLIENT" 2>/dev/null

# 2. Hulpprogramma starten (ontvangt de data op je eigen Mac)
"$HELPER" -data "$DIR/albion-helper-data.json" &
HPID=$!
trap 'kill $HPID 2>/dev/null' EXIT
sleep 1

# 3. Toestemming om netwerkverkeer te lezen: eerst 64 bpf-apparaten aanmaken (macOS maakt ze anders pas later,
#    met standaardrechten), dan lezen+schrijven voor beheerders, zoals Wireshark doet. Geldt tot de volgende herstart.
echo ""
echo "Typ je Mac-wachtwoord (je ziet niets als je typt). Dat is nodig zodat de client het verkeer van Albion mag lezen."
if ! sudo sh -c 'for i in $(seq 0 63); do : < /dev/bpf$i; done 2>/dev/null; chgrp admin /dev/bpf* && chmod g+rw /dev/bpf*'; then
  echo "Geen toestemming gekregen. Probeer het opnieuw."; read -r -p "Druk op Enter om te sluiten."; exit 1
fi

# 4. Client starten die ALLEEN naar je eigen Mac stuurt
echo ""
echo "De Albion Data Client start nu en stuurt alleen naar je eigen Mac (http://127.0.0.1:47800)."
echo "Start de client dus NIET zelf, alleen via dit script."
echo "Laat dit venster open terwijl je speelt; sluit het als je klaar bent."
echo "Zet op de site bij \"Eigen data (prive)\" het vinkje aan."
echo ""
"$CLIENT" -i http://127.0.0.1:47800
