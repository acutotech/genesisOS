# Genesis OS website

The product page for Genesis OS: one static page, no build step, no external
requests (fonts and images are served from this folder).

```
docs/
  index.html                 the page
  404.html                   "not found" page for GitHub Pages
  assets/css/site.css        styles
  assets/js/aurora.js        hero background (WebGL aurora, constellation lines)
  assets/js/desktop.js       the interactive desktop
  assets/js/site.js          scroll effects, Imager and terminal animations, pin header, settings tabs,
                             filter and search of the feature list
  assets/img/                logo, wallpapers (copied from stage-custom/10-branding), social card
  assets/img/sensors/        sensor box icons A to K (copied from genesis_shell/icons/sensors)
  assets/fonts/              Inter and JetBrains Mono (SIL Open Font License, texts included)
  .nojekyll                  tells GitHub Pages to serve the files as they are
```

## Preview on your computer

```sh
python3 -m http.server 8000 --directory docs
# then open http://localhost:8000
```

Opening `docs/index.html` directly from the file manager works too.

## Put it online with GitHub Pages

1. On GitHub: *Settings → Pages*.
2. *Build and deployment → Source*: **Deploy from a branch**.
3. *Branch*: the branch that holds this folder (`main` once it is merged), folder **/docs**. *Save*.
4. After a minute the page is at `https://acutotech.github.io/genesisOS/`.

Pages serves a repository's site publicly, also from a private repository on
plans that allow it, so check the page says only what you want public.

## Keeping it true

The page states facts about the image. When one of these changes, change the
page with it:

| On the page | Where it comes from |
| --- | --- |
| Python versions | `PYTHON_PRIMARY`, `PYTHON_LATEST` in `stage-custom/custom.conf` |
| Pinned apps, panel position, accent, glass opacity, blank timeout | `stage-custom/custom.conf`, `genesis_shell/config.py` |
| Key bindings | `stage-custom/11-genesis-shell/files/labwc/rc.xml` |
| Control center, power dialog, launcher labels | `genesis_shell/control_center.py`, `power.py`, `launcher.py` |
| Genesis Settings pages and rows | `stage-custom/12-genesis-settings/files/genesis_settings.py` |
| Sensor box addresses, scan interval | `genesis_shell/services/sensors.py`, `config.py` |
| Imager fields, hash, platforms | `imager/genesis_imager.py`, `.github/workflows/build-image.yml` |
| Test counts in "Built like a product" | `make test` (stage dry-run, first boot, shell logic, Acu Connect, Imager) |
| Interfaces on by default | `ENABLE_*` in `stage-custom/custom.conf` |
| Accent colours in the demo | `ACCENTS` in `stage-custom/12-genesis-settings/files/genesis_settings.py` |
| Calendar, toasts, sensor card, power dialog in the demo | `genesis_shell/notifications.py`, `sensors.py`, `power.py`, `shell.css` |
| What the remote client sends | `stage-custom/16-acu-connect/files/acu-connect.default` and the README section on Acu Connect |

## The feature list

"Everything in the image" is a plain list in `index.html`: one `<section class="idx-group">`
per area, one `<li><b>name</b><span>one line</span></li>` per entry. Add, change or
remove a line there; the counts on the filter buttons are written in the HTML, so
change those with it (the script counts the rest). The last group, "Good to know",
lists what the image does not do yet. Keep it honest: remove a line only when the
limit is really gone.

The desktop on the page is a recreation in HTML drawn from the shell's own
colours, sizes and labels; it is labelled as such. The wallpapers, the logo and
the sensor icons are the real ones. It shows what a default image shows: no
Night Light tile (the image does not install `wlsunset`), weeks starting on
Sunday (the image's `en_US` locale).
