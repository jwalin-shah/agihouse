# Even Realities G2 Build Notes

Source docs reviewed April 26, 2026:
- https://hub.evenrealities.com/docs/getting-started/overview
- https://hub.evenrealities.com/docs/getting-started/installation
- https://hub.evenrealities.com/docs/getting-started/first-app
- https://hub.evenrealities.com/docs/getting-started/architecture
- https://hub.evenrealities.com/docs/guides/page-lifecycle
- https://hub.evenrealities.com/docs/guides/input-events
- https://hub.evenrealities.com/docs/guides/display
- https://hub.evenrealities.com/docs/guides/device-apis
- https://hub.evenrealities.com/docs/guides/design-guidelines
- https://hub.evenrealities.com/docs/guides/networking
- https://hub.evenrealities.com/docs/guides/headless-testing
- https://hub.evenrealities.com/docs/reference/simulator
- https://hub.evenrealities.com/docs/reference/packaging
- https://hub.evenrealities.com/docs/reference/cli
- https://hub.evenrealities.com/docs/reference/app-submission

## Platform Model

- Apps are standard web apps running in the Even Realities phone app WebView.
- The phone app bridges app commands to the glasses over Bluetooth.
- App logic does not run on the glasses; the glasses render containers and emit input events.
- Current supported app type is plugins/background-layer apps.
- Privacy/device limits: no camera, no speaker, no direct Bluetooth access.

## Hardware Constraints

- Display: 576 x 288 px per eye.
- Color: 4-bit greyscale, rendered as green shades.
- Audio input: 16 kHz signed 16-bit little-endian PCM mono.
- Inputs: G2 temple touchpads and optional R1 ring, each supporting press, double press, swipe up, swipe down.

## Tooling

Use Node.js 20 LTS or 22+.

```bash
npm install @evenrealities/even_hub_sdk
npm install -g @evenrealities/evenhub-simulator
npm install -g @evenrealities/evenhub-cli
```

Useful commands:

```bash
evenhub-simulator http://localhost:5173
evenhub-simulator http://localhost:5173 --automation-port 9898
evenhub qr --url "http://<LAN-IP>:5173"
evenhub init
evenhub pack app.json dist -o myapp.ehpk
evenhub pack app.json dist -o myapp.ehpk -c
```

The `eh` binary is an alias for `evenhub`.

## SDK Startup Pattern

```ts
import {
  waitForEvenAppBridge,
  TextContainerProperty,
  CreateStartUpPageContainer,
} from '@evenrealities/even_hub_sdk'

const bridge = await waitForEvenAppBridge()

const mainText = new TextContainerProperty({
  xPosition: 0,
  yPosition: 0,
  width: 576,
  height: 288,
  borderWidth: 0,
  borderColor: 5,
  paddingLength: 4,
  containerID: 1,
  containerName: 'main',
  content: 'Hello from G2!',
  isEventCapture: 1,
})

await bridge.createStartUpPageContainer(
  new CreateStartUpPageContainer({
    containerTotalNum: 1,
    textObject: [mainText],
  }),
)
```

`createStartUpPageContainer` returns:
- `0`: success
- `1`: invalid parameters
- `2`: oversize
- `3`: out of memory

## Display System

- The glasses do not render HTML/CSS. The SDK sends positioned containers.
- Coordinates are absolute pixels, origin at top-left.
- Max 4 image containers and 8 other containers per page.
- Exactly one container should have `isEventCapture: 1`.
- Containers can overlap; later containers draw on top.
- Text/list containers support borders, border radius, and padding.
- There is no background fill, text alignment, font size control, bold, italic, or per-item list styling.
- Text content limits:
  - startup/rebuild: 1,000 chars
  - text upgrade: 2,000 chars
  - about 400-500 chars fill a full-screen text container
- Lists max out at 20 items, 64 chars per item.
- Images are 4-bit greyscale, 20-200 px wide and 20-100 px high.
- For image-first screens, put a full-screen event-capturing text container behind the image.

## Page Lifecycle

- Use `createStartUpPageContainer` once at startup.
- Use `textContainerUpgrade` for frequent text changes; it avoids hardware flicker.
- Use `rebuildPageContainer` when the layout changes.
- Use `updateImageRawData` for images; do not send images concurrently.
- Use `shutDownPageContainer(1)` for root-page double-tap exit confirmation.

## Input Handling

Event types:

- `CLICK_EVENT` / `0`: single press
- `SCROLL_TOP_EVENT` / `1`: swipe up or scroll top boundary
- `SCROLL_BOTTOM_EVENT` / `2`: swipe down or scroll bottom boundary
- `DOUBLE_CLICK_EVENT` / `3`: double press
- `FOREGROUND_ENTER_EVENT` / `4`: app foregrounded
- `FOREGROUND_EXIT_EVENT` / `5`: app backgrounded
- `ABNORMAL_EXIT_EVENT` / `6`: unexpected disconnect

Event routing depends on the event-capturing container:
- text container events arrive as `event.textEvent`
- list container events arrive as `event.listEvent`

Design around one active input target.

## Device APIs

- `audioControl(true | false)` starts/stops mic capture.
- Audio arrives as `audioEvent`.
- `imuControl(true, ImuReportPace.P500)` starts IMU stream.
- IMU data arrives through `event.sysEvent.imuData`.
- `getDeviceInfo()` returns model, serial, battery, wearing, charging, and in-case status.
- `onDeviceStatusChanged()` subscribes to battery/wearing/charging updates.
- `getUserInfo()` returns uid, name, avatar, and country.
- `setLocalStorage()` / `getLocalStorage()` persist setup state.

## Networking

- Production network calls must pass two gates:
  - destination origin appears in `app.json` network whitelist
  - remote API returns valid browser CORS headers
- Whitelist entries use full origins like `https://api.example.com`; no wildcards.
- HTTPS is required in production.
- If a third-party API does not support CORS, route through a server we control.

## Manifest

Current app manifest rules:

- `package_id`: reverse-domain, lowercase, no hyphens, at least two segments.
- `edition`: `"202601"`.
- `name`: 20 chars or fewer; must not contain "Even" unless officially approved.
- `version`: `x.y.z`.
- `min_app_version`: required, example `"2.0.0"`.
- `min_sdk_version`: current floor `"0.0.10"`.
- `entrypoint`: file inside built output, usually `index.html`.
- `permissions`: array of permission objects, can be `[]`.
- supported languages: `en`, `de`, `fr`, `es`, `it`, `zh`, `ja`, `ko`.

Permission names:
- `network`
- `location`
- `g2-microphone`
- `phone-microphone`
- `album`
- `camera`

## Simulator And QA

Simulator supports:
- glow rendering
- audio input device selection
- screenshots
- HTTP automation on `--automation-port`

Automation endpoints:
- `GET /api/ping`
- `GET /api/screenshot/glasses`
- `GET /api/screenshot/webview`
- `GET /api/console`
- `DELETE /api/console`
- `POST /api/input` with actions like `click`, `double_click`, `up`, `down`

Testing notes:
- Wait until the first event-capturing container exists before sending input.
- Treat any screenshot pixel with `alpha > 0` as lit.
- Simulator is good for layout and logic, but hardware can differ in font rendering, list scrolling, image constraints, and abnormal errors.

## Submission Checklist

- No black screen on first launch.
- If setup is needed, explain next step on glasses and persist setup in local storage.
- Works while phone is locked and Even app is backgrounded.
- Every gesture gives visible feedback.
- App remains alive after 2 minutes idle.
- Root double-tap shows system exit confirmation with `shutDownPageContainer(1)`.
- Cleanup lifecycle handlers are wired.
- Store screenshots come from simulator/hardware and match real output.
- Privacy policy covers all permissions.
- `evenhub pack app.json dist -o myapp.ehpk -c` passes.

## Hackathon Biases

- Prefer text-first or simple-icon apps over complex image UIs.
- Prefer one clear loop that works hands-free on glasses/ring input.
- Avoid third-party APIs unless CORS and whitelist behavior are known.
- Use local storage for setup and preferences.
- Use `textContainerUpgrade` for timers, live statuses, transcripts, and counters.
- Keep root double-tap exit behavior correct from the first implementation.
