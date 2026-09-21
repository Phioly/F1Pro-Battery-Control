# Contributing

Thanks for helping improve F1 Pro Battery Control.

## Local build

```bash
npm ci
npm run typecheck
npm run build
python3 package.py --check
```

Please keep changes focused on the Linux `power_supply` / Decky integration and avoid
adding dependencies that are not needed at runtime.

The project is currently tested against the OneXFly F1 Pro on SteamOS. Do not generalize
device support claims without testing the corresponding kernel / power_supply interface.
