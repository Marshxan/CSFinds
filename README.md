# CS Finds bot

Bot de Discord pentru postat produse de pe Taobao / Weidian / 1688 pe forumuri:
ia pretul, pozele si greutatea prin Kakobuy, scoate fundalul, pune logoul si
face postarea cu butoane pentru fiecare agent.

## Ce iti trebuie

- Python 3.12
- Node 18+ (pentru scriptele din `scraper/`, care randeaza paginile Kakobuy)
- un cont Kakobuy (pentru preturi si poze)

## 1. Tokenul

`.env` e deja completat, cu tokenul si codurile de afiliere - nu trebuie
configurat nimic. E acelasi bot si acelasi server, deci nu trebuie facut
unul nou - dar tokenul e parola botului: nu-l pune in git, nu-l trimite mai
departe si nu-l lipi in chat public. Daca scapa, se face **Reset Token** din
https://discord.com/developers/applications si se schimba in `.env`.

**Nu porniti botul in acelasi timp pe doua masini.** Discord permite o singura
sesiune per token: a doua se deconecteaza singura, si postarile ies duplicate.
Cand preiei gazduirea, Kevin il opreste pe al lui (`stop-bot.bat`).

## 2. Instalare

```
pip install -r requirements.txt
```

`.env` vine completat. Cheile, daca vrei sa le schimbi:

| Cheie | Ce e |
|---|---|
| `DISCORD_TOKEN` | tokenul de la pasul 1 |
| `FINDS_CATEGORY_ID` | id-ul categoriei cu forumurile de produse |
| `OWNER_ROLE_ID` | rolul care are voie sa dea comenzi |
| `TOOLS_CHANNEL_ID` | canalul unde arunci linkuri si botul raspunde cu butoane |
| `WELCOME_CHANNEL_ID` | canalul de bun venit (optional) |
| `AFF_*`, `DOPPEL_REF` | codurile tale de afiliere (lasa gol daca n-ai) |

Id-urile se iau cu click dreapta pe canal/rol, cu Developer Mode pornit in
Discord (Settings > Advanced).

## 3. Scraperul de Kakobuy

Scripturile din `scraper/` cer o sesiune logata, facuta o singura data:

```
cd scraper
npm install playwright
npx playwright install chromium
```

Sesiunea (`scraper/.kakobuy-session.json`) vine deja facuta, deci nu trebuie
login. Cand expira, preturile si pozele ies
goale - ruleaza `kakobuy-login.js` din nou.

Daca tii scraperul in alt folder, arata-i botului calea in `.env`:
`KAKOBUY_SCRIPT=` si `KAKOBUY_PRICE_SCRIPT=`.

## 4. Pornire

```
python bot.py
```

sau `start-bot.bat` pe Windows (`stop-bot.bat` il opreste).

Panoul web porneste odata cu botul pe http://127.0.0.1:8787 - se leaga doar pe
localhost si n-are login, deci nu-l expune pe internet. Tot ce face panoul se
poate face si din Discord: `.posts`, `.drafts`, `.bulk`, `.fixbg`.

## Comenzi

`.help` le arata pe toate, grupate. Cele mai folosite:

- `.add` - formularul de postat un produs
- `.bulk <linkuri>` - oricate linkuri deodata, in drafturi
- `.drafts` / `.posts` - coada de drafturi si ce e postat, cu editare
- `.edit <link postare>` - nume, pret, link, greutate, poza, categorie
- `.image <link>` - fundal scos, logo pus
- `.qc <link>` - pozele QC de pe pagina produsului
- `.fixbg` - reface pozele taiate prost (`all` = tot, cu modelul bun)

## Datele (si mutarea lor pe baza de date)

`posts.json`, `drafts.json` si `links.json` sunt in arhiva, cu datele reale -
ai de unde migra si de pe ce testa.

Tot cititul si scrisul trec prin trei module, deci baza de date se schimba
acolo si nimic altceva nu se atinge:

| Fisier | Ce tine | Functiile publice |
|---|---|---|
| `jsonstore.py` | citit/scris atomic + rotatie de backupuri | `read`, `write` |
| `poststore.py` | postarile si drafturile | `all_posts`, `get`, `save`, `remove`, `all_drafts`, `get_draft`, `save_draft`, `remove_draft` |
| `linkstore.py` | evidenta produselor vazute (poze, preturi, ce s-a postat) | `get`, `record`, `posted_before` |

`poststore` si `linkstore` folosesc `jsonstore` doar prin `read`/`write`, deci
cel mai ieftin drum e sa rescrii `jsonstore.py` peste tabelul tau si sa lasi
restul neatins. Cheile sunt: postare = `thread_id` (int, id-ul threadului de
Discord), draft = `id` (`platform-item_id`), link = `platform` + `item_id`.

Atentie la un lucru: `poststore.save` e apelat si din event loop-ul botului, si
din panoul web, in acelasi proces - pastreaza scrierea atomica sau pune un lock,
altfel se pierd randuri la doua salvari deodata.

## Ce e in arhiva

Tot ce trebuie ca sa mearga din prima: codul, datele (`posts.json`,
`drafts.json`, `links.json`), `.env` cu tokenul completat si sesiunea Kakobuy
a lui Kevin (`scraper/.kakobuy-session.json`), deci nu mai e nevoie de login.

Cele doua fisiere cu chei - `.env` si `scraper/.kakobuy-session.json` - sunt
credentialele lui Kevin. Nu ajung in git (`.gitignore` le sare deja) si nu se
dau mai departe. Cand sesiunea Kakobuy expira, se face din nou cu
`node scraper/kakobuy-login.js`.

Lipseste doar `bot.log`, care oricum se scrie de la zero.
