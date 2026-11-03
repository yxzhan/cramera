# cramera-shared-sim

A **Shared Sim** tab for the cramera viewer (`/plugins/shared-sim/shared-sim.html`): it
shows [Robot Lobby](https://yxzhan.github.io/robot-lobby/), a MuJoCo world in the
browser shared by everyone connected, under the viewer's top bar.

The lobby is managed and published on its own
([yxzhan/robot-lobby](https://github.com/yxzhan/robot-lobby)); the page embeds
https://yxzhan.github.io/robot-lobby/ in a frame. The plugin has browser files only and
no routes, so it works the same on a running viewer and on the static site.

As it opens, the page has the lobby start its shared world on Binder (the lobby's
`?online`: *Start shared world* without the click), or rejoin the one this browser
joined last while it still answers.

```bash
pip install ./packages/plugins/shared-sim
cramera                                    # the Shared Sim tab is in the top bar
cramera-export-site site --scenes ... --plugin shared-sim
```

## Page parameters

| | |
| --- | --- |
| `?server=<url>` | join that shared world (a lobby server, `npm run server`) instead of starting one |
| `?local` | the physics in the page, no shared world |
| `?lobby=<url>` | another copy of the lobby instead of the published one (a local `npm start`, a test build) |

The lobby is a page of another origin in a frame: its browser storage is its own, and
WebXR in it needs the viewer itself served over HTTPS.
