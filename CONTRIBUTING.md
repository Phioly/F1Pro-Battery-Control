# Contributing

Thanks for helping improve F1Pro EC Control.

## Local build

```bash
npm ci
npm run typecheck
npm run build
python3 package.py --check
```

`dist/index.js` is checked in — rebuild it (`npm run build`) whenever you change
`src/index.tsx`, otherwise the plugin loads with a blank UI.

## Tests

Both suites run offline, without a handheld and without root:

```bash
npm test                 # frontend display rules (187 assertions)
npm run test:backend     # backend / fake sysfs + fake EC (289 assertions)
npm run test:all         # both
```

`npm test` extracts real expressions from `src/index.tsx` and evaluates them, so a
display-rule regression and an "expression can no longer be extracted" failure are
both caught. Keep new assertions evaluating real inputs — a bare
"source contains this check" assertion is not meaningful and will silently pass even
when the implementation is broken.

## Translations

UI strings live in `src/i18n/` (frontend) and in `Plugin._MSG` (backend, `main.py`).
Both tables must stay in sync — the same key set, the same `{placeholder}` names.
`tests/test_display.mjs` and `test_i18n` in `tests/test_backend.py` enforce this, so a
missing or mistyped key fails the build rather than showing a raw key in the UI.

Two rules that are easy to get wrong:

- **No user-facing string may be hardcoded.** Everything goes through `t(...)` (or
  `self._t(...)` / `cls._t_static(...)` in `main.py`). Log messages are the only
  exception and stay in Chinese for the developer console.
- **Traditional Chinese follows Steam.** Steam reports `schinese` / `tchinese`, not
  `zh-CN`; both must map to Chinese. Anything else falls back to English.

`_lang` is a **class attribute** on purpose: `_normalize_fan_curve` is a `@classmethod`
and can only read `cls._lang`. Assigning `self._lang` in `set_locale()` would create an
instance attribute that shadows it, and Chinese users would keep getting English curve
errors. Write `Plugin._lang = ...`.

## Docs

`README.md` (Chinese) and `README.en.md` (English) must stay in sync: same sections,
same anchors linking to each other, and both must carry the current version and zip name.
`python package.py --check` enforces the version/zip/link trio. For the `](#anchor)`
references themselves, resolve them the way GitHub does — strip backticks and asterisks
but **keep underscores**, lowercase, drop punctuation, and turn spaces into hyphens —
and check every reference in **both** files. Don't do it with an inline `python -c`: the
backticks in the markdown collide with bash quoting, so use a script file or a heredoc.

## Packaging

```bash
python package.py          # validate + build the zip (written to the parent directory)
python package.py --check  # validate only
```

## Scope

Please keep changes focused on the Linux `power_supply` / `oxp_ec` interfaces and the
Decky integration, and avoid adding dependencies that are not needed at runtime.

The project is currently tested against the OneXFly F1 Pro on SteamOS. Do not
generalize device support claims without testing the corresponding kernel /
`power_supply` interface.
