"""Journal local (SQLite) : envois (anti-doublon) et collectes."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class Store:
    def __init__(self, chemin: str):
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(chemin)
        self.cx.executescript("""
            CREATE TABLE IF NOT EXISTS envoi (cle TEXT PRIMARY KEY, envoye_le TEXT, resend_id TEXT, sujet TEXT);
            CREATE TABLE IF NOT EXISTS collecte (le TEXT, mode TEXT, pieces INTEGER, evenements INTEGER);
        """)

    def deja_envoye(self, cle: str, apres: datetime | None = None) -> bool:
        """Envoi déjà fait pour cette clé ; avec `apres`, seulement s'il a eu lieu à partir de cette heure
        (un envoi manuel du matin ne bloque pas le créneau automatique du soir)."""
        r = self.cx.execute("SELECT envoye_le FROM envoi WHERE cle=?", (cle,)).fetchone()
        if r is None:
            return False
        return apres is None or datetime.fromisoformat(r[0]) >= apres

    def noter_envoi(self, cle, resend_id, sujet):
        self.cx.execute("INSERT OR REPLACE INTO envoi VALUES (?,?,?,?)", (cle, datetime.now().isoformat(sep=" "), resend_id, sujet))
        self.cx.commit()

    def noter_collecte(self, mode, n_pieces, n_evts):
        self.cx.execute("INSERT INTO collecte VALUES (?,?,?,?)", (datetime.now().isoformat(sep=" "), mode, n_pieces, n_evts))
        self.cx.commit()
