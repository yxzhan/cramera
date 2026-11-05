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
| `?stage=<id>` | open that stage (the links the lobby's stages give) |
| `?lobby=<url>` | another copy of the lobby instead of the published one (a local `npm start`, a test build) |

Any other parameter goes to the lobby too. The lobby gets this page's address as
`?embed=`, so the links its Share panel gives (the QR code, the code for another
device, a stage's link) open this page with the shared world, not the lobby alone.

The two pages keep their parameters the same afterwards, through messages (the lobby's
`public/js/app/embed.js`):

| Message | From → to | |
| --- | --- | --- |
| `{type: 'robot-lobby:url', params}` | lobby → page | the lobby's parameters as it opens and whenever it changes them (joined a server, took a stage); the page's address follows without a new history entry, so a reload or a copied link opens the same world |
| `{type: 'robot-lobby:params', params}` | page → lobby | the page's parameters after back or forward; the lobby opens with them and answers with its own |

`params` is a list of `[name, value]` pairs without the page's `lobby` and the lobby's
`embed` and `online`. Each side takes messages only from the other's frame and origin
and sends only to that origin, since `server` carries its access token. A request sent
while the lobby is still opening is sent again when the lobby's first answer lacks it.

The lobby is a page of another origin in a frame: its browser storage is its own, and
WebXR in it needs the viewer itself served over HTTPS.
