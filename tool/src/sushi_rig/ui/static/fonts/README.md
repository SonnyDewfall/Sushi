# The page's three fonts (#139)

Bundled so the page renders in its own type with no network (the UX doc's
rule 6): `style.css` and `guide.css` load them from here with `@font-face`,
and the agent serves them under `/ui/fonts/`. Each is the upstream project's
own variable WOFF2, byte for byte, renamed only to drop the brackets from its
file name, with the project's OFL licence beside it. All three are under the
SIL Open Font License 1.1; none declares a Reserved Font Name.

Only the upright styles: nothing in `style.css` sets italic. The guide's
emphasis (`*word*`, an `<em>`) is the browser's slant of the upright, not a
true italic; each family's italic is a second file of about the same size. One
variable file a family covers every weight the CSS uses (Newsreader 500 to
700, Instrument Sans 400 to 600, Geist Mono 400 and 600), and is smaller than
those weights as static files would be. Not subset: a subset would be a
modified font, and the full files are 375 KB together, loaded once and kept
by the browser against their ETag.

| File | Family, version | From |
|---|---|---|
| `newsreader/Newsreader.woff2` | Newsreader 1.003 (Production Type); axes `opsz`, `wght` 200 to 800 | github.com/productiontype/Newsreader at commit `1ece6a8bfe5db1a2b90c76cc1fe5d3b2eed5dcf3` (the commit Google Fonts ships; `master`, `cfcb4f7`, has the same file on 2026-11-24): `fonts/variable/woff2/Newsreader[opsz,wght].woff2` |
| `newsreader/OFL.txt` | its licence | the same commit's `OFL.txt` |
| `instrument-sans/InstrumentSans.woff2` | Instrument Sans 1.000 (Rodrigo Fuenzalida, Jordan Egstad); axes `wdth` 75 to 100, `wght` 400 to 700 | github.com/Instrument/instrument-sans at commit `7fa22308a3d0c94ee2b3cd537a1196b65db34a3e` (`master` on 2026-11-24, and the commit Google Fonts ships): `fonts/webfonts/InstrumentSans[wdth,wght].woff2` |
| `instrument-sans/OFL.txt` | its licence | the same commit's `OFL.txt` |
| `geist-mono/GeistMono.woff2` | Geist Mono 1.700 (Vercel); axis `wght` 100 to 900 | github.com/vercel/geist-font, release tag `v1.9.2` (commit `ef2597cfc42f4797676df105517ccc0a1cfa6569`, its latest on 2026-11-24): `fonts/GeistMono/webfonts/GeistMono[wght].woff2` |
| `geist-mono/OFL.txt` | its licence | the same tag's `OFL.txt` |

Each was fetched from `https://raw.githubusercontent.com/<owner>/<repo>/<commit or tag>/<path>`
on 2026-11-24. The versions are the fonts' own `name` table (ID 5).

sha256:

    1faa3380ac0e87e057b180e03fd94bd708a612afb67d2590677be4508909fae9  newsreader/Newsreader.woff2
    fdfad38143ec470553cae82a1e45320bdd1b9ec70415d37bd0171051d8a4ded8  newsreader/OFL.txt
    aa72922aafcc0dc18f36ec1d805b0212057dabe8b9d5b8b57f67035aea1b826d  instrument-sans/InstrumentSans.woff2
    9e27a72ed30eb49a08678f6a5d6ed98ec7ba5368f541637ee0683ec9134ef966  instrument-sans/OFL.txt
    afaacc4c5fbba89d2ebf7a02dc4070208540874592a5504d57175782fe893101  geist-mono/GeistMono.woff2
    021caa749f89fecd7c2e40a50499de29e84d2bc19251659b47d499c2fbfffc17  geist-mono/OFL.txt

The checksums are held by `tests/test_ui_install.py`: a changed file fails the
suite until this note says what it is now.
