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
         "$HOME/Downloads/albiondata-client/albiondata-client" "$HOME/Downloads/albiondata-client-amd64-mac/albiondata-client" \
         "$HOME/Applications/albiondata-client/albiondata-client" "/Applications/albiondata-client/albiondata-client"; do
  if [ -f "$c" ]; then CLIENT="$c"; break; fi
done
if [ -z "$CLIENT" ] && [ -f "$DIR/client-pad.txt" ]; then CLIENT="$(cat "$DIR/client-pad.txt")"; fi
if [ -z "$CLIENT" ] || [ ! -f "$CLIENT" ]; then
  echo ""
  echo "De Albion Data Client is niet gevonden."
  echo "Sleep het bestand 'albiondata-client' (uit de map van de client) in dit venster en druk op Enter:"
  read -r CLIENT
  CLIENT="${CLIENT%\"}"; CLIENT="${CLIENT#\"}"; CLIENT="${CLIENT%\'}"; CLIENT="${CLIENT#\'}"; CLIENT="${CLIENT% }"
  CLIENT="${CLIENT//\\ / }"
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

# 3. Client starten die ALLEEN naar je eigen Mac stuurt (vraagt om je wachtwoord: nodig om netwerkverkeer te lezen)
echo ""
echo "De Albion Data Client start nu en stuurt alleen naar je eigen Mac (http://127.0.0.1:47800)."
echo "Hij vraagt om je Mac-wachtwoord (je ziet niets als je typt)."
echo "Laat dit venster open terwijl je speelt; sluit het als je klaar bent."
echo "Zet op de site bij \"Eigen data (prive)\" het vinkje aan."
echo ""
sudo "$CLIENT" -i http://127.0.0.1:47800
