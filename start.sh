#!/bin/bash
# Jedna komanda za pokretanje: ./start.sh
#
# Provjerava je li Flask instaliran, je li grocery.db napravljena (ako nije,
# nudi da je napravi preko update.py), i onda pokrece web sucelje.

set -e
cd "$(dirname "$0")"

echo "Provjeravam Flask..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Flask nije instaliran. Instaliram..."
    pip install --break-system-packages flask
fi

if [ ! -f "grocery.db" ]; then
    echo
    echo "grocery.db ne postoji jos."
    read -p "Zelis li je sada napraviti (preuzima podatke, traje ~1 minutu)? [d/N] " odgovor
    if [[ "$odgovor" == "d" || "$odgovor" == "D" ]]; then
        python3 update.py
    else
        echo "Bez grocery.db aplikacija ne moze raditi. Pokreni 'python3 update.py' kasnije."
        exit 1
    fi
fi

echo
echo "Pokrecem grocery-finder na http://127.0.0.1:5000 ..."
echo "(za prekid pritisni Ctrl+C)"
echo

# otvori browser automatski, ako je moguce (ne prekida pokretanje ako ne uspije)
( sleep 1.5 && (xdg-open http://127.0.0.1:5000 2>/dev/null || open http://127.0.0.1:5000 2>/dev/null || true) ) &

python3 app.py --db grocery.db
