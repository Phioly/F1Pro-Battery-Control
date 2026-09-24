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
npm test                 # frontend display rules (166 assertions)
npm run test:backend     # backend / fake sysfs + fake EC (273 assertions)
npm run test:all         # both
```

`npm test` extracts real expressions from `src/index.tsx` and evaluates them, so a
display-rule regression and an "expression can no longer be extracted" failure are
both caught. Keep new assertions evaluating real inputs — a bare
"source contains this check" assertion is not meaningful and will silently pass even
when the implementation is broken.

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
