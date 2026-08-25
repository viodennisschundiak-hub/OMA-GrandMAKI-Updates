# OMA GrandMAKI-BOT – signierter Updatekanal

Dieses öffentliche Repository dient ausschließlich als HTTPS-Downloadkanal für signierte OMA-Updates.

## Sicherheitsregeln

- Bedienung, Moduswahl, MA2-Aufbau und Updates erfolgen ausschließlich im OMA Launcher.
- Das Browser-Kontrollfenster bleibt strikt read-only.
- Während einer laufenden OMA-Session sind Änderungen und Updates gesperrt. Vorher muss OMA gestoppt werden.
- Jedes Update wird vor der Installation mit SHA-256 und Ed25519 geprüft.
- Bridge und Launcher werden als koordiniertes Paket aktualisiert.
- Fehlerhafte Installationen werden automatisch zurückgerollt.
- Der private Signierschlüssel darf niemals in dieses Repository, in den Launcher, in Diagnose-ZIPs oder in den Chat gelangen.
- Keine Showfiles, Zugangsdaten, Logs oder sonstigen Geheimnisse hier hochladen.

## Öffentliche Kanalstruktur

- `stable/channel.json` – signierte Kanalbeschreibung
- `stable/OMA_GrandMAKI_Update_<version>.zip` – signiertes Update-Bundle
- `public/trusted_update_keys.json` – ausschließlich öffentliche Prüfschlüssel

Dateien unter `stable/` werden nur vom kontrollierten Veröffentlichungsprozess erzeugt. Manuelle Änderungen können die Signaturprüfung absichtlich fehlschlagen lassen.

## Laufzeit

GitHub ersetzt die MA-Bridge nicht. Die Bridge, grandMA2 onPC/Telnet und `@grandMA2 AI Bridge` laufen weiterhin lokal. GitHub wird ausschließlich bei einer Updateprüfung kontaktiert.
